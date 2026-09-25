# 手机输入法(Android / iOS)

在手机上用自己电脑上的 STT + LLM 服务打字:切到「语音输入」键盘,点一下麦克风说话,
再点一下,整理好的文字就插进当前输入框。识别和后处理都在你自己的电脑上跑,手机只负责录音和显示。

> **状态:初版。** Android 的协议部分(`android/core`)有单元测试,并对真的 `/ws/stream`
> 跑过集成测试;Android 应用和 iOS 代码由 CI(`.github/workflows/mobile.yml`)编译,
> 还没在真机上长期用过。

## 怎么工作的

两个平台都只连 **STT 服务**(默认 6544 端口)的 `/ws/stream`,协议和桌面客户端一样:
边录边传,服务端录音期间就分段转写,松手后只剩最后一段;LLM 后处理由 STT 服务转发,
所以 LLM 服务可以继续只绑本机。连接中途断了(换 Wi‑Fi、进电梯),录到的音频都在手机上,
说完后换一条连接整段重发,不丢字。

**Android**:就是一个输入法(`InputMethodService`),自己录音、自己连服务、直接把字插进输入框。
它还声明成了「语音输入法」:AOSP 键盘、HeliBoard、FlorisBoard 这类键盘上的麦克风键可以一键切过来。

**iOS**:系统不让第三方键盘用麦克风,也不让应用在后台**开始**录音。所以和 Typeless、
Wispr Flow 一样分成两半:

```
 键盘扩展(VoiceKeyboard)                   应用(VoiceInput)
 ─────────────────────────                  ──────────────────────────────
 点麦克风 ──(会话没开)── voiceinput://dictate ──▶ 在前台开音频会话,马上开始录
          ──(会话开着)── 「开始」命令 ─────────▶ 把采到的音频发给 STT 服务
 点 ■ ─────────────────── 「结束」命令 ─────────▶ 等结果
 插入文字 ◀──────────────── 结果 ◀──────────────── 写进 App Group 共享容器
```

第一次点麦克风会跳到应用,开始录音后你自己回到原来的应用(点左上角的「◀」),说完在键盘上点 ■。
之后会话一直开着(状态栏有橙色麦克风点,但只有点了麦克风才会把声音发出去),
再点麦克风就不用跳了。会话闲置 5 分钟(可改成 15 / 60 分钟)后自动结束。

## 服务端准备

手机要从网络上连过来,服务默认只绑本机,要改两处(见主 README 的「服务端环境变量」):

```bash
export VIF_STT_HOST=0.0.0.0          # 允许局域网连接
export VIF_API_TOKEN=换成一串随机字符   # 手机上填同一个;LLM 服务也要设同一个值
uv run python -m services.stt_server
```

- 手机里填 `电脑的IP`(如 `192.168.1.10`,会自动补成 `http://192.168.1.10:6544`),或者完整地址。
- **出门在外也想用:推荐 [Tailscale](https://tailscale.com/)。** 电脑和手机都装上,填电脑的
  Tailscale 地址即可,流量是加密的,也不用在路由器上开端口。还可以
  `tailscale serve --bg 6544` 得到一个 `https://<机器名>.<tailnet>.ts.net` 地址,手机上填它。
- **不要**把 6544 端口直接映射到公网。
- 局域网里用 `http://` 时令牌是明文传输的,只在你信任的网络里这样用。
- 不用配 `VIF_CORS_ORIGINS`:CORS 只管浏览器,原生应用不受它限制。

## Android

**要求**:Android 10 及以上。

**构建**:用 Android Studio 打开 `mobile/android`,运行 `app`;或者命令行:

```bash
cd mobile/android
./gradlew :app:assembleDebug        # 产物在 app/build/outputs/apk/debug/
```

CI 每次构建都会把 debug APK 作为 artifact 上传(Actions → Mobile → `voice-input-android-debug`),
下载到手机上直接装也行。

**启用**:打开「语音输入」应用,按三步走 —— 授予麦克风权限 → 在系统设置里启用键盘 →
切换到它。再在下面填服务地址和令牌,点「保存并测试连接」。

**使用**:点一下麦克风开始、再点一下结束;或者按住说话、松手结束。`✕` 放弃这段录音,
`↺` 把上一条结果再插一次,🌐 切换输入法(长按弹出列表)。

## iOS

**要求**:iOS 16 及以上;一台 Mac 和 Xcode 15 以上;Apple ID(免费的个人团队也能装到自己手机上,
但证书 7 天过期,要重新装一次)。

**构建**:

1. 装 XcodeGen:`brew install xcodegen`
2. 改 `mobile/ios/project.yml` 顶部三个值:`DEVELOPMENT_TEAM`(你的团队 ID)、
   `BUNDLE_ID_PREFIX`(别人没用过的 Bundle ID)、`APP_GROUP_ID`(`group.` 开头)
3. `cd mobile/ios && xcodegen generate && open VoiceInput.xcodeproj`,选你的 iPhone,运行

**启用**:

1. 设置 → 通用 → 键盘 → 键盘 → 添加新键盘 → 语音输入
2. 点「语音输入」,打开「允许完全访问」—— 键盘要靠它和应用传话(读写共享容器),
   不会上传你用别的键盘打的字
3. 打开「语音输入」应用,填服务地址和令牌,点「保存并测试连接」

**已知限制**:

- 跳到应用后不能自动回到原来的应用(iOS 没有公开的办法),要自己点左上角「◀」。
- 键盘拉起应用用的是响应链上找 `UIApplication` 再按 selector 调 `openURL` 的办法:
  公开 API 做不到,商业语音键盘都这么做;将来的 iOS 版本可能会改。只自己装着用没有审核问题,
  要上架 App Store 需要自己评估。
- 会话期间状态栏一直有橙色麦克风点。不用时可以在应用里点「结束」。

## 开发

`android/core` 是纯 Kotlin/JVM 模块(协议、断线重发、音量计算),不用 Android SDK 就能测:

```bash
cd mobile/android
./gradlew :core:test
```

`tools/fake_stt_server.py` 起一个**不用模型**的 STT 服务:跑的是真的 `services/stt_server.py`,
只把转写换成「heard 3.2 seconds」、LLM 换成在后面加「 [polished]」。用它可以不装模型就把
手机端整条链路走通,也能跑对真实协议的集成测试:

```bash
# 仓库根目录
uv run --no-project --with fastapi --with 'uvicorn[standard]' --with httpx \
    --with python-multipart --with 'numpy<2' --with scipy \
    python mobile/tools/fake_stt_server.py --host 0.0.0.0 --token secret

# 另一个终端
cd mobile/android
VIF_TEST_SERVER=http://127.0.0.1:6544 VIF_TEST_TOKEN=secret ./gradlew --no-daemon :core:test
```

iOS 的 `VoiceInput/DictationSession.swift` 是 `core/DictationSession.kt` 的直译,改协议时两边一起改。

| 路径 | 内容 |
|------|------|
| `android/core/` | 协议客户端 `DictationSession`、`ServerConfig`、`ServerCheck`、测试 |
| `android/app/` | 输入法服务、键盘界面、设置页 |
| `ios/Shared/` | 应用和键盘共用:共享容器里的状态 / 命令、Darwin 通知 |
| `ios/VoiceInput/` | 应用:音频会话、协议客户端、设置界面 |
| `ios/VoiceKeyboard/` | 键盘扩展 |
| `tools/fake_stt_server.py` | 不用模型的 STT 服务 |
