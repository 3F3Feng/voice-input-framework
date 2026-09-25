import UIKit

/// 语音键盘。自己不录音(iOS 不给键盘麦克风),只是 [DictationController] 的遥控器:
///
/// - 点麦克风:应用的会话开着 → 发「开始」命令;没开 → 拉起应用(`voiceinput://dictate?id=…`),
///   应用在前台开会话、马上开始录,用户说完回到这里;
/// - 录音中再点一下 → 发「结束」;✕ → 发「放弃」;
/// - 应用把结果写进共享容器,这里读到属于自己的那条就插进输入框。
///
/// 键盘扩展随时会被系统销毁重建(切到应用再回来时几乎一定会),所以「在等哪一条」
/// 记在共享容器里(KeyboardState),而不是这个对象上。
final class KeyboardViewController: UIInputViewController {
    private let statusLabel = UILabel()
    private let micButton = MicButton()
    private let globeKey = KeyboardViewController.makeKey("🌐")
    private let actionKey = KeyboardViewController.makeKey("✕")
    private let spaceKey = KeyboardViewController.makeKey(L.t("空格", "space"))
    private let deleteKey = KeyboardViewController.makeKey("⌫")
    private let returnKey = KeyboardViewController.makeKey("⏎")

    private var pollTimer: Timer?
    private var deleteTimer: Timer?
    private var stateObserver: DarwinObserver?
    private var appState = AppState()
    /// 本地的提示(没开完全访问、刚放弃……),优先于应用给的状态显示。
    private var localStatus: String?

    /// 结果出来多久以内算「刚出的」,可以自动插入。
    private static let freshResultWindow: Double = 120

    override func viewDidLoad() {
        super.viewDidLoad()
        buildUI()
    }

    override func viewWillAppear(_ animated: Bool) {
        super.viewWillAppear(animated)
        localStatus = nil
        stateObserver = DarwinObserver(.state) { [weak self] in self?.refresh() }
        // Darwin 通知偶尔会丢,音量条也要刷新:再加一个轮询兜底。
        pollTimer = Timer.scheduledTimer(withTimeInterval: 0.15, repeats: true) { [weak self] _ in
            self?.refresh()
        }
        refresh()
    }

    override func viewWillDisappear(_ animated: Bool) {
        super.viewWillDisappear(animated)
        pollTimer?.invalidate()
        pollTimer = nil
        stateObserver = nil
        stopDeleteRepeat()
    }

    override func viewWillLayoutSubviews() {
        super.viewWillLayoutSubviews()
        globeKey.isHidden = !needsInputModeSwitchKey
    }

    override func textDidChange(_ textInput: UITextInput?) {
        super.textDidChange(textInput)
        returnKey.setTitle(returnLabel(), for: .normal)
    }

    // MARK: 界面

    private func buildUI() {
        let height = view.heightAnchor.constraint(equalToConstant: 250)
        height.priority = .init(999)
        height.isActive = true

        statusLabel.font = .preferredFont(forTextStyle: .footnote)
        statusLabel.textColor = .secondaryLabel
        statusLabel.numberOfLines = 2
        statusLabel.textAlignment = .center

        micButton.addTarget(self, action: #selector(micTapped), for: .touchUpInside)
        micButton.accessibilityLabel = L.t("语音输入", "Dictate")

        // 点一下切到下一个键盘,按住弹出键盘列表:系统推荐的写法。
        globeKey.addTarget(self, action: #selector(handleInputModeList(from:with:)), for: .allTouchEvents)
        globeKey.accessibilityLabel = L.t("切换键盘", "Next keyboard")
        actionKey.addTarget(self, action: #selector(actionTapped), for: .touchUpInside)
        spaceKey.addTarget(self, action: #selector(spaceTapped), for: .touchUpInside)
        deleteKey.addTarget(self, action: #selector(deleteDown), for: .touchDown)
        deleteKey.addTarget(self, action: #selector(stopDeleteRepeat), for: [.touchUpInside, .touchUpOutside, .touchCancel])
        deleteKey.accessibilityLabel = L.t("删除", "Delete")
        returnKey.addTarget(self, action: #selector(returnTapped), for: .touchUpInside)
        returnKey.setTitle(returnLabel(), for: .normal)

        let row = UIStackView(arrangedSubviews: [globeKey, actionKey, spaceKey, deleteKey, returnKey])
        row.spacing = 6
        row.distribution = .fill
        for key in [globeKey, actionKey, deleteKey] {
            key.widthAnchor.constraint(equalToConstant: 48).isActive = true
        }
        returnKey.widthAnchor.constraint(equalToConstant: 72).isActive = true

        let stack = UIStackView(arrangedSubviews: [statusLabel, micButton, row])
        stack.axis = .vertical
        stack.alignment = .fill
        stack.spacing = 8
        stack.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 8),
            stack.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -8),
            stack.topAnchor.constraint(equalTo: view.topAnchor, constant: 8),
            stack.bottomAnchor.constraint(equalTo: view.bottomAnchor, constant: -6),
            micButton.heightAnchor.constraint(equalToConstant: 130),
            row.heightAnchor.constraint(equalToConstant: 44),
        ])
    }

    private static func makeKey(_ title: String) -> UIButton {
        let b = UIButton(type: .system)
        b.setTitle(title, for: .normal)
        b.titleLabel?.font = .systemFont(ofSize: 18)
        b.setTitleColor(.label, for: .normal)
        b.backgroundColor = UIColor { $0.userInterfaceStyle == .dark ? UIColor(white: 0.35, alpha: 1) : .white }
        b.layer.cornerRadius = 6
        b.layer.shadowColor = UIColor.black.cgColor
        b.layer.shadowOpacity = 0.25
        b.layer.shadowOffset = CGSize(width: 0, height: 1)
        b.layer.shadowRadius = 0
        return b
    }

    // MARK: 状态

    private func refresh() {
        guard hasFullAccess, SharedStore.container != nil else {
            micButton.mode = .idle
            statusLabel.text = L.t(
                "请在 设置 → 通用 → 键盘 → 键盘 → 语音输入 里打开「允许完全访问」",
                "Turn on Allow Full Access in Settings → General → Keyboard → Keyboards → Voice Input"
            )
            actionKey.isEnabled = false
            return
        }
        appState = SharedStore.readAppState()
        var ks = SharedStore.readKeyboardState()
        let mine = appState.requestId != nil && appState.requestId == ks.pendingId

        // 属于自己、还没插过、刚出的结果:插进去。
        if let id = appState.resultId, id == ks.pendingId, id != ks.consumedResultId {
            ks.consumedResultId = id
            let fresh = Date().timeIntervalSince1970 - (appState.resultAt ?? 0) < Self.freshResultWindow
            if let text = appState.resultText, fresh {
                insert(text)
                ks.lastInserted = text
                localStatus = appState.resultNote.map { L.t("文字整理没做成,插入的是原文:", "Polishing failed, inserted the raw text: ") + $0 }
            } else if let error = appState.resultError {
                localStatus = error
            }
            SharedStore.writeKeyboardState(ks)
        }

        switch appState.phase {
        case .recording where mine:
            micButton.mode = .recording
            micButton.level = CGFloat(appState.level)
        case .processing where mine:
            micButton.mode = .processing
        default:
            micButton.mode = .idle
        }
        let busy = mine && appState.phase != .idle
        actionKey.setTitle(busy ? "✕" : "↺", for: .normal)
        actionKey.accessibilityLabel = busy ? L.t("放弃这段录音", "Discard") : L.t("再插入一次上一条结果", "Insert the last result again")
        actionKey.isEnabled = busy || ks.lastInserted != nil

        if busy {
            statusLabel.text = appState.status
        } else if let local = localStatus {
            statusLabel.text = local
        } else if appState.phase != .idle {
            statusLabel.text = L.t("应用里正在听写…", "Dictating in the app…")
        } else if appState.sessionAlive {
            statusLabel.text = L.t("点麦克风开始说话", "Tap the mic to talk")
        } else {
            statusLabel.text = L.t("点麦克风:会先打开应用开始会话,再回到这里", "Tap the mic: the app opens to start a session, then come back here")
        }
    }

    // MARK: 按键

    @objc private func micTapped() {
        guard hasFullAccess, SharedStore.container != nil else { return refresh() }
        localStatus = nil
        let st = SharedStore.readAppState()
        var ks = SharedStore.readKeyboardState()
        switch st.phase {
        case .recording:
            if let id = st.requestId, id == ks.pendingId {
                send(.stop, id)
            }
        case .processing:
            break
        case .idle:
            let id = UUID().uuidString
            ks.pendingId = id
            SharedStore.writeKeyboardState(ks)
            if st.sessionAlive {
                send(.start, id)
            } else if let url = URL(string: "voiceinput://dictate?id=\(id)") {
                openContainingApp(url)
            }
        }
        refresh()
    }

    @objc private func actionTapped() {
        let st = SharedStore.readAppState()
        let ks = SharedStore.readKeyboardState()
        if let id = st.requestId, id == ks.pendingId, st.phase != .idle {
            send(.cancel, id)
            localStatus = L.t("已放弃", "Discarded")
        } else if let text = ks.lastInserted {
            insert(text)
        }
        refresh()
    }

    @objc private func spaceTapped() {
        textDocumentProxy.insertText(" ")
    }

    @objc private func deleteDown() {
        textDocumentProxy.deleteBackward()
        deleteTimer?.invalidate()
        // 按住连删:停 0.4 秒,之后每 0.06 秒一个。
        deleteTimer = Timer.scheduledTimer(withTimeInterval: 0.4, repeats: false) { [weak self] _ in
            self?.deleteTimer = Timer.scheduledTimer(withTimeInterval: 0.06, repeats: true) { [weak self] _ in
                self?.textDocumentProxy.deleteBackward()
            }
        }
    }

    @objc private func stopDeleteRepeat() {
        deleteTimer?.invalidate()
        deleteTimer = nil
    }

    @objc private func returnTapped() {
        textDocumentProxy.insertText("\n")
    }

    private func returnLabel() -> String {
        switch textDocumentProxy.returnKeyType ?? .default {
        case .go: return L.t("前往", "Go")
        case .search, .google, .yahoo: return L.t("搜索", "Search")
        case .send: return L.t("发送", "Send")
        case .next: return L.t("下一项", "Next")
        case .done: return L.t("完成", "Done")
        default: return "⏎"
        }
    }

    // MARK: 和应用通信

    private func send(_ action: Command.Action, _ id: String) {
        SharedStore.writeCommand(Command(action: action, requestId: id))
        DarwinNote.command.post()
    }

    /// 插入文字。前面紧挨着英文字母 / 数字 / 句末标点、插入的又以英文开头时补一个空格;中文之间不加。
    private func insert(_ text: String) {
        let prev = textDocumentProxy.documentContextBeforeInput?.last
        let needsSpace: Bool = {
            guard let prev, let first = text.first else { return false }
            let prevOk = (prev.isASCII && (prev.isLetter || prev.isNumber)) || ".,!?;:".contains(prev)
            return prevOk && first.isASCII && (first.isLetter || first.isNumber)
        }()
        textDocumentProxy.insertText(needsSpace ? " " + text : text)
    }

    /// 从键盘拉起本应用。`UIApplication.shared` 和 `open(_:)` 在扩展里不可用,只能顺着
    /// 响应链找到 UIApplication,再按 selector 调。Typeless、Wispr Flow 等语音键盘都靠这个;
    /// iOS 18 起老的 `openURL:` 不再生效,要用带 options 和回调的那个。
    private func openContainingApp(_ url: URL) {
        let selector = NSSelectorFromString("openURL:options:completionHandler:")
        var responder: UIResponder? = self
        while let r = responder {
            if let app = r as? UIApplication, app.responds(to: selector) {
                typealias OpenURL = @convention(c) (AnyObject, Selector, NSURL, NSDictionary, AnyObject?) -> Void
                let open = unsafeBitCast(app.method(for: selector), to: OpenURL.self)
                open(app, selector, url as NSURL, NSDictionary(), nil)
                return
            }
            responder = r.next
        }
        localStatus = L.t("打不开应用,请手动打开「语音输入」开始会话", "Couldn't open the app. Open Voice Input manually to start a session.")
    }
}

/// 圆形麦克风键;录音时外面一圈随音量变大。
final class MicButton: UIControl {
    enum Mode { case idle, recording, processing }

    var mode: Mode = .idle { didSet { if mode != oldValue { update() } } }
    var level: CGFloat = 0 {
        didSet {
            let scale = 1 + 0.5 * max(level, 0)
            ring.transform = CATransform3DMakeScale(scale, scale, 1)
        }
    }

    private let circle = CAShapeLayer()
    private let ring = CAShapeLayer()
    private let icon = UIImageView()

    override init(frame: CGRect) {
        super.init(frame: frame)
        layer.addSublayer(ring)
        layer.addSublayer(circle)
        icon.tintColor = .white
        icon.contentMode = .scaleAspectFit
        icon.isUserInteractionEnabled = false
        addSubview(icon)
        update()
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) is not used") }

    override func layoutSubviews() {
        super.layoutSubviews()
        let d = min(bounds.width, bounds.height) * 0.62
        let rect = CGRect(x: bounds.midX - d / 2, y: bounds.midY - d / 2, width: d, height: d)
        for l in [circle, ring] {
            l.frame = bounds
            l.path = UIBezierPath(ovalIn: rect).cgPath
        }
        icon.frame = rect.insetBy(dx: d * 0.3, dy: d * 0.3)
    }

    override var isHighlighted: Bool {
        didSet { alpha = isHighlighted ? 0.7 : 1 }
    }

    private func update() {
        let color: UIColor
        let symbol: String
        switch mode {
        case .idle:
            color = .systemBlue
            symbol = "mic.fill"
        case .recording:
            color = .systemRed
            symbol = "stop.fill"
        case .processing:
            color = .systemGray
            symbol = "ellipsis"
        }
        circle.fillColor = color.cgColor
        ring.fillColor = color.withAlphaComponent(0.25).cgColor
        ring.isHidden = mode != .recording
        if mode != .recording { level = 0 }
        icon.image = UIImage(systemName: symbol)
    }
}
