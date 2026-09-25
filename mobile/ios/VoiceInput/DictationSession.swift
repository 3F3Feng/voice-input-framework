import Foundation

/// 连哪台 STT 服务。和 Android 的 core/ServerConfig.kt 同一套规则。
struct ServerConfig {
    /// 已经过 [normalizeUrl] 的地址,如 `http://192.168.1.10:6544`。
    var baseUrl: String
    var token: String?
    /// `auto` / `zh` / `en` / `yue` / `ja` / `ko`,和桌面端一样。
    var language: String = "auto"

    static let defaultPort = 6544
    static let languages = ["auto", "zh", "en", "yue", "ja", "ko"]

    var acceptLanguage: String { L.english ? "en" : "zh-CN" }

    func url(_ path: String) -> URL? {
        var base = baseUrl
        while base.hasSuffix("/") { base.removeLast() }
        return URL(string: base + path)
    }

    /// WebSocket 地址:http → ws,https → wss。
    func wsURL(_ path: String) -> URL? {
        guard let u = url(path), var c = URLComponents(url: u, resolvingAgainstBaseURL: false) else { return nil }
        c.scheme = c.scheme == "https" ? "wss" : "ws"
        return c.url
    }

    /// 整理用户填的地址:只填主机时补 `http://` 和默认端口;写了 scheme 却没写端口时
    /// 不补(那通常是 `tailscale serve` / 反向代理的 https 地址,就该走 443)。不像地址时返回 nil。
    static func normalizeUrl(_ input: String) -> String? {
        let raw = input.trimmingCharacters(in: .whitespacesAndNewlines)
        func trimSlashes(_ s: String) -> String {
            var s = s
            while s.hasSuffix("/") { s.removeLast() }
            return s
        }
        var result: String
        if let range = raw.range(of: "://") {
            let scheme = raw[..<range.lowerBound].lowercased()
            let rest = trimSlashes(String(raw[range.upperBound...]))
            switch scheme {
            case "http", "ws": result = "http://" + rest
            case "https", "wss": result = "https://" + rest
            default: return nil
            }
        } else {
            let s = trimSlashes(raw)
            if s.isEmpty { return nil }
            let authority = String(s.prefix { $0 != "/" })
            let hasPort: Bool
            if authority.hasPrefix("[") {
                hasPort = authority.split(separator: "]", maxSplits: 1).dropFirst().first?.hasPrefix(":") ?? false
            } else {
                hasPort = authority.contains(":")
            }
            result = "http://" + authority + (hasPort ? "" : ":\(defaultPort)") + s.dropFirst(authority.count)
        }
        let host = result.components(separatedBy: "://")[1].prefix { $0 != "/" && $0 != "?" }
        if host.isEmpty || host.hasPrefix(":") || host.contains(where: { $0.isWhitespace }) { return nil }
        return result
    }
}

/// 一次听写:按下开始,边录边传,[finish] 后等结果。
///
/// 做法和 Android 的 core/DictationSession.kt、桌面端的 `LiveSession`(gui/src-tauri/src/stt.rs)
/// 一样:连接在后台建,不挡录音;录到的全部音频留在本地,连不上、服务端太老、连接中途断了,
/// 松手后换一条连接整段重发一次;服务端明确回了 error、超时、没听到声音就直接报错;
/// [cancel] 告诉服务端 `cancel`,之后不再回调。
///
/// 线程:状态只在内部串行队列上改,回调也在这个队列上,界面要自己切回主线程。
final class DictationSession {
    enum Stage {
        case connecting, streaming, recordingOffline, uploading, transcribing, polishing
    }

    struct Failure: Error {
        enum Kind { case noSpeech, unreachable, unauthorized, timeout, server }
        let kind: Kind
        let message: String
    }

    var onStage: ((Stage) -> Void)?
    var onSegment: ((Int, String) -> Void)?
    /// 最终文本;第二个参数不为 nil 时表示 LLM 后处理没做成,文本是原文。
    var onResult: ((String, String?) -> Void)?
    var onError: ((Failure) -> Void)?

    /// 边录边传时攒够这么多再发一条(约 0.25 秒)。
    static let liveFrameBytes = 8 * 1024
    /// 一条音频消息最多这么大(约 2 秒)。
    static let maxFrameBytes = 64 * 1024
    /// 还没发出去的数据积压到这么多就先停一停,等发送回调。
    static let inflightHighWater = 1024 * 1024
    /// 发起连接到收到 `ready` 的上限(连接超时也算在里面)。
    static let readyTimeout: TimeInterval = 10

    private let config: ServerConfig
    private let urlSession: URLSession
    private let queue = DispatchQueue(label: "dictation-session")

    // 以下只在 queue 上读写。
    private var audio = Data()
    private var sent = 0
    private var task: URLSessionWebSocketTask?
    private var generation = 0
    private var uploading = false
    private var ready = false
    private var finished = false
    private var awaiting = false
    private var closed = false
    private var lastText = ""
    private var sawKeepalive = false
    private var inflight = 0
    private var timer: DispatchWorkItem?

    init(config: ServerConfig, urlSession: URLSession = DictationSession.defaultURLSession) {
        self.config = config
        self.urlSession = urlSession
    }

    /// WebSocket 的请求超时放长:转写很久时服务端每 5 秒一次心跳,但老服务端不发;
    /// 结果超时由这里自己管。
    static let defaultURLSession: URLSession = {
        let c = URLSessionConfiguration.default
        c.timeoutIntervalForRequest = 600
        c.waitsForConnectivity = false
        return URLSession(configuration: c)
    }()

    func start() { queue.async { self.openSocket() } }

    /// 16 kHz 单声道 16 位小端 PCM。
    func appendAudio(_ pcm: Data) {
        queue.async {
            guard !self.closed, !self.finished else { return }
            self.audio.append(pcm)
            self.pump()
        }
    }

    func finish() {
        queue.async {
            guard !self.closed, !self.finished else { return }
            self.finished = true
            // 还在连时什么都不用做:ready 到了 pump 会发完再发 end;连不上会整段上传。
            if self.task == nil { self.startUpload() } else { self.pump() }
        }
    }

    func cancel() {
        queue.async {
            guard !self.closed else { return }
            self.closed = true
            if let t = self.task {
                t.send(.string(#"{"type":"cancel"}"#)) { _ in t.cancel(with: .normalClosure, reason: nil) }
            }
            self.task = nil
            self.generation += 1
            self.timer?.cancel()
        }
    }

    // MARK: 连接

    private func openSocket() {
        guard !closed else { return }
        generation += 1
        let gen = generation
        ready = false
        awaiting = false
        sawKeepalive = false
        sent = 0
        inflight = 0
        onStage?(uploading ? .uploading : .connecting)

        guard let url = config.wsURL("/ws/stream") else {
            fail(.unreachable, L.t("服务地址不对", "Invalid server address"))
            return
        }
        var request = URLRequest(url: url)
        request.setValue(config.acceptLanguage, forHTTPHeaderField: "Accept-Language")
        if let token = config.token { request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        let t = urlSession.webSocketTask(with: request)
        task = t
        t.resume()
        receive(t, gen: gen)
        schedule(Self.readyTimeout) { [weak self] in self?.onSocketLost("no ready message", httpCode: nil) }
    }

    private func receive(_ t: URLSessionWebSocketTask, gen: Int) {
        t.receive { [weak self] result in
            guard let self else { return }
            self.queue.async {
                guard gen == self.generation, !self.closed else { return }
                switch result {
                case .success(let message):
                    switch message {
                    case .string(let text): self.onServerMessage(text)
                    case .data(let data): self.onServerMessage(String(decoding: data, as: UTF8.self))
                    @unknown default: break
                    }
                    if gen == self.generation, !self.closed { self.receive(t, gen: gen) }
                case .failure(let error):
                    let code = (t.response as? HTTPURLResponse)?.statusCode
                    self.onSocketLost(error.localizedDescription, httpCode: code)
                }
            }
        }
    }

    private func dropSocket() {
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
        generation += 1
        ready = false
        awaiting = false
        timer?.cancel()
    }

    private func onSocketLost(_ reason: String, httpCode: Int?) {
        guard !closed else { return }
        dropSocket()
        // 令牌不对时服务端在握手阶段就拒绝(HTTP 403)。换条连接也一样,直接报。
        if httpCode == 401 || httpCode == 403 {
            fail(.unauthorized, L.t(
                "访问令牌缺失或不对,请在应用里填上服务端的 VIF_API_TOKEN",
                "Missing or invalid access token. Enter the server's VIF_API_TOKEN in the app."
            ))
            return
        }
        if uploading {
            fail(.unreachable, L.t("连不上识别服务", "Can't reach the STT service") + " (\(config.baseUrl)): \(reason)")
        } else if finished {
            startUpload()
        } else {
            onStage?(.recordingOffline)
        }
    }

    // MARK: 服务端消息

    private func onServerMessage(_ text: String) {
        guard !closed,
              let obj = try? JSONSerialization.jsonObject(with: Data(text.utf8)),
              let msg = obj as? [String: Any] else { return }
        func str(_ key: String) -> String? { msg[key] as? String }

        switch str("type") {
        case "ready":
            onReady(msg)
        case "segment":
            onSegment?((msg["index"] as? Int) ?? 0, str("text") ?? "")
        case "stt_result":
            if let t = str("text"), !t.isEmpty { lastText = t }
        case "llm_start":
            onStage?(.polishing)
        case "progress":
            sawKeepalive = true
        case "result":
            if let t = str("text"), !t.isEmpty { lastText = t }
            let note = str("llm_error")?.trimmingCharacters(in: .whitespacesAndNewlines)
            deliver(note?.isEmpty == false ? note : nil)
            return
        case "done":
            deliver(nil)
            return
        case "error":
            let message = str("error_message").flatMap { $0.isEmpty ? nil : $0 } ?? L.t("未知错误", "Unknown error")
            if awaiting {
                fail(.server, message)
            } else {
                // 录音中服务端报错:这条连接不能用了,松手后整段重发。
                onSocketLost("server error: \(message)", httpCode: nil)
            }
            return
        default:
            break
        }
        if awaiting { scheduleResultTimeout() }
    }

    private func onReady(_ msg: [String: Any]) {
        timer?.cancel()
        // 老服务端不支持边录边识别:这条连接没用,松手后整段上传。
        if !uploading && (msg["incremental"] as? Bool) != true {
            dropSocket()
            if finished { startUpload() } else { onStage?(.recordingOffline) }
            return
        }
        var cfg: [String: Any] = ["type": "config", "language": config.language]
        if !uploading { cfg["incremental"] = true }
        guard let data = try? JSONSerialization.data(withJSONObject: cfg) else { return }
        send(String(decoding: data, as: UTF8.self))
        ready = true
        if !finished { onStage?(.streaming) }
        pump()
    }

    // MARK: 发送

    /// 把没发的音频发出去;松手了且发完了就发 `end`。积压太多时停下,等发送回调再接着发。
    private func pump() {
        guard task != nil, ready, !awaiting, !closed else { return }
        if !finished && audio.count - sent < Self.liveFrameBytes { return }
        while sent < audio.count {
            if inflight >= Self.inflightHighWater { return }
            let end = min(sent + Self.maxFrameBytes, audio.count)
            let b64 = audio.subdata(in: sent..<end).base64EncodedString()
            send(#"{"type":"audio","data":""# + b64 + #""}"#)
            sent = end
        }
        if finished { sendEnd() }
    }

    private func send(_ text: String) {
        guard let t = task else { return }
        let gen = generation
        let size = text.utf8.count
        inflight += size
        t.send(.string(text)) { [weak self] error in
            guard let self else { return }
            self.queue.async {
                guard gen == self.generation, !self.closed else { return }
                self.inflight -= size
                if let error {
                    self.onSocketLost(error.localizedDescription, httpCode: nil)
                } else {
                    self.pump()
                }
            }
        }
    }

    private func sendEnd() {
        send(#"{"type":"end"}"#)
        awaiting = true
        onStage?(.transcribing)
        scheduleResultTimeout()
    }

    private func startUpload() {
        guard !closed else { return }
        if audio.isEmpty {
            fail(.noSpeech, noSpeech)
            return
        }
        uploading = true
        openSocket()
    }

    // MARK: 结束

    private func deliver(_ note: String?) {
        if lastText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            fail(.noSpeech, noSpeech)
            return
        }
        let text = lastText
        close()
        onResult?(text, note)
    }

    private func fail(_ kind: Failure.Kind, _ message: String) {
        close()
        onError?(Failure(kind: kind, message: message))
    }

    private func close() {
        closed = true
        task?.cancel(with: .normalClosure, reason: nil)
        task = nil
        generation += 1
        timer?.cancel()
    }

    private var noSpeech: String { L.t("没有听到说话", "No speech detected") }

    // MARK: 超时

    private func schedule(_ seconds: TimeInterval, _ action: @escaping () -> Void) {
        timer?.cancel()
        let gen = generation
        let item = DispatchWorkItem { [weak self] in
            guard let self, !self.closed, gen == self.generation else { return }
            action()
        }
        timer = item
        queue.asyncAfter(deadline: .now() + seconds, execute: item)
    }

    /// 见过心跳后 60 秒没动静就算挂了;老服务端不发心跳,只能等 5 分钟。
    private func scheduleResultTimeout() {
        let wait: TimeInterval = sawKeepalive ? 60 : 300
        schedule(wait) { [weak self] in
            self?.fail(.timeout, L.t(
                "识别服务 \(Int(wait)) 秒没有动静,可能已经卡住",
                "The STT service has been silent for \(Int(wait))s and may be stuck"
            ))
        }
    }
}

/// 应用里的「测试连接」。和 Android 的 core/ServerCheck.kt 一样:`/health` 看服务在不在,
/// 再拿需要令牌的 `/models` 试令牌。
enum ServerCheck {
    struct Result {
        let ok: Bool
        let message: String
    }

    static func run(_ config: ServerConfig) async -> Result {
        guard let healthURL = config.url("/health"), let modelsURL = config.url("/models") else {
            return Result(ok: false, message: L.t("服务地址不对", "Invalid server address"))
        }
        let health: (Int, Data)
        do {
            health = try await get(healthURL, config, withToken: false)
        } catch {
            return Result(ok: false, message: L.t("连不上服务", "Can't reach the service") + ": \(error.localizedDescription)")
        }
        guard health.0 == 200,
              let body = (try? JSONSerialization.jsonObject(with: health.1)) as? [String: Any] else {
            return Result(ok: false, message: L.t("这个地址不像是 STT 服务", "This doesn't look like the STT service"))
        }
        let model = body["current_model"] as? String
        let version = body["app_version"] as? String

        if let models = try? await get(modelsURL, config, withToken: true), models.0 == 401 {
            return Result(ok: false, message: (config.token ?? "").isEmpty
                ? L.t("服务端要求访问令牌,请填上 VIF_API_TOKEN", "The server requires an access token (VIF_API_TOKEN)")
                : L.t("访问令牌不对", "The access token is wrong"))
        }

        var detail = L.t("已连接", "Connected")
        if let model, !model.isEmpty { detail += " · \(model)" }
        if let version, !version.isEmpty { detail += " · v\(version)" }
        switch body["status"] as? String {
        case "ok": return Result(ok: true, message: detail)
        case "loading": return Result(ok: true, message: detail + L.t("(模型加载中)", " (model loading)"))
        default:
            let err = (body["error"] as? String) ?? L.t("模型没有加载", "Model not loaded")
            return Result(ok: false, message: detail + " · " + err)
        }
    }

    private static func get(_ url: URL, _ config: ServerConfig, withToken: Bool) async throws -> (Int, Data) {
        var request = URLRequest(url: url, timeoutInterval: 8)
        request.setValue(config.acceptLanguage, forHTTPHeaderField: "Accept-Language")
        if withToken, let token = config.token {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        let (data, response) = try await URLSession.shared.data(for: request)
        return ((response as? HTTPURLResponse)?.statusCode ?? 0, data)
    }
}
