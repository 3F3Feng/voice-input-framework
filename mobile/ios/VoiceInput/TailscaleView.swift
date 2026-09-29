import SwiftUI

/// 「从 Tailscale 查找」:第一次填只读凭证,之后一键搜索、点选候选。
struct TailscaleView: View {
    /// 选中一个候选后调用,参数是完整地址。
    let onPick: (String) -> Void
    @Environment(\.dismiss) private var dismiss

    @State private var credentials = TailscaleKeychain.load()
    @State private var clientId = ""
    @State private var clientSecret = ""
    @State private var searching = false
    @State private var progress: (done: Int, total: Int) = (0, 0)
    @State private var results: [DiscoveredServer] = []
    @State private var searched = false
    @State private var error: String?
    @State private var confirmRemove = false

    var body: some View {
        NavigationStack {
            Form {
                if credentials == nil {
                    credentialsSection
                } else {
                    searchSection
                    if searched && error == nil { resultsSection }
                    accountSection
                }
            }
            .navigationTitle(L.t("从 Tailscale 查找", "Find via Tailscale"))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(L.t("关闭", "Close")) { dismiss() }
                }
            }
            .alert(L.t("删除已保存的凭证?", "Remove the saved credentials?"), isPresented: $confirmRemove) {
                Button(L.t("删除", "Remove"), role: .destructive) {
                    TailscaleKeychain.delete()
                    credentials = nil
                    results = []
                    searched = false
                    error = nil
                }
                Button(L.t("取消", "Cancel"), role: .cancel) {}
            }
        }
    }

    // MARK: 第一次:填凭证

    private var credentialsSection: some View {
        Section {
            TextField("Client ID", text: $clientId)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
            SecureField("Client secret", text: $clientSecret)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
            Button(L.t("保存", "Save")) { saveCredentials() }
                .disabled(clientId.trimmingCharacters(in: .whitespaces).isEmpty
                          || clientSecret.trimmingCharacters(in: .whitespaces).isEmpty)
            if let error { Text(error).font(.footnote).foregroundStyle(.red) }
        } header: {
            Text(L.t("Tailscale 凭证", "Tailscale credentials"))
        } footer: {
            Text(L.t(
                "在 Tailscale 管理后台的 OAuth clients 页面新建一个,权限只勾选 devices:core:read(只读设备列表)。凭证只存在这台手机的钥匙串里,只用来读设备名字。不要用能修改 tailnet 的完整密钥。",
                "Create one on the OAuth clients page of the Tailscale admin console and grant only devices:core:read (read-only device list). It's kept in this phone's Keychain and only used to read device names. Don't use a full-access key that can change your tailnet."
            ))
        }
    }

    private func saveCredentials() {
        let c = TailscaleCredentials(
            clientId: clientId.trimmingCharacters(in: .whitespacesAndNewlines),
            clientSecret: clientSecret.trimmingCharacters(in: .whitespacesAndNewlines)
        )
        guard TailscaleKeychain.save(c) else {
            error = L.t("没能存进钥匙串", "Couldn't save to the Keychain")
            return
        }
        error = nil
        credentials = c
        clientId = ""
        clientSecret = ""
        Task { await search() }
    }

    // MARK: 搜索

    private var searchSection: some View {
        Section {
            Button {
                Task { await search() }
            } label: {
                HStack {
                    Label(L.t("查找设备", "Search devices"), systemImage: "magnifyingglass")
                    if searching {
                        Spacer()
                        ProgressView()
                    }
                }
            }
            .disabled(searching)
            if searching && progress.total > 0 {
                Text(L.t("已探测 \(progress.done) / \(progress.total)", "Probed \(progress.done) of \(progress.total)"))
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
            if let error { Text(error).font(.footnote).foregroundStyle(.red) }
        } footer: {
            Text(L.t(
                "会向 tailnet 里的每台设备请求一次 HTTPS 的 /health(端口 443 和 8443),只把回答得像 STT 服务的列出来。手机上的 Tailscale 需要是连接状态。",
                "Requests HTTPS /health once on each device in your tailnet (ports 443 and 8443) and lists only those that answer like the STT service. Tailscale must be connected on this phone."
            ))
        }
    }

    private var resultsSection: some View {
        Section {
            if results.isEmpty {
                Text(L.t("没有找到。确认那台电脑用 tailscale serve 开了 HTTPS,并且 Tailscale 在线。", "None found. Make sure the computer exposes HTTPS with tailscale serve and is online."))
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
            ForEach(results) { server in
                Button {
                    onPick(server.url)
                    dismiss()
                } label: {
                    VStack(alignment: .leading, spacing: 3) {
                        Text(server.deviceName).foregroundStyle(.primary)
                        Text(server.url.replacingOccurrences(of: "https://", with: ""))
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                        Text(detail(server))
                            .font(.caption)
                            .foregroundStyle(server.status == "ok" ? Color.secondary : Color.orange)
                    }
                }
            }
        } header: {
            Text(L.t("候选", "Candidates"))
        }
    }

    private func detail(_ s: DiscoveredServer) -> String {
        var parts: [String] = []
        if !s.os.isEmpty { parts.append(s.os) }
        if let m = s.model, !m.isEmpty { parts.append(m) }
        if let v = s.version, !v.isEmpty { parts.append("v\(v)") }
        if let st = s.status, st != "ok" {
            parts.append(st == "loading" ? L.t("模型加载中", "model loading") : L.t("模型未就绪", "model not ready"))
        }
        return parts.joined(separator: " · ")
    }

    private func search() async {
        guard let credentials, !searching else { return }
        searching = true
        error = nil
        progress = (0, 0)
        defer { searching = false }
        do {
            results = try await TailscaleDiscovery.search(credentials) { done, total in
                Task { @MainActor in progress = (done, total) }
            }
            searched = true
        } catch {
            self.error = error.localizedDescription
            results = []
            searched = true
        }
    }

    // MARK: 账号

    private var accountSection: some View {
        Section {
            Button(L.t("删除已保存的凭证", "Remove saved credentials"), role: .destructive) {
                confirmRemove = true
            }
        }
    }
}
