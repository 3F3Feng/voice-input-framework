# macOS 权限(TCC)

Tauri GUI 客户端在 macOS 上需要三项系统权限,缺任何一项都会让某个功能**静默失效**。
实现见 `gui/src-tauri/src/permissions.rs`,UI 在设置面板的「系统权限」区块。

| 权限 | 缺失时的症状 | 使用它的代码 | 查询 | 申请 |
|------|--------------|--------------|------|------|
| 麦克风 Microphone | 录不到声音 / 只录到静音 | `audio.rs`(cpal) | `AVCaptureDevice.authorizationStatusForMediaType:` | `requestAccessForMediaType:completionHandler:` |
| 输入监控 Input Monitoring | 全局快捷键完全不响应(`CGEventTapCreate` 失败) | `hotkey.rs`(CGEventTap) | `IOHIDCheckAccess(kIOHIDRequestTypeListenEvent)` | `IOHIDRequestAccess(kIOHIDRequestTypeListenEvent)` |
| 辅助功能 Accessibility | 转录成功、日志正常,但目标窗口里**不出现任何文字** | `input.rs`(enigo 模拟键盘) | `AXIsProcessTrusted()` | `AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: true})` |

## 用途说明(Info.plist)

`gui/src-tauri/Info.plist` 提供 `NSMicrophoneUsageDescription`。Tauri 打包 macOS
`.app` 时会自动把 `tauri.conf.json` 同级目录下的 `Info.plist` 合并进生成的
Info.plist(也可以用 `bundle.macOS.infoPlist` 另外指定一个文件)。

缺这个 key 时,进程第一次访问麦克风会被系统直接拒绝、甚至被杀掉,并且**不会**弹出授权窗口。
输入监控和辅助功能没有对应的 Info.plist key,只能在运行时申请。

## 什么时候申请

不在启动时一次性弹三个窗,而是各自在真正需要的时刻申请:

- **输入监控**:启动时(`lib.rs` 的 `setup`)。全局快捷键监听器紧接着就要创建
  CGEventTap,没有权限直接创建失败。只在状态为「从未询问」时弹窗——已拒绝的话
  系统本来也不会再弹,每次启动都调用只会骚扰用户。
- **麦克风**:第一次开始录音时(`start_recording_internal`)。
- **辅助功能**:第一次自动输入文字时(`input.rs::type_text`),且进程生命周期内只弹一次。

任何一项被拒绝后,系统不再弹窗,只能引导用户打开对应面板:

```
x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone
x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent
x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility
```

## ad-hoc 签名的权限会反复失效

macOS 把 TCC 授权记录绑定在**代码签名身份**上,而不是绑在路径或 bundle id 上。

`codesign --force --sign -`(ad-hoc 签名)每次都会生成一个新的、不稳定的签名标识。
因此:

- 每次重新 ad-hoc 签名后,系统认为这是"另一个"程序,之前授予的麦克风 / 输入监控 /
  辅助功能权限全部失效,需要重新授权;
- CI 产出的每个构建都要重新授权一次,老的条目会以重复项的形式堆在系统设置列表里
  (可以手动删掉);
- 用固定的 Developer ID 证书签名后签名标识稳定,授权就能跨版本保留。

本地开发时如果发现"昨天还好好的权限今天又没了",通常就是重新签名导致的,不是代码问题。
