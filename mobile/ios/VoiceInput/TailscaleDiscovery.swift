import Foundation
import Security

// 从 Tailscale 查找 STT 服务:用一个只读的 OAuth 客户端(scope: devices:core:read)向 Tailscale 要
// tailnet 里的设备列表,再逐台探测 HTTPS 上的 `/health`,能回答的就是候选。
//
// - 只用 HTTPS(`tailscale serve` 给的证书对完整的 MagicDNS 名字有效),端口试 443 和 8443;
// - 凭证只存在 Keychain,只有应用读得到,键盘扩展拿不到;
// - 只在用户点「查找」时才联网,不在后台扫描。

struct TailscaleCredentials: Codable, Equatable {
    var clientId: String
    var clientSecret: String
}

enum TailscaleKeychain {
    private static let service = "tailscale-oauth"
    private static let account = "client"

    private static var query: [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
    }

    static func load() -> TailscaleCredentials? {
        var q = query
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var out: AnyObject?
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess,
              let data = out as? Data else { return nil }
        return try? JSONDecoder().decode(TailscaleCredentials.self, from: data)
    }

    @discardableResult
    static func save(_ c: TailscaleCredentials) -> Bool {
        guard let data = try? JSONEncoder().encode(c) else { return false }
        delete()
        var q = query
        q[kSecValueData as String] = data
        // 首次解锁后可读,且不随备份迁到别的设备。
        q[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        return SecItemAdd(q as CFDictionary, nil) == errSecSuccess
    }

    static func delete() {
        SecItemDelete(query as CFDictionary)
    }
}

/// 探测到的一台 STT 服务。
struct DiscoveredServer: Identifiable, Equatable {
    /// 填进「服务地址」的完整地址,如 `https://mac.tailnet.ts.net:8443`。
    let url: String
    let deviceName: String
    let os: String
    let model: String?
    let version: String?
    /// 服务端说自己还没准备好(模型没加载 / 加载中)。
    let status: String?

    var id: String { url }
}

enum TailscaleDiscovery {
    enum Failure: LocalizedError {
        case rejected
        case network(String)
        case badResponse

        var errorDescription: String? {
            switch self {
            case .rejected:
                return L.t(
                    "Tailscale 拒绝了这个凭证。检查 Client ID / Secret,以及它是否有 devices:core:read 权限",
                    "Tailscale rejected the credentials. Check the client ID / secret and that it has the devices:core:read scope"
                )
            case .network(let m):
                return L.t("连不上 Tailscale", "Can't reach Tailscale") + ": " + m
            case .badResponse:
                return L.t("Tailscale 返回的内容看不懂", "Unexpected response from Tailscale")
            }
        }
    }

    private struct TokenResponse: Decodable {
        let access_token: String
    }

    private struct DeviceList: Decodable {
        let devices: [Device]
    }

    private struct Device: Decodable {
        let name: String
        let hostname: String?
        let os: String?
    }

    /// 探测哪些端口。443 是 `tailscale serve --bg 6544` 的默认,8443 是常见的自选端口。
    static let ports = [443, 8443]
    private static let maxDevices = 60
    private static let probeTimeout: TimeInterval = 4

    /// 找出 tailnet 里跑着 STT 服务的设备。`progress` 报告「已探测 / 总数」。
    static func search(
        _ credentials: TailscaleCredentials,
        progress: @escaping @Sendable (Int, Int) -> Void
    ) async throws -> [DiscoveredServer] {
        let token = try await accessToken(credentials)
        let devices = try await listDevices(token: token)
        let targets = devices.prefix(maxDevices).flatMap { device in
            ports.map { (device, $0) }
        }

        var found: [DiscoveredServer] = []
        var done = 0
        progress(0, targets.count)
        await withTaskGroup(of: DiscoveredServer?.self) { group in
            for (device, port) in targets {
                group.addTask { await probe(device, port: port) }
            }
            for await result in group {
                done += 1
                progress(done, targets.count)
                if let result { found.append(result) }
            }
        }
        // 同一台设备两个端口都通时 443 排前面;不同设备按名字排,结果稳定。
        return found.sorted { $0.deviceName == $1.deviceName ? $0.url < $1.url : $0.deviceName < $1.deviceName }
    }

    // MARK: Tailscale API

    private static func accessToken(_ c: TailscaleCredentials) async throws -> String {
        guard let url = URL(string: "https://api.tailscale.com/api/v2/oauth/token") else { throw Failure.badResponse }
        var request = URLRequest(url: url, timeoutInterval: 15)
        request.httpMethod = "POST"
        request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        request.httpBody = formEncode([
            "grant_type": "client_credentials",
            "client_id": c.clientId,
            "client_secret": c.clientSecret,
        ]).data(using: .utf8)

        let (data, status) = try await send(request)
        if status == 401 || status == 403 || status == 400 { throw Failure.rejected }
        guard status == 200, let token = try? JSONDecoder().decode(TokenResponse.self, from: data) else {
            throw Failure.badResponse
        }
        return token.access_token
    }

    private static func listDevices(token: String) async throws -> [Device] {
        // `-` 是「这个凭证所属的 tailnet」。
        guard let url = URL(string: "https://api.tailscale.com/api/v2/tailnet/-/devices") else { throw Failure.badResponse }
        var request = URLRequest(url: url, timeoutInterval: 15)
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")

        let (data, status) = try await send(request)
        if status == 401 || status == 403 { throw Failure.rejected }
        guard status == 200, let list = try? JSONDecoder().decode(DeviceList.self, from: data) else {
            throw Failure.badResponse
        }
        return list.devices
    }

    private static func send(_ request: URLRequest) async throws -> (Data, Int) {
        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            return (data, (response as? HTTPURLResponse)?.statusCode ?? 0)
        } catch {
            throw Failure.network(error.localizedDescription)
        }
    }

    private static func formEncode(_ fields: [String: String]) -> String {
        // 表单里 + & = 等要转义;只放行字母数字和 -._~。
        let allowed = CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~")
        return fields
            .map { key, value in
                "\(key)=\(value.addingPercentEncoding(withAllowedCharacters: allowed) ?? "")"
            }
            .sorted()
            .joined(separator: "&")
    }

    // MARK: 探测

    /// 只请求 `/health`。返回内容要像我们的服务(有 `current_model` 或 `app_version`),
    /// 免得把 tailnet 里别的 HTTPS 网页当成候选。
    private static func probe(_ device: Device, port: Int) async -> DiscoveredServer? {
        // API 给的是完整 MagicDNS 名,有时带结尾的点。
        var host = device.name
        while host.hasSuffix(".") { host.removeLast() }
        guard !host.isEmpty else { return nil }

        let base = port == 443 ? "https://\(host)" : "https://\(host):\(port)"
        guard let url = URL(string: base + "/health") else { return nil }
        var request = URLRequest(url: url, timeoutInterval: probeTimeout)
        request.setValue(L.english ? "en" : "zh-CN", forHTTPHeaderField: "Accept-Language")

        guard let (data, response) = try? await URLSession.shared.data(for: request),
              (response as? HTTPURLResponse)?.statusCode == 200,
              let body = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              body["current_model"] != nil || body["app_version"] != nil else { return nil }

        return DiscoveredServer(
            url: base,
            deviceName: device.hostname?.isEmpty == false ? device.hostname! : host,
            os: device.os ?? "",
            model: body["current_model"] as? String,
            version: body["app_version"] as? String,
            status: body["status"] as? String
        )
    }
}
