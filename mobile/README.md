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
 再点一下 ──────────────── 「结束」命令 ─────────▶ 等结果
 插入文字 ◀──────────────── 结果 ◀──────────────── 写进 App Group 共享容器
```

第一次点麦克风会跳到应用,开始录音后你自己回到原来的应用(点左上角的「◀」),说完在键盘上再点一下麦克风(录音中是波形图标)。
之后会话一直开着(状态栏有橙色麦克风点,但只有点了麦克风才会把声音发出去),
再点麦克风就不用跳了。会话闲置 5 分钟(可改成 15 / 60 分钟)后自动结束。

## 服务端准备

**最省事的办法**:桌面客户端里「设置 → 服务 → 手机直连(局域网)」打开开关(本地管理模式)。
它会自动让 STT 服务监听局域网、生成随机令牌、重启服务,并显示手机里该填的地址和令牌。
Windows 上防火墙的放行命令也在同一个面板里。

**自己管服务的话**:手机要从网络上连过来,服务默认只绑本机,要改两处(见主 README 的「服务端环境变量」):

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

**安装(不用应用商店,免费)**:发版后 APK 挂在仓库的 [Releases](../../releases)(标签 `mobile-v*`)上,
用发布密钥签名。可以直接下载安装,也可以用 [Obtainium](https://github.com/ImranR98/Obtainium)
填上本仓库地址,之后新版本由它自动提示更新。

**构建**:用 Android Studio 打开 `mobile/android`,运行 `app`;或者命令行:

```bash
cd mobile/android
./gradlew :app:assembleDebug        # 产物在 app/build/outputs/apk/debug/
```

CI 每次构建都会把 debug APK 作为 artifact 上传(Actions → Mobile → `voice-input-android-debug`),
下载到手机上直接装也行。

**启用**:打开「语音输入」应用,按三步走 —— 授予麦克风权限 → 在系统设置里启用键盘 →
切换到它。再在下面填服务地址和令牌,点「保存并测试连接」。

**使用**:点一下麦克风开始、再点一下结束;或者按住说话、松手结束。键盘顶部一排按钮选识别语言
(自动 / 中 / EN / 粤 / 日 / 한),下一次听写就用它,和应用里的「识别语言」是同一个设置。
录音中「取消」放弃这一段,没在录音时「重插」把上一条结果再插一次,🌐 切换输入法(长按弹出列表)。
这是纯语音键盘,只有麦克风、空格、退格和回车;要打字时切回平时的键盘。

**免手打地址(走 Tailscale 时)**:桌面客户端「设置 → 服务 → 配对手机」显示的二维码,用手机相机扫一下,
选「在 语音输入 中打开」,应用会先弹窗让你确认地址,点「使用」就填好并测试连接。只认 `https://` 地址;
局域网里的 `http://` 地址没有这个功能,手填即可(完整步骤见 [../docs/testing.md](../docs/testing.md))。

## iOS

**要求**:iOS 16 及以上;一台 Mac 和 Xcode 15 以上;Apple ID(免费的个人团队也能装到自己手机上,
但证书 7 天过期,过期前要续一次,见下面「续期」)。

**构建**:

1. 打开 Xcode → Settings → Accounts,点「+」登录 Apple ID(免费的就行)
2. 运行 `mobile/ios/setup.sh`:自动装 XcodeGen、找到你的 Team ID、起好 Bundle ID
   (默认 `io.github.<仓库所有者>.voiceinput`)和 App Group,填进 `project.yml`,生成工程并打开 Xcode
3. 连上 iPhone,在 Xcode 顶部选它,按 ⌘R 运行(第一次要在 iPhone 上打开「开发者模式」)

想手动来也行:改 `mobile/ios/project.yml` 顶部的 `DEVELOPMENT_TEAM`(团队 ID)、
`BUNDLE_ID_PREFIX`(别人没用过的 Bundle ID)、`APP_GROUP_ID`(`group.` 开头),
再 `cd mobile/ios && xcodegen generate && open VoiceInput.xcodeproj`。

**续期(免费 Apple ID)**:免费账号签出来的应用 7 天过期,过期后打不开、键盘也用不了;苹果不给延长,
只能隔几天重新签名、重新装一遍。不用开 Xcode:

```bash
mobile/ios/renew.sh            # 离过期不到 4 天才续,否则什么都不做
mobile/ios/renew.sh --force    # 现在就续
```

它会让苹果发一份新的描述文件(从签发那一刻起再算 7 天),重新编译、签名,装到手机上;覆盖安装,
服务地址、令牌和已经添加的键盘都还在。手机和 Mac 在同一个 Wi‑Fi 下就行,不用插线,锁屏也能装
(iPhone 17 Pro Max / Xcode 27 实测,整个过程十几秒)。手机连不上时退出码是 75,过会儿再跑就行,
所以适合交给定时任务每天跑一两次。前提是跑过 `setup.sh`、用 Xcode 往这台手机上装成功过一次。

**配对(不用手打地址)**:最省事的是桌面客户端:「设置 → 服务 → 配对手机」(本地管理模式)点「显示二维码」,
手机相机扫一下。没有桌面客户端(或想在终端里做)的话,在装了 STT 服务的电脑上运行

```bash
swift mobile/tools/pair.swift
```

它读本机 Tailscale 里指向 STT 服务的 HTTPS 转发,打印一个二维码和一条链接。用手机相机扫二维码
(或把链接发到手机上点开),选「在 语音输入 中打开」,点「使用」,地址就填好并自动测试连接。
没有 HTTPS 转发时它会告诉你先执行哪条 `tailscale serve` 命令。设了 `VIF_API_TOKEN` 的话,令牌也会带在链接里。
配对链接只认 `https://`,并且一定会先弹窗让你确认地址,不会直接生效。

想在 tailnet 里自动找:App 里「从 Tailscale 查找」用一个只读的 OAuth 凭证(`devices:core:read`)列出设备并探测。
比配对麻烦,只在电脑不方便运行脚本时用。

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

## 发布(维护者)

**Android**:推一个 `mobile-vX.Y.Z` 标签,`.github/workflows/android-release.yml` 会构建、用发布密钥签名、
验签,然后把 APK 和它的 SHA-256 挂到这个标签的 GitHub Release 上。发版前:

1. 改 `android/app/build.gradle.kts` 里的 `versionName` 和 `versionCode`。**标签里的版本必须和
   `versionName` 一致**,不一致工作流会直接失败,免得发出去的包和标签对不上。
2. 第一次发版要生成一个发布密钥,并设四个 GitHub secrets(仓库 Settings → Secrets and variables → Actions):

   ```bash
   keytool -genkeypair -v -keystore release.jks -alias voiceinput \
       -keyalg RSA -keysize 4096 -validity 10000
   base64 -i release.jks | pbcopy        # macOS;Linux 用 base64 -w0 release.jks
   ```

   | secret | 内容 |
   |---|---|
   | `ANDROID_KEYSTORE_BASE64` | 上面 base64 的输出 |
   | `ANDROID_KEYSTORE_PASSWORD` | 密钥库密码 |
   | `ANDROID_KEY_ALIAS` | 别名(上面是 `voiceinput`) |
   | `ANDROID_KEY_PASSWORD` | 密钥密码 |

3. `git tag mobile-v0.1.0 && git push origin mobile-v0.1.0`

**这个密钥库要自己另外备份(密码管理器、加密的 U 盘都行),并且不要提交进仓库。** Android 只认
同一个密钥签的更新:丢了它,已经装了的用户就没法升级,只能卸载重装。

想进 [IzzyOnDroid](https://izzyondroid.org/docs/general/AppInclusionPolicy/)(免费的第三方 F-Droid 源,
自动从 GitHub Release 拉 APK):APK 要用发布密钥签名、不能带 debuggable、源码公开,这几条上面的工作流
都已经满足;提交收录申请是往它们的仓库开一个 issue,和本仓库无关。

**iOS**:没有免费的公开分发渠道(TestFlight / App Store 都要付费开发者账号)。目前的方式是用户
按上面「构建」一节自己从源码装,用自己的免费 Apple ID 签名,7 天过期前用 `mobile/ios/renew.sh` 续一次。
AltStore Classic(全球可用,美国、加拿大也行;AltStore PAL 只在欧盟等地区)可以让用户自己续签,
但我们是「应用 + 键盘扩展」两个包并且靠 App Group 通信,重新签名后是否还能正常工作**还没有实测**,
测过之前不要对外承诺支持。

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
| `ios/setup.sh` | 一键填好签名设置、生成并打开 Xcode 工程 |
| `tools/fake_stt_server.py` | 不用模型的 STT 服务 |
