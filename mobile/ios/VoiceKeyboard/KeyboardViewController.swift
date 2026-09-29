import UIKit

/// 语音键盘。自己不录音(iOS 不给键盘麦克风),只是 [DictationController] 的遥控器:
///
/// - 点麦克风:应用的会话开着 → 发「开始」命令;没开 → 拉起应用(`voiceinput://dictate?id=…`),
///   应用在前台开会话、马上开始录,用户说完回到这里;
/// - 录音中再点一下 → 发「结束」;取消 → 发「放弃」;
/// - 应用把结果写进共享容器,这里读到属于自己的那条就插进输入框;
/// - 顶部选主要识别语言,存进共享容器,应用下一次听写就用它。
///
/// 键盘扩展随时会被系统销毁重建(切到应用再回来时几乎一定会),所以「在等哪一条」
/// 记在共享容器里(KeyboardState),而不是这个对象上。
///
/// 界面用 iOS 26 的液态玻璃(Liquid Glass);更老的系统退回毛玻璃材质。
final class KeyboardViewController: UIInputViewController {
    private let backdrop = BackdropView()
    private let brandIcon = UIImageView()
    private let languageBar = LanguageBar()
    private let statusLabel = UILabel()
    private let micButton = MicButton()
    private let globeKey = KeyButton(symbol: "globe")
    private let actionKey = KeyButton(title: L.t("重插", "Reinsert"))
    private let spaceKey = KeyButton(title: L.t("空格", "space"))
    private let deleteKey = KeyButton(symbol: "delete.left")
    private let returnKey = KeyButton(symbol: "return")

    private let impact = UIImpactFeedbackGenerator(style: .light)
    private let selection = UISelectionFeedbackGenerator()

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
        impact.prepare()
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
        updateReturnKey()
    }

    // MARK: 界面

    private func buildUI() {
        let height = view.heightAnchor.constraint(equalToConstant: 272)
        height.priority = .init(999)
        height.isActive = true

        backdrop.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(backdrop)

        brandIcon.image = UIImage(
            systemName: "waveform.circle.fill",
            withConfiguration: UIImage.SymbolConfiguration(pointSize: 26, weight: .semibold)
        )
        brandIcon.tintColor = .systemBlue
        brandIcon.contentMode = .scaleAspectFit
        brandIcon.setContentHuggingPriority(.required, for: .horizontal)

        languageBar.selected = SharedStore.readSettings()?.language ?? "auto"
        languageBar.onSelect = { [weak self] code in self?.languageChanged(code) }

        statusLabel.font = .systemFont(ofSize: 15, weight: .medium)
        statusLabel.textColor = .secondaryLabel
        statusLabel.numberOfLines = 2
        statusLabel.textAlignment = .center
        statusLabel.adjustsFontSizeToFitWidth = true
        statusLabel.minimumScaleFactor = 0.85

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
        updateReturnKey()

        let topRow = UIStackView(arrangedSubviews: [brandIcon, languageBar])
        topRow.spacing = 10
        topRow.alignment = .center

        let row = UIStackView(arrangedSubviews: [globeKey, actionKey, spaceKey, deleteKey, returnKey])
        row.spacing = 8
        row.distribution = .fill

        let stack = UIStackView(arrangedSubviews: [topRow, statusLabel, micButton, row])
        stack.axis = .vertical
        stack.alignment = .fill
        stack.spacing = 8
        stack.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(stack)

        NSLayoutConstraint.activate([
            backdrop.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            backdrop.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            backdrop.topAnchor.constraint(equalTo: view.topAnchor),
            backdrop.bottomAnchor.constraint(equalTo: view.bottomAnchor),

            stack.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 10),
            stack.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -10),
            stack.topAnchor.constraint(equalTo: view.topAnchor, constant: 10),
            stack.bottomAnchor.constraint(equalTo: view.bottomAnchor, constant: -6),

            brandIcon.widthAnchor.constraint(equalToConstant: 28),
            languageBar.heightAnchor.constraint(equalToConstant: 36),
            micButton.heightAnchor.constraint(equalToConstant: 88),
            row.heightAnchor.constraint(equalToConstant: 46),
            globeKey.widthAnchor.constraint(equalToConstant: 52),
            actionKey.widthAnchor.constraint(equalToConstant: 88),
            deleteKey.widthAnchor.constraint(equalToConstant: 56),
            returnKey.widthAnchor.constraint(equalToConstant: 88),
        ])
    }

    // MARK: 状态

    private func refresh() {
        guard hasFullAccess, SharedStore.container != nil else {
            micButton.mode = .idle
            backdrop.mode = .idle
            languageBar.isEnabled = false
            statusLabel.text = L.t(
                "请在 设置 → 通用 → 键盘 → 键盘 → 语音输入 里打开「允许完全访问」",
                "Turn on Allow Full Access in Settings → General → Keyboard → Keyboards → Voice Input"
            )
            actionKey.isEnabled = false
            return
        }
        languageBar.isEnabled = true
        languageBar.selected = SharedStore.readSettings()?.language ?? "auto"

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

        let phase = effectivePhase(appState)
        switch phase {
        case .recording where mine:
            micButton.mode = .recording
            micButton.level = CGFloat(appState.level)
        case .processing where mine:
            micButton.mode = .processing
        default:
            micButton.mode = .idle
        }
        backdrop.mode = micButton.mode

        let busy = mine && phase != .idle
        actionKey.set(title: busy ? L.t("取消", "Cancel") : L.t("重插", "Reinsert"))
        actionKey.kind = busy ? .destructive : .normal
        actionKey.accessibilityLabel = busy ? L.t("放弃这段录音", "Discard") : L.t("再插入一次上一条结果", "Insert the last result again")
        actionKey.isEnabled = busy || ks.lastInserted != nil

        if busy {
            // 录音中给出下一步该做什么;处理中沿用应用给的状态(「正在整理…」等)。
            statusLabel.text = phase == .recording ? L.t("再点一下结束", "Tap again to finish") : appState.status
        } else if let local = localStatus {
            statusLabel.text = local
        } else if phase != .idle {
            statusLabel.text = L.t("应用里正在听写…", "Dictating in the app…")
        } else if appState.sessionAlive {
            statusLabel.text = L.t("点一下说话", "Tap to speak")
        } else {
            statusLabel.text = L.t("点一下说话(会先打开应用,说完回到这里)", "Tap to speak (opens the app first, then come back)")
        }
    }

    /// 应用被杀 / 崩了以后,共享容器里还留着「录音中」;没有心跳就当空闲,别一直卡在录音状态。
    /// 界面和按键都要用这个,不能直接读 `phase`。
    private func effectivePhase(_ st: AppState) -> AppState.Phase {
        st.sessionAlive ? st.phase : .idle
    }

    private func languageChanged(_ code: String) {
        guard hasFullAccess, SharedStore.container != nil else { return }
        selection.selectionChanged()
        SharedStore.writeSettings(SharedSettings(language: code))
        DarwinNote.settings.post()
    }

    // MARK: 按键

    @objc private func micTapped() {
        guard hasFullAccess, SharedStore.container != nil else { return refresh() }
        impact.impactOccurred()
        localStatus = nil
        let st = SharedStore.readAppState()
        var ks = SharedStore.readKeyboardState()
        switch effectivePhase(st) {
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
        if let id = st.requestId, id == ks.pendingId, effectivePhase(st) != .idle {
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

    /// 普通回车用图标;搜索、发送等有明确动作的用带色的文字键。
    private func updateReturnKey() {
        let label: String?
        switch textDocumentProxy.returnKeyType ?? .default {
        case .go: label = L.t("前往", "Go")
        case .search, .google, .yahoo: label = L.t("搜索", "Search")
        case .send: label = L.t("发送", "Send")
        case .next: label = L.t("下一项", "Next")
        case .done: label = L.t("完成", "Done")
        default: label = nil
        }
        if let label {
            returnKey.set(title: label, symbol: nil)
            returnKey.kind = .accent
        } else {
            returnKey.set(title: nil, symbol: "return")
            returnKey.kind = .normal
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

// MARK: - 液态玻璃

/// 玻璃底板。iOS 26 起用真正的液态玻璃(`UIGlassEffect`),更老的系统退回毛玻璃材质。
/// 玻璃只放在浮在内容之上的控件上,不叠玻璃。
final class GlassView: UIVisualEffectView {
    enum Shape {
        case capsule
        case rounded(CGFloat)
    }

    private let shape: Shape
    private var tint: UIColor?
    private let interactiveGlass: Bool

    init(shape: Shape, tint: UIColor? = nil, interactive: Bool = false) {
        self.shape = shape
        self.tint = tint
        self.interactiveGlass = interactive
        super.init(effect: nil)
        effect = makeEffect()
        applyShape()
        if #unavailable(iOS 26.0) { contentView.backgroundColor = tint }
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) is not used") }

    /// 换玻璃的染色(录音时变红等)。带渐变动画。
    func setTint(_ tint: UIColor?, animated: Bool = true) {
        self.tint = tint
        let apply = {
            self.effect = self.makeEffect()
            if #unavailable(iOS 26.0) { self.contentView.backgroundColor = tint }
        }
        animated ? UIView.animate(withDuration: 0.3, animations: apply) : apply()
    }

    private func makeEffect() -> UIVisualEffect {
        if #available(iOS 26.0, *) {
            let glass = UIGlassEffect(style: .regular)
            glass.tintColor = tint
            glass.isInteractive = interactiveGlass
            return glass
        }
        return UIBlurEffect(style: .systemThinMaterial)
    }

    private func applyShape() {
        if #available(iOS 26.0, *) {
            switch shape {
            case .capsule: cornerConfiguration = .capsule()
            case .rounded(let r): cornerConfiguration = .corners(radius: .fixed(r))
            }
        } else {
            clipsToBounds = true
            layer.cornerCurve = .continuous
        }
    }

    override func layoutSubviews() {
        super.layoutSubviews()
        if #unavailable(iOS 26.0) {
            switch shape {
            case .capsule: layer.cornerRadius = bounds.height / 2
            case .rounded(let r): layer.cornerRadius = r
            }
        }
    }
}

/// 键盘背后柔和的彩色光晕:让玻璃有东西可折射。录音时偏红,处理中偏灰。
final class BackdropView: UIView {
    /// 上下边缘各渐隐多少 pt。键盘扩展只能画自己那个窗口;窗口上方(有的宿主会留一条)
    /// 和下方(系统的地球键 / 听写键那一行)是系统自己画的背景,画不到。
    /// 边缘渐隐到透明,和它们自然衔接,不留一条硬边。
    static let fade: CGFloat = 28

    var mode: MicButton.Mode = .idle {
        didSet { if mode != oldValue { updateColors() } }
    }

    private let base = CALayer()
    private let blobA = CAGradientLayer()
    private let blobB = CAGradientLayer()
    private let fadeMask = CAGradientLayer()

    override init(frame: CGRect) {
        super.init(frame: frame)
        isUserInteractionEnabled = false
        layer.addSublayer(base)
        fadeMask.colors = [UIColor.clear.cgColor, UIColor.black.cgColor, UIColor.black.cgColor, UIColor.clear.cgColor]
        layer.mask = fadeMask
        for blob in [blobA, blobB] {
            blob.type = .radial
            blob.startPoint = CGPoint(x: 0.5, y: 0.5)
            blob.endPoint = CGPoint(x: 1, y: 1)
            layer.addSublayer(blob)
        }
        updateColors()
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) is not used") }

    override func layoutSubviews() {
        super.layoutSubviews()
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        base.frame = bounds
        let h = bounds.height
        blobA.frame = CGRect(x: -bounds.width * 0.15, y: -h * 0.25, width: bounds.width * 0.75, height: h)
        blobB.frame = CGRect(x: bounds.width * 0.4, y: h * 0.05, width: bounds.width * 0.8, height: h)
        fadeMask.frame = bounds
        if h > 0 {
            let f = min(Self.fade / h, 0.4)
            fadeMask.locations = [0, NSNumber(value: f), NSNumber(value: 1 - f), 1]
        }
        CATransaction.commit()
        updateColors()
    }

    private func updateColors() {
        let (a, b): (UIColor, UIColor)
        switch mode {
        case .idle: (a, b) = (.systemBlue, .systemPurple)
        case .recording: (a, b) = (.systemRed, .systemOrange)
        case .processing: (a, b) = (.systemGray, .systemTeal)
        }
        func colors(_ c: UIColor, _ alpha: CGFloat) -> [CGColor] {
            let resolved = c.resolvedColor(with: traitCollection)
            return [resolved.withAlphaComponent(alpha).cgColor, resolved.withAlphaComponent(0).cgColor]
        }
        blobA.colors = colors(a, 0.42)
        blobB.colors = colors(b, 0.32)
        base.backgroundColor = a.resolvedColor(with: traitCollection).withAlphaComponent(0.16).cgColor
    }
}

// MARK: - 语言栏

/// 顶部的主要识别语言选择:一条玻璃胶囊,选中的那个有滑动的色块。
final class LanguageBar: UIView {
    struct Item {
        let code: String
        let label: String
        let name: String
    }

    static let items: [Item] = [
        Item(code: "auto", label: L.t("自动", "Auto"), name: L.t("自动检测", "Auto-detect")),
        Item(code: "zh", label: "中", name: L.t("中文", "Chinese")),
        Item(code: "en", label: "EN", name: "English"),
        Item(code: "yue", label: "粤", name: L.t("粤语", "Cantonese")),
        Item(code: "ja", label: "日", name: "日本語"),
        Item(code: "ko", label: "한", name: "한국어"),
    ]

    var selected: String = "auto" {
        didSet {
            guard selected != oldValue else { return }
            updateSelection(animated: window != nil)
        }
    }
    var onSelect: ((String) -> Void)?
    var isEnabled = true {
        didSet {
            isUserInteractionEnabled = isEnabled
            alpha = isEnabled ? 1 : 0.5
        }
    }

    private let glass = GlassView(shape: .capsule)
    private let thumb = UIView()
    private let stack = UIStackView()
    private var buttons: [UIButton] = []
    private static let inset: CGFloat = 3

    override init(frame: CGRect) {
        super.init(frame: frame)
        addSubview(glass)
        thumb.backgroundColor = .systemBlue
        thumb.layer.cornerCurve = .continuous
        thumb.layer.shadowColor = UIColor.systemBlue.cgColor
        thumb.layer.shadowOpacity = 0.35
        thumb.layer.shadowRadius = 6
        thumb.layer.shadowOffset = CGSize(width: 0, height: 2)
        glass.contentView.addSubview(thumb)

        stack.distribution = .fillEqually
        glass.contentView.addSubview(stack)
        for (i, item) in Self.items.enumerated() {
            let b = UIButton(type: .custom)
            b.setTitle(item.label, for: .normal)
            b.titleLabel?.font = Self.font(size: item.label.count > 2 ? 13 : 15)
            b.tag = i
            b.accessibilityLabel = item.name
            b.addTarget(self, action: #selector(tapped(_:)), for: .touchUpInside)
            stack.addArrangedSubview(b)
            buttons.append(b)
        }
        updateSelection(animated: false)
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) is not used") }

    private static func font(size: CGFloat) -> UIFont {
        let base = UIFont.systemFont(ofSize: size, weight: .semibold)
        return base.fontDescriptor.withDesign(.rounded).map { UIFont(descriptor: $0, size: size) } ?? base
    }

    override func layoutSubviews() {
        super.layoutSubviews()
        glass.frame = bounds
        stack.frame = bounds.insetBy(dx: Self.inset, dy: Self.inset)
        thumb.frame = thumbFrame()
        thumb.layer.cornerRadius = thumb.bounds.height / 2
    }

    private func thumbFrame() -> CGRect {
        let area = bounds.insetBy(dx: Self.inset, dy: Self.inset)
        let w = area.width / CGFloat(Self.items.count)
        let i = Self.items.firstIndex { $0.code == selected } ?? 0
        return CGRect(x: area.minX + w * CGFloat(i), y: area.minY, width: w, height: area.height)
    }

    private func updateSelection(animated: Bool) {
        let apply = {
            self.thumb.frame = self.thumbFrame()
            for (i, b) in self.buttons.enumerated() {
                let on = Self.items[i].code == self.selected
                b.setTitleColor(on ? .white : .label, for: .normal)
                b.accessibilityTraits = on ? [.button, .selected] : .button
            }
        }
        if animated {
            UIView.animate(withDuration: 0.35, delay: 0, usingSpringWithDamping: 0.8, initialSpringVelocity: 0.4, options: [.allowUserInteraction], animations: apply)
        } else {
            apply()
        }
    }

    @objc private func tapped(_ sender: UIButton) {
        let code = Self.items[sender.tag].code
        guard code != selected else { return }
        selected = code
        onSelect?(code)
    }
}

// MARK: - 按键

/// 玻璃功能键:图标或文字。按下时轻轻缩一下。
final class KeyButton: UIControl {
    enum Kind { case normal, accent, destructive }

    var kind: Kind = .normal {
        didSet { if kind != oldValue { applyKind() } }
    }

    private let glass = GlassView(shape: .rounded(14))
    private let icon = UIImageView()
    private let label = UILabel()

    init(title: String? = nil, symbol: String? = nil) {
        super.init(frame: .zero)
        // 玻璃盖满整个键;不关掉它的交互,点击会落在玻璃上,传不到按键自己。
        glass.isUserInteractionEnabled = false
        addSubview(glass)

        icon.contentMode = .scaleAspectFit
        icon.isUserInteractionEnabled = false
        label.font = .systemFont(ofSize: 16, weight: .semibold)
        label.isUserInteractionEnabled = false
        let content = UIStackView(arrangedSubviews: [icon, label])
        content.spacing = 6
        content.alignment = .center
        content.isUserInteractionEnabled = false
        content.translatesAutoresizingMaskIntoConstraints = false
        glass.contentView.addSubview(content)
        NSLayoutConstraint.activate([
            content.centerXAnchor.constraint(equalTo: glass.contentView.centerXAnchor),
            content.centerYAnchor.constraint(equalTo: glass.contentView.centerYAnchor),
            content.leadingAnchor.constraint(greaterThanOrEqualTo: glass.contentView.leadingAnchor, constant: 8),
        ])

        set(title: title, symbol: symbol)
        applyKind()
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) is not used") }

    /// 只改文字,图标不动。
    func set(title: String?) { setLabel(title) }

    func set(title: String?, symbol: String?) {
        setLabel(title)
        icon.image = symbol.flatMap {
            UIImage(systemName: $0, withConfiguration: UIImage.SymbolConfiguration(pointSize: 18, weight: .semibold))
        }
        icon.isHidden = icon.image == nil
    }

    private func setLabel(_ title: String?) {
        label.text = title
        label.isHidden = title == nil
    }

    override func layoutSubviews() {
        super.layoutSubviews()
        glass.frame = bounds
    }

    override var isHighlighted: Bool {
        didSet {
            UIView.animate(withDuration: 0.25, delay: 0, usingSpringWithDamping: 0.7, initialSpringVelocity: 0.5, options: [.allowUserInteraction, .beginFromCurrentState]) {
                self.transform = self.isHighlighted ? CGAffineTransform(scaleX: 0.93, y: 0.93) : .identity
            }
        }
    }

    override var isEnabled: Bool {
        didSet { alpha = isEnabled ? 1 : 0.4 }
    }

    private func applyKind() {
        let color: UIColor
        switch kind {
        case .normal:
            glass.setTint(nil)
            color = .label
        case .accent:
            glass.setTint(UIColor.systemBlue.withAlphaComponent(0.9))
            color = .white
        case .destructive:
            glass.setTint(nil)
            color = .systemRed
        }
        label.textColor = color
        icon.tintColor = color
    }
}

// MARK: - 麦克风键

/// 大的玻璃胶囊麦克风键:待机是蓝色的麦克风,录音时变红、里面是随音量跳动的波形,
/// 处理中变灰、转圈。录音时外面一圈光晕随音量变大。
final class MicButton: UIControl {
    enum Mode { case idle, recording, processing }

    var mode: Mode = .idle { didSet { if mode != oldValue { update() } } }
    var level: CGFloat = 0 {
        didSet {
            let scale = 1 + 0.14 * max(min(level, 1), 0)
            halo.transform = CATransform3DMakeScale(scale, scale, 1)
            waveform.level = level
        }
    }

    private static let capsuleSize = CGSize(width: 190, height: 68)

    private let halo = CAShapeLayer()
    private let glass = GlassView(shape: .capsule, tint: MicButton.tint(.idle))
    private let micIcon = UIImageView()
    private let waveform = WaveformView()
    private let spinner = UIActivityIndicatorView(style: .medium)

    override init(frame: CGRect) {
        super.init(frame: frame)
        layer.addSublayer(halo)
        glass.isUserInteractionEnabled = false
        addSubview(glass)

        micIcon.image = UIImage(
            systemName: "mic.fill",
            withConfiguration: UIImage.SymbolConfiguration(pointSize: 28, weight: .semibold)
        )
        micIcon.tintColor = .white
        micIcon.contentMode = .scaleAspectFit
        spinner.color = .white
        for v in [micIcon, waveform, spinner] as [UIView] {
            v.isUserInteractionEnabled = false
            glass.contentView.addSubview(v)
        }
        update()
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) is not used") }

    private static func tint(_ mode: Mode) -> UIColor {
        switch mode {
        case .idle: return UIColor(red: 0.20, green: 0.48, blue: 1.0, alpha: 0.92)
        case .recording: return UIColor(red: 1.0, green: 0.27, blue: 0.32, alpha: 0.92)
        case .processing: return UIColor.systemGray.withAlphaComponent(0.85)
        }
    }

    override func layoutSubviews() {
        super.layoutSubviews()
        let w = min(bounds.width, Self.capsuleSize.width)
        let h = min(bounds.height - 12, Self.capsuleSize.height)
        let capsule = CGRect(x: bounds.midX - w / 2, y: bounds.midY - h / 2, width: w, height: h)
        glass.frame = capsule

        let center = CGPoint(x: capsule.width / 2, y: capsule.height / 2)
        micIcon.frame = CGRect(x: center.x - 18, y: center.y - 18, width: 36, height: 36)
        waveform.frame = CGRect(x: center.x - 30, y: center.y - 20, width: 60, height: 40)
        spinner.center = center

        // 光晕:不能设 frame。它带着放大的 transform 时,frame 会按变换后的大小反推 bounds,
        // 形状就偏离图层中心。
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        halo.bounds = CGRect(origin: .zero, size: bounds.size)
        halo.position = CGPoint(x: bounds.midX, y: bounds.midY)
        halo.path = UIBezierPath(roundedRect: capsule, cornerRadius: h / 2).cgPath
        CATransaction.commit()
    }

    override var isHighlighted: Bool {
        didSet {
            UIView.animate(withDuration: 0.3, delay: 0, usingSpringWithDamping: 0.65, initialSpringVelocity: 0.5, options: [.allowUserInteraction, .beginFromCurrentState]) {
                self.transform = self.isHighlighted ? CGAffineTransform(scaleX: 0.95, y: 0.95) : .identity
            }
        }
    }

    override func didMoveToWindow() {
        super.didMoveToWindow()
        if window == nil { waveform.stop() } else { update() }
    }

    private func update() {
        glass.setTint(Self.tint(mode))
        halo.fillColor = Self.tint(mode).withAlphaComponent(0.28).cgColor
        halo.shadowColor = Self.tint(mode).cgColor
        halo.shadowOpacity = 0.5
        halo.shadowRadius = 14
        halo.shadowOffset = .zero
        halo.isHidden = mode != .recording
        if mode != .recording { level = 0 }

        micIcon.isHidden = mode != .idle
        waveform.isHidden = mode != .recording
        if mode == .recording, window != nil { waveform.start() } else { waveform.stop() }
        if mode == .processing { spinner.startAnimating() } else { spinner.stopAnimating() }
    }
}

/// 录音时胶囊里的五根竖条:随音量起伏,静音时也轻轻晃动,表示「正在听」。
final class WaveformView: UIView {
    var level: CGFloat = 0

    private let bars: [CALayer] = (0..<5).map { _ in CALayer() }
    private var link: CADisplayLink?
    private var smoothed: CGFloat = 0
    private var phase: CGFloat = 0

    override init(frame: CGRect) {
        super.init(frame: frame)
        for bar in bars {
            bar.backgroundColor = UIColor.white.cgColor
            bar.cornerRadius = 2.5
            layer.addSublayer(bar)
        }
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) is not used") }

    func start() {
        guard link == nil else { return }
        link = CADisplayLink(target: self, selector: #selector(tick))
        link?.add(to: .main, forMode: .common)
    }

    func stop() {
        link?.invalidate()
        link = nil
    }

    override func layoutSubviews() {
        super.layoutSubviews()
        layoutBars()
    }

    @objc private func tick() {
        phase += 0.16
        smoothed += (min(max(level, 0), 1) - smoothed) * 0.25
        layoutBars()
    }

    private func layoutBars() {
        let barWidth: CGFloat = 5
        let gap: CGFloat = 5
        let total = barWidth * CGFloat(bars.count) + gap * CGFloat(bars.count - 1)
        let startX = (bounds.width - total) / 2
        let amplitude = min(1, 0.2 + smoothed * 1.2)
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        for (i, bar) in bars.enumerated() {
            let wave = 0.5 + 0.5 * sin(phase + CGFloat(i) * 0.9)
            let h = 8 + (bounds.height - 8) * amplitude * (0.35 + 0.65 * wave)
            bar.frame = CGRect(x: startX + CGFloat(i) * (barWidth + gap), y: (bounds.height - h) / 2, width: barWidth, height: h)
        }
        CATransaction.commit()
    }
}
