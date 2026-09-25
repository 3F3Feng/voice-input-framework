import SwiftUI
import UIKit

struct ContentView: View {
    @EnvironmentObject private var c: DictationController
    @State private var urlField = ""
    @State private var checkResult = ""
    @State private var checking = false

    var body: some View {
        NavigationStack {
            Form {
                if c.phase == .recording && !c.transcriptMode {
                    recordingBanner
                }
                sessionSection
                serverSection
                setupSection
                trySection
            }
            .navigationTitle(L.t("语音输入", "Voice Input"))
            .onAppear { urlField = c.serverUrl }
        }
    }

    /// 从键盘拉起来、正在录音时顶上那一块:告诉用户回去。
    private var recordingBanner: some View {
        Section {
            VStack(alignment: .leading, spacing: 8) {
                Label(c.status, systemImage: "mic.fill")
                    .font(.headline)
                    .foregroundStyle(.red)
                Text(L.t(
                    "说吧。说完回到刚才的应用(点左上角的「◀」),在键盘上点 ■ 结束。",
                    "Go ahead and talk. When you're done, go back to the previous app (tap ◀ in the top-left corner) and tap ■ on the keyboard."
                ))
                .font(.subheadline)
            }
            .padding(.vertical, 4)
        }
    }

    private var sessionSection: some View {
        Section {
            HStack {
                Image(systemName: c.sessionActive ? "waveform.circle.fill" : "waveform.circle")
                    .foregroundStyle(c.sessionActive ? .green : .secondary)
                VStack(alignment: .leading) {
                    Text(c.sessionActive ? L.t("会话进行中", "Session active") : L.t("会话未开始", "No session"))
                    if let end = c.sessionEndsAt, c.sessionActive {
                        Text(L.t("闲置到 ", "Ends if idle at ") + end.formatted(date: .omitted, time: .shortened))
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    if !c.status.isEmpty {
                        Text(c.status).font(.caption).foregroundStyle(.secondary)
                    }
                }
                Spacer()
                if c.sessionActive {
                    Button(L.t("结束", "End")) { c.endSession() }
                } else {
                    Button(L.t("开始", "Start")) { Task { await c.startSession() } }
                }
            }
            Picker(L.t("闲置多久后结束", "End after idle for"), selection: $c.sessionMinutes) {
                ForEach([5, 15, 60], id: \.self) { m in
                    Text(L.t("\(m) 分钟", "\(m) min")).tag(m)
                }
            }
        } header: {
            Text(L.t("会话", "Session"))
        } footer: {
            Text(L.t(
                "iOS 不让键盘用麦克风,也不让应用在后台开始录音。所以由应用开一个会话:会话期间状态栏会有橙色麦克风点,但只有在键盘上点了麦克风时才会把声音发给服务。",
                "iOS doesn't let keyboards use the microphone, or apps start recording in the background. So the app keeps a session open: the orange mic dot shows while it's active, but audio is only sent to the server after you tap the mic on the keyboard."
            ))
        }
    }

    private var serverSection: some View {
        Section {
            TextField(L.t("例如 192.168.1.10 或 https://mac.tailnet.ts.net", "e.g. 192.168.1.10 or https://mac.tailnet.ts.net"), text: $urlField)
                .keyboardType(.URL)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
            SecureField(L.t("访问令牌(服务端没设就留空)", "Access token (empty if not set)"), text: $c.token)
            Picker(L.t("识别语言", "Language"), selection: $c.language) {
                ForEach(ServerConfig.languages, id: \.self) { Text(languageName($0)).tag($0) }
            }
            Button {
                saveAndTest()
            } label: {
                HStack {
                    Text(L.t("保存并测试连接", "Save & test connection"))
                    if checking { Spacer(); ProgressView() }
                }
            }
            if !checkResult.isEmpty {
                Text(checkResult).font(.footnote)
            }
        } header: {
            Text(L.t("识别服务", "STT service"))
        } footer: {
            Text(L.t(
                "填运行 STT 服务(默认端口 6544)那台电脑的地址。服务端要设 VIF_STT_HOST=0.0.0.0;建议同时设 VIF_API_TOKEN 并在这里填同一个令牌。出门在外也想用,推荐 Tailscale。",
                "The address of the computer running the STT service (port 6544 by default). The server needs VIF_STT_HOST=0.0.0.0; also set VIF_API_TOKEN and enter the same token here. For use away from home, Tailscale is recommended."
            ))
        }
    }

    private var setupSection: some View {
        Section {
            Text(L.t(
                "1. 设置 → 通用 → 键盘 → 键盘 → 添加新键盘 → 语音输入",
                "1. Settings → General → Keyboard → Keyboards → Add New Keyboard → Voice Input"
            ))
            Text(L.t(
                "2. 点「语音输入」,打开「允许完全访问」(键盘要靠它和本应用传话,不会上传你打的字)",
                "2. Tap Voice Input and turn on Allow Full Access (the keyboard needs it to talk to this app; it doesn't upload what you type)"
            ))
            Button(L.t("打开本应用的设置", "Open app settings")) {
                if let url = URL(string: UIApplication.openSettingsURLString) {
                    UIApplication.shared.open(url)
                }
            }
            HStack {
                Text(L.t("麦克风权限", "Microphone"))
                Spacer()
                switch c.micPermission {
                case .granted: Text("✓").foregroundStyle(.green)
                case .denied: Text(L.t("已拒绝,去设置里打开", "Denied: enable in Settings")).foregroundStyle(.red)
                default: Button(L.t("授权", "Grant")) { Task { await c.requestMicPermission() } }
                }
            }
        } header: {
            Text(L.t("启用键盘", "Enable the keyboard"))
        }
    }

    private var trySection: some View {
        Section {
            TextEditor(text: $c.transcript)
                .frame(minHeight: 100)
            Button {
                Task { await toggleAppDictation() }
            } label: {
                Label(
                    c.phase == .recording && c.transcriptMode ? L.t("结束", "Stop") : L.t("在这里说一句试试", "Try dictating here"),
                    systemImage: c.phase == .recording && c.transcriptMode ? "stop.circle.fill" : "mic.circle.fill"
                )
            }
            .disabled(c.phase == .processing)
        } header: {
            Text(L.t("试一试", "Try it"))
        }
    }

    private func toggleAppDictation() async {
        if c.phase == .recording {
            c.stopDictation()
        } else if await c.startSession() {
            c.startDictation(id: "app")
        }
    }

    private func saveAndTest() {
        guard let url = ServerConfig.normalizeUrl(urlField) else {
            checkResult = L.t("地址不对,例如 192.168.1.10", "That's not a valid address, e.g. 192.168.1.10")
            return
        }
        urlField = url
        c.serverUrl = url
        guard let config = c.serverConfig else { return }
        checking = true
        checkResult = ""
        Task {
            let r = await ServerCheck.run(config)
            checkResult = (r.ok ? "✓ " : "✗ ") + r.message
            checking = false
        }
    }

    private func languageName(_ code: String) -> String {
        switch code {
        case "auto": return L.t("自动检测", "Auto-detect")
        case "zh": return L.t("中文", "Chinese")
        case "en": return "English"
        case "yue": return L.t("粤语", "Cantonese")
        case "ja": return "日本語"
        case "ko": return "한국어"
        default: return code
        }
    }
}
