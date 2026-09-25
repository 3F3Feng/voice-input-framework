import Foundation

// 应用和键盘扩展之间的通信。两边都编进这个文件。
//
// iOS 的第三方键盘不能用麦克风,所以录音、连服务都在应用里做(应用开着一个后台音频会话),
// 键盘只发「开始 / 结束 / 放弃」、收结果。Typeless、Wispr Flow 都是这么做的。
//
// 数据放在 App Group 共享容器里的几个小 JSON 文件,而不是 UserDefaults(suiteName:):
// 跨进程时 UserDefaults 可能读到旧值,而键盘写完命令、应用紧接着就要读。
// 有变化时再发一个 Darwin 通知(不带内容,只是「去读一下」)。

/// 应用写、键盘读:会话和听写的状态,以及最近一条结果。
struct AppState: Codable {
    /// 应用的音频会话开着时每秒刷新。超过几秒没刷新 = 会话没了(超时结束或应用被杀),
    /// 键盘下次要重新拉起应用。
    var heartbeat: Double = 0
    var phase: Phase = .idle
    /// 正在处理的听写(键盘发起时带的 id)。
    var requestId: String?
    /// 给键盘显示的状态,已按界面语言写好。
    var status: String = ""
    /// 录音音量 0...1。
    var level: Float = 0

    var resultId: String?
    var resultText: String?
    /// 失败时的原因(此时 resultText 为 nil)。
    var resultError: String?
    /// LLM 后处理没做成时的原因(此时 resultText 是原文)。
    var resultNote: String?
    /// 结果出来的时间。键盘只自动插入刚出的结果,放久了的不往别的输入框里插。
    var resultAt: Double?

    enum Phase: String, Codable {
        case idle, recording, processing
    }

    var sessionAlive: Bool {
        Date().timeIntervalSince1970 - heartbeat < 3
    }
}

/// 键盘写、应用读。
struct Command: Codable {
    enum Action: String, Codable {
        case start, stop, cancel
    }

    var action: Action
    var requestId: String
    var at: Double = Date().timeIntervalSince1970
}

/// 键盘自己的状态。键盘扩展随时可能被系统销毁重建(切到应用再回来时几乎一定会),
/// 所以「我在等哪一条结果」「哪条已经插过了」也得落盘。
struct KeyboardState: Codable {
    var pendingId: String?
    var consumedResultId: String?
    var lastInserted: String?
}

enum SharedStore {
    /// App Group 的 id 写在两个 target 的 Info.plist 里(`VIFAppGroup`),来自 project.yml 的
    /// `APP_GROUP_ID`,和 entitlements 用的是同一个值。
    static let appGroup: String =
        Bundle.main.object(forInfoDictionaryKey: "VIFAppGroup") as? String ?? "group.com.voiceinput"

    /// 键盘没开「完全访问」时拿不到共享容器,返回 nil。
    static var container: URL? {
        FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: appGroup)
    }

    static func readAppState() -> AppState { read("app-state.json") ?? AppState() }
    static func writeAppState(_ s: AppState) { write(s, "app-state.json") }

    static func readCommand() -> Command? { read("command.json") }
    static func writeCommand(_ c: Command) { write(c, "command.json") }

    static func readKeyboardState() -> KeyboardState { read("keyboard-state.json") ?? KeyboardState() }
    static func writeKeyboardState(_ s: KeyboardState) { write(s, "keyboard-state.json") }

    private static func read<T: Decodable>(_ name: String) -> T? {
        guard let url = container?.appendingPathComponent(name),
              let data = try? Data(contentsOf: url) else { return nil }
        return try? JSONDecoder().decode(T.self, from: data)
    }

    private static func write<T: Encodable>(_ value: T, _ name: String) {
        guard let url = container?.appendingPathComponent(name),
              let data = try? JSONEncoder().encode(value) else { return }
        try? data.write(to: url, options: .atomic)
    }
}

/// 跨进程的「有新东西了」通知。
enum DarwinNote: String {
    /// 键盘写了新命令。
    case command = "com.voiceinput.command"
    /// 应用的状态变了(开始录音、出了结果……)。
    case state = "com.voiceinput.state"

    func post() {
        CFNotificationCenterPostNotification(
            CFNotificationCenterGetDarwinNotifyCenter(),
            CFNotificationName(rawValue as CFString),
            nil, nil, true
        )
    }
}

/// 监听一个 [DarwinNote]。对象活着就一直收,释放时自动注销。回调在主线程上。
final class DarwinObserver {
    private let handler: () -> Void
    private let name: String

    init(_ note: DarwinNote, handler: @escaping () -> Void) {
        self.handler = handler
        self.name = note.rawValue
        let observer = Unmanaged.passUnretained(self).toOpaque()
        CFNotificationCenterAddObserver(
            CFNotificationCenterGetDarwinNotifyCenter(),
            observer,
            { _, observer, _, _, _ in
                guard let observer else { return }
                let me = Unmanaged<DarwinObserver>.fromOpaque(observer).takeUnretainedValue()
                DispatchQueue.main.async { me.handler() }
            },
            name as CFString,
            nil,
            .deliverImmediately
        )
    }

    deinit {
        CFNotificationCenterRemoveEveryObserver(
            CFNotificationCenterGetDarwinNotifyCenter(),
            Unmanaged.passUnretained(self).toOpaque()
        )
    }
}

/// 界面跟系统语言走:中文系统显示中文,其余一律英文。
enum L {
    static let english: Bool = !(Locale.preferredLanguages.first ?? "en").hasPrefix("zh")

    static func t(_ zh: String, _ en: String) -> String { english ? en : zh }
}
