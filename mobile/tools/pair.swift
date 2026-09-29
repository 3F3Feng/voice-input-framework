#!/usr/bin/env swift
// 给手机生成配对链接和二维码:手机上的「语音输入」扫一下(或点开链接)就填好服务地址,
// 不用手打地址,也不需要任何 Tailscale 凭证。
//
//   swift mobile/tools/pair.swift
//
// 它读本机 Tailscale 的信息,找到 `tailscale serve` 里转发到 STT 服务的那个 HTTPS 地址。
// 没找到时会告诉你要执行哪条命令,不会替你改 Tailscale 的配置。
//
// 环境变量:
//   VIF_STT_PORT    STT 服务的本机端口,默认 6544
//   VIF_API_TOKEN   设了的话,会连同令牌一起写进链接(二维码里就带着令牌,别拍给别人)
//
// 仅 macOS(用系统的 CoreImage 画二维码)。

import CoreImage
import Foundation

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(1)
}

// MARK: 找 tailscale

func findTailscale() -> String? {
    let candidates = [
        "/usr/local/bin/tailscale",
        "/opt/homebrew/bin/tailscale",
        "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
    ]
    if let hit = candidates.first(where: { FileManager.default.isExecutableFile(atPath: $0) }) { return hit }
    for dir in (ProcessInfo.processInfo.environment["PATH"] ?? "").split(separator: ":") {
        let path = "\(dir)/tailscale"
        if FileManager.default.isExecutableFile(atPath: path) { return path }
    }
    return nil
}

func run(_ tool: String, _ args: [String]) -> Data? {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: tool)
    p.arguments = args
    let out = Pipe()
    p.standardOutput = out
    p.standardError = Pipe()
    do { try p.run() } catch { return nil }
    let data = out.fileHandleForReading.readDataToEndOfFile()
    p.waitUntilExit()
    return p.terminationStatus == 0 ? data : nil
}

func json(_ data: Data?) -> [String: Any]? {
    guard let data else { return nil }
    return (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
}

guard let tailscale = findTailscale() else {
    fail("没找到 tailscale 命令行。装好 Tailscale 并打开一次,或者把它加进 PATH。")
}
guard let status = json(run(tailscale, ["status", "--json"])),
      let me = status["Self"] as? [String: Any],
      var host = me["DNSName"] as? String else {
    fail("读不到 Tailscale 的状态。确认 Tailscale 已登录并在线。")
}
while host.hasSuffix(".") { host.removeLast() }

// MARK: 找 serve 里指向 STT 服务的那条

let sttPort = ProcessInfo.processInfo.environment["VIF_STT_PORT"] ?? "6544"
var address: String?
if let serve = json(run(tailscale, ["serve", "status", "--json"])),
   let web = serve["Web"] as? [String: Any] {
    // 键形如 "mac.tailnet.ts.net:8443";按端口排,结果稳定。
    for key in web.keys.sorted() {
        guard let entry = web[key] as? [String: Any],
              let handlers = entry["Handlers"] as? [String: Any] else { continue }
        let proxiesToStt = handlers.values.contains { value in
            guard let h = value as? [String: Any], let proxy = h["Proxy"] as? String else { return false }
            return proxy.hasSuffix(":\(sttPort)") || proxy.hasSuffix(":\(sttPort)/")
        }
        guard proxiesToStt else { continue }
        // 443 是 https 默认端口,不写。
        address = key.hasSuffix(":443") ? "https://" + key.dropLast(4) : "https://" + key
        break
    }
}

guard let address else {
    fail("""
    Tailscale 里还没有把 STT 服务(本机 \(sttPort) 端口)暴露成 HTTPS 的转发。先执行:

      tailscale serve --bg --https=8443 http://127.0.0.1:\(sttPort)

    然后再运行本脚本。这条命令只在你的 tailnet 内可见,不会对公网开放。
    (本机的 Tailscale 名字是 \(host))
    """)
}

// MARK: 链接

var components = URLComponents()
components.scheme = "voiceinput"
components.host = "setup"
var query = [URLQueryItem(name: "url", value: address)]
let token = ProcessInfo.processInfo.environment["VIF_API_TOKEN"] ?? ""
if !token.isEmpty { query.append(URLQueryItem(name: "token", value: token)) }
components.queryItems = query
// URLComponents 不会转义 query 里的 `+`,而它在表单里代表空格;令牌里可能有,手动转。
components.percentEncodedQuery = components.percentEncodedQuery?.replacingOccurrences(of: "+", with: "%2B")
guard let link = components.string else { fail("生成不了链接") }

// MARK: 二维码(终端里用半块字符画,黑白背景,深色终端也能扫)

func printQR(_ text: String) {
    guard let filter = CIFilter(name: "CIQRCodeGenerator") else { fail("系统里没有二维码生成器") }
    filter.setValue(Data(text.utf8), forKey: "inputMessage")
    filter.setValue("M", forKey: "inputCorrectionLevel")
    guard let image = filter.outputImage else { fail("二维码生成失败") }

    // 输出是「一个模块一个像素」,没有空白边;自己在四周补 2 个模块的空白。
    let context = CIContext(options: [.useSoftwareRenderer: true])
    guard let cg = context.createCGImage(image, from: image.extent) else { fail("二维码渲染失败") }
    let n = cg.width
    var gray = [UInt8](repeating: 255, count: n * n)
    guard let space = CGColorSpace(name: CGColorSpace.linearGray),
          let bitmap = CGContext(data: &gray, width: n, height: n, bitsPerComponent: 8, bytesPerRow: n,
                                 space: space, bitmapInfo: CGImageAlphaInfo.none.rawValue) else { fail("二维码渲染失败") }
    bitmap.interpolationQuality = .none
    bitmap.draw(cg, in: CGRect(x: 0, y: 0, width: n, height: n))

    let quiet = 2
    let size = n + quiet * 2
    func dark(_ x: Int, _ y: Int) -> Bool {
        let px = x - quiet, py = y - quiet
        guard px >= 0, py >= 0, px < n, py < n else { return false }
        return gray[py * n + px] < 128
    }
    // 每个字符格画上下两个模块:前景色是上面的,背景色是下面的。
    var y = 0
    while y < size {
        var line = ""
        for x in 0..<size {
            let top = dark(x, y), bottom = y + 1 < size ? dark(x, y + 1) : false
            line += "\u{1B}[\(top ? 30 : 37);\(bottom ? 40 : 47)m▀"
        }
        print(line + "\u{1B}[0m")
        y += 2
    }
}

print("")
printQR(link)
print("")
print("用手机相机扫上面的二维码,选「在 语音输入 中打开」,再点「使用」。")
print("地址:\(address)")
if !token.isEmpty { print("注意:链接里带着访问令牌,别把二维码或链接发给别人。") }
print("")
print("不方便扫的话,把下面这行链接发到手机上打开(AirDrop / 备忘录 / 信息):")
print(link)
