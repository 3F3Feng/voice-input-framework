import AVFoundation
import Foundation

/// 应用这一侧的听写:开着一个「会话」(后台音频),替键盘录音、连服务、交结果。
///
/// 为什么要「会话」:iOS 不允许应用在后台**开始**录音,只允许前台开始的录音在后台继续。
/// 所以键盘第一次点麦克风时拉起应用,应用在前台把音频引擎开起来,之后用户回到原来的应用,
/// 引擎一直开着(状态栏会有橙色麦克风点),键盘每次点「开始 / 结束」只是切换要不要把
/// 采到的音频交给服务。会话闲置 [sessionMinutes] 分钟后自动关掉。
@MainActor
final class DictationController: ObservableObject {
    static let shared = DictationController()

    enum Phase { case idle, recording, processing }

    @Published private(set) var sessionActive = false
    @Published private(set) var phase: Phase = .idle
    @Published private(set) var status = ""
    @Published private(set) var sessionEndsAt: Date?
    /// 应用内试用时的结果。
    @Published var transcript = ""
    @Published private(set) var micPermission: AVAudioSession.RecordPermission = AVAudioSession.sharedInstance().recordPermission

    // MARK: 设置(只有应用用;键盘不需要知道服务在哪)

    @Published var serverUrl: String = UserDefaults.standard.string(forKey: "serverUrl") ?? "" {
        didSet { UserDefaults.standard.set(serverUrl, forKey: "serverUrl") }
    }
    @Published var token: String = UserDefaults.standard.string(forKey: "token") ?? "" {
        didSet { UserDefaults.standard.set(token, forKey: "token") }
    }
    @Published var language: String = UserDefaults.standard.string(forKey: "language") ?? "auto" {
        didSet { UserDefaults.standard.set(language, forKey: "language") }
    }
    @Published var sessionMinutes: Int = UserDefaults.standard.object(forKey: "sessionMinutes") as? Int ?? 5 {
        didSet {
            UserDefaults.standard.set(sessionMinutes, forKey: "sessionMinutes")
            touchSession()
        }
    }

    /// 正在进行的是应用里的试用,而不是替键盘录的。
    var transcriptMode: Bool { requestId == "app" }

    var serverConfig: ServerConfig? {
        guard !serverUrl.isEmpty else { return nil }
        let t = token.trimmingCharacters(in: .whitespacesAndNewlines)
        return ServerConfig(baseUrl: serverUrl, token: t.isEmpty ? nil : t, language: language)
    }

    // MARK: 内部

    private let engine = AVAudioEngine()
    private let sink = CaptureSink()
    private var session: DictationSession?
    private var requestId: String?
    private var heartbeat: Timer?
    /// 录音时给键盘的音量条刷新得勤一点(心跳一秒一次太慢)。
    private var levelTimer: Timer?
    private var commandObserver: DarwinObserver?
    private var lastCommandAt: Double = 0
    private var state = AppState()
    private var observers: [NSObjectProtocol] = []

    private init() {
        commandObserver = DarwinObserver(.command) { [weak self] in
            Task { @MainActor in self?.handleCommand() }
        }
        let center = NotificationCenter.default
        observers.append(center.addObserver(
            forName: AVAudioSession.interruptionNotification, object: nil, queue: .main
        ) { [weak self] note in
            guard let raw = note.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt,
                  AVAudioSession.InterruptionType(rawValue: raw) == .began else { return }
            // 来电话之类:录音被系统停了,会话也就没了。正在录的那段照常交出去。
            Task { @MainActor in self?.endSession(reason: L.t("被系统打断(来电等)", "Interrupted by the system")) }
        })
        observers.append(center.addObserver(
            forName: .AVAudioEngineConfigurationChange, object: engine, queue: .main
        ) { [weak self] _ in
            // 插拔耳机、连蓝牙:引擎会自己停,换了输入格式,要重新装 tap。
            Task { @MainActor in self?.restartEngineAfterRouteChange() }
        })
        publish()
    }

    // MARK: 键盘来的请求

    /// 键盘拉起应用:`voiceinput://dictate?id=<uuid>`。开会话,马上开始录。
    func handle(url: URL) {
        guard url.host == "dictate" else { return }
        let id = URLComponents(url: url, resolvingAgainstBaseURL: false)?
            .queryItems?.first { $0.name == "id" }?.value ?? UUID().uuidString
        Task {
            guard await startSession() else { return }
            startDictation(id: id)
        }
    }

    private func handleCommand() {
        guard let cmd = SharedStore.readCommand(), cmd.at > lastCommandAt else { return }
        lastCommandAt = cmd.at
        switch cmd.action {
        case .start:
            if sessionActive { startDictation(id: cmd.requestId) }
        case .stop:
            if cmd.requestId == requestId { stopDictation() }
        case .cancel:
            if cmd.requestId == requestId { cancelDictation() }
        }
    }

    // MARK: 会话

    func requestMicPermission() async -> Bool {
        let granted = await withCheckedContinuation { cont in
            AVAudioSession.sharedInstance().requestRecordPermission { cont.resume(returning: $0) }
        }
        micPermission = AVAudioSession.sharedInstance().recordPermission
        return granted
    }

    /// 开音频会话和引擎。必须在前台调用。
    @discardableResult
    func startSession() async -> Bool {
        if sessionActive {
            touchSession()
            return true
        }
        if micPermission != .granted {
            guard await requestMicPermission() else {
                setStatus(L.t("没有麦克风权限", "No microphone permission"))
                return false
            }
        }
        do {
            let av = AVAudioSession.sharedInstance()
            // mixWithOthers:开着会话时用户照样能听音乐 / 播客。
            try av.setCategory(.playAndRecord, mode: .default,
                               options: [.mixWithOthers, .allowBluetooth, .defaultToSpeaker])
            try av.setActive(true)
            try startEngine()
        } catch {
            setStatus(L.t("麦克风打不开:", "Couldn't start the microphone: ") + error.localizedDescription)
            return false
        }
        sessionActive = true
        touchSession()
        heartbeat?.invalidate()
        heartbeat = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.tick() }
        }
        setStatus(L.t("会话已开始", "Session started"))
        return true
    }

    func endSession(reason: String? = nil) {
        if phase == .recording { stopDictation() }
        sink.session = nil
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        heartbeat?.invalidate()
        heartbeat = nil
        sessionActive = false
        sessionEndsAt = nil
        state.heartbeat = 0
        setStatus(reason ?? L.t("会话已结束", "Session ended"))
    }

    private func startEngine() throws {
        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        guard format.sampleRate > 0, format.channelCount > 0 else {
            throw NSError(domain: "VoiceInput", code: 1,
                          userInfo: [NSLocalizedDescriptionKey: L.t("没有可用的麦克风", "No microphone available")])
        }
        guard let converter = PCMConverter(from: format) else {
            throw NSError(domain: "VoiceInput", code: 2,
                          userInfo: [NSLocalizedDescriptionKey: "Unsupported input format \(format)"])
        }
        let sink = self.sink
        input.removeTap(onBus: 0)
        // tap 在音频线程上回调;只有正在听写时才转换、上交。
        input.installTap(onBus: 0, bufferSize: 4096, format: format) { buffer, _ in
            guard let session = sink.session, let pcm = converter.convert(buffer) else { return }
            session.appendAudio(pcm)
            sink.report(level: PCMConverter.level(pcm))
        }
        engine.prepare()
        try engine.start()
    }

    private func restartEngineAfterRouteChange() {
        guard sessionActive else { return }
        do {
            try startEngine()
        } catch {
            endSession(reason: L.t("音频设备变了,会话已结束", "Audio device changed; session ended"))
        }
    }

    /// 有动静就把会话的截止时间往后推。
    private func touchSession() {
        guard sessionActive else { return }
        sessionEndsAt = Date().addingTimeInterval(TimeInterval(sessionMinutes * 60))
    }

    private func tick() {
        if let end = sessionEndsAt, phase == .idle, Date() >= end {
            endSession(reason: L.t("会话闲置超时,已结束", "Session ended after being idle"))
            return
        }
        publish()
    }

    private func startLevelTimer() {
        levelTimer?.invalidate()
        levelTimer = Timer.scheduledTimer(withTimeInterval: 0.15, repeats: true) { [weak self] _ in
            Task { @MainActor in
                guard let self, self.phase == .recording else { return }
                self.state.level = self.sink.takeLevel()
                self.publish()
            }
        }
    }

    private func stopLevelTimer() {
        levelTimer?.invalidate()
        levelTimer = nil
    }

    // MARK: 听写

    /// id 为 "app" 时是应用里的试用,结果放到 [transcript];否则交给键盘。
    func startDictation(id: String) {
        guard sessionActive, phase == .idle else { return }
        guard let config = serverConfig else {
            finish(id: id, text: nil, error: L.t("还没在应用里填服务地址", "No server set up in the app yet"), note: nil)
            return
        }
        let s = DictationSession(config: config)
        s.onStage = { [weak self] stage in
            Task { @MainActor in self?.show(stage, id: id) }
        }
        s.onSegment = { [weak self] _, text in
            Task { @MainActor in
                guard let self, self.requestId == id, !text.isEmpty else { return }
                self.setStatus("…" + String(text.suffix(24)))
            }
        }
        s.onResult = { [weak self] text, note in
            Task { @MainActor in self?.finish(id: id, text: text, error: nil, note: note) }
        }
        s.onError = { [weak self] failure in
            Task { @MainActor in self?.finish(id: id, text: nil, error: failure.message, note: nil) }
        }
        session = s
        requestId = id
        phase = .recording
        state.resultText = nil
        state.resultError = nil
        s.start()
        sink.session = s
        startLevelTimer()
        touchSession()
        setStatus(L.t("正在听…", "Listening…"))
    }

    func stopDictation() {
        guard phase == .recording, let s = session else { return }
        phase = .processing
        stopLevelTimer()
        setStatus(L.t("识别中…", "Transcribing…"))
        // 多录 0.25 秒:人往往是话音刚落就点结束,别把最后一个字切掉。
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) { [weak self] in
            guard let self, self.session === s else { return }
            self.sink.session = nil
            s.finish()
        }
    }

    func cancelDictation() {
        stopLevelTimer()
        sink.session = nil
        session?.cancel()
        session = nil
        requestId = nil
        phase = .idle
        setStatus(L.t("已放弃", "Discarded"))
    }

    private func show(_ stage: DictationSession.Stage, id: String) {
        guard requestId == id else { return }
        switch stage {
        case .connecting: setStatus(L.t("正在听…(连接中)", "Listening… (connecting)"))
        case .streaming: setStatus(L.t("正在听…", "Listening…"))
        case .recordingOffline: setStatus(L.t("正在听…(连接断了,说完后整段上传)", "Listening… (offline, will upload when you stop)"))
        case .uploading: setStatus(L.t("上传中…", "Uploading…"))
        case .transcribing: setStatus(L.t("识别中…", "Transcribing…"))
        case .polishing: setStatus(L.t("整理文字…", "Polishing…"))
        }
    }

    private func finish(id: String, text: String?, error: String?, note: String?) {
        guard requestId == id || session == nil else { return }
        stopLevelTimer()
        sink.session = nil
        session = nil
        requestId = nil
        phase = .idle
        touchSession()
        if id == "app" {
            if let text { transcript += (transcript.isEmpty ? "" : "\n") + text }
        }
        state.resultId = id
        state.resultText = text
        state.resultError = error
        state.resultNote = note
        state.resultAt = Date().timeIntervalSince1970
        setStatus(error ?? note.map { L.t("文字整理没做成,插入的是原文:", "Polishing failed, inserted the raw text: ") + $0 } ?? L.t("完成", "Done"))
    }

    // MARK: 状态

    private func setStatus(_ text: String) {
        status = text
        publish()
    }

    /// 把状态写到共享容器,通知键盘。
    private func publish() {
        state.heartbeat = sessionActive ? Date().timeIntervalSince1970 : 0
        switch phase {
        case .idle: state.phase = .idle
        case .recording: state.phase = .recording
        case .processing: state.phase = .processing
        }
        state.requestId = requestId
        state.status = status
        if phase != .recording { state.level = 0 }
        SharedStore.writeAppState(state)
        DarwinNote.state.post()
    }
}

/// 音频线程和主线程之间交接的东西:当前该把音频交给哪个会话,以及音量。
private final class CaptureSink: @unchecked Sendable {
    private let lock = NSLock()
    private var _session: DictationSession?
    private var _level: Float = 0

    var session: DictationSession? {
        get { lock.lock(); defer { lock.unlock() }; return _session }
        set { lock.lock(); _session = newValue; lock.unlock() }
    }

    func report(level: Float) {
        lock.lock()
        _level = max(level, _level)
        lock.unlock()
    }

    /// 取上次取过之后的最大音量并清零。
    func takeLevel() -> Float {
        lock.lock()
        defer { _level = 0; lock.unlock() }
        return _level
    }
}

/// 麦克风的原生格式(一般是 48 kHz float)→ 服务端要的 16 kHz 单声道 16 位 PCM。
final class PCMConverter: @unchecked Sendable {
    static let target = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 16000, channels: 1, interleaved: true)!

    private let converter: AVAudioConverter

    init?(from format: AVAudioFormat) {
        guard let c = AVAudioConverter(from: format, to: Self.target) else { return nil }
        converter = c
    }

    func convert(_ buffer: AVAudioPCMBuffer) -> Data? {
        let ratio = Self.target.sampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio) + 32
        guard let out = AVAudioPCMBuffer(pcmFormat: Self.target, frameCapacity: capacity) else { return nil }
        var consumed = false
        var error: NSError?
        let status = converter.convert(to: out, error: &error) { _, inputStatus in
            if consumed {
                inputStatus.pointee = .noDataNow
                return nil
            }
            consumed = true
            inputStatus.pointee = .haveData
            return buffer
        }
        guard status != .error, error == nil, out.frameLength > 0, let channel = out.int16ChannelData else { return nil }
        return Data(bytes: channel[0], count: Int(out.frameLength) * 2)
    }

    /// 音量 0...1:按 dBFS 线性映射,-60 dB 以下算 0。和 Android 的 core/Pcm.kt 一样。
    static func level(_ pcm: Data) -> Float {
        let count = pcm.count / 2
        guard count > 0 else { return 0 }
        var sum: Double = 0
        pcm.withUnsafeBytes { raw in
            let samples = raw.bindMemory(to: Int16.self)
            for s in samples.prefix(count) {
                let v = Double(Int16(littleEndian: s)) / 32768
                sum += v * v
            }
        }
        let rms = (sum / Double(count)).squareRoot()
        guard rms > 0 else { return 0 }
        return Float(min(max((20 * log10(rms) + 60) / 60, 0), 1))
    }
}
