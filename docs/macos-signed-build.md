# macOS 本地签名构建

> 一句话:用 `scripts/build-macos.sh --install`,不要手工 `npm run tauri build` 之后自己拷过去。

```bash
scripts/build-macos.sh --install
```

脚本会自动挑选本机可用的签名身份、构建、校验产物、安装到 `/Applications`,
并在签名身份发生变化时提示你重置权限。

装完**从启动台打开**(原因见下文「不要用终端启动」)。

---

## 为什么要签名

macOS 的 TCC(隐私权限)**按代码签名身份记录授权**。本应用需要三项权限
(麦克风、输入监控、辅助功能,见 [macos-permissions.md](macos-permissions.md)),
签名方式直接决定这些授权能不能留住:

| 签名方式 | 身份是否稳定 | 后果 |
|---|---|---|
| ad-hoc(不带证书构建时 Tauri 的默认行为) | ❌ 身份是 cdhash,每次构建都变 | **每构建一次,三项权限全部要重新授予**;系统设置里堆一串同名条目 |
| 自签名证书 | ✅ | 授权一次长期有效(仅本机信任) |
| Apple Development / Developer ID | ✅ | 授权一次长期有效;证书续期也不失效 |

Apple 证书之所以连续期都扛得住,是因为 TCC 实际匹配的是 **designated requirement**
(bundle ID + 证书 CN + Apple 锚点),而不是某一张具体的证书:

```
designated => identifier "com.voiceinput.app" and anchor apple generic
  and certificate leaf[subject.CN] = "Apple Development: ..."
```

## 准备一个签名身份

**已有 Apple 开发者账号** —— 在 Xcode 里登录账号,证书会自动装进钥匙串。确认:

```bash
security find-identity -v -p codesigning
```

**没有账号** —— 钥匙串访问 → 证书助理 → 创建证书,
名称随意、身份类型选「**自签名根**」、证书类型选「**代码签名**」。
之后用 `--identity <证书名>` 指定。

自签名证书同样能解决权限反复失效的问题,只是仅本机信任,不能分发给别人。

## hardened runtime 与 entitlement(重要)

**一旦用真证书签名,Tauri 会自动启用 hardened runtime。** 而 hardened runtime 下,
访问受保护资源必须显式声明 entitlement,否则系统**直接拒绝**,不是弹窗询问。

麦克风缺 `com.apple.security.device.audio-input` 时的表现极具迷惑性:

- `AVCaptureDevice.authorizationStatus` 返回 **denied**
- **不弹**授权窗(压根没到询问那一步)
- 应用**不出现**在「系统设置 → 隐私与安全性 → 麦克风」列表里

三个症状同时出现,很容易误判成"权限代码写错了"。实际上代码没问题,
是 entitlement 没声明。

本仓库的 `gui/src-tauri/Entitlements.plist` 已声明该 entitlement,
通过 `tauri.conf.json` 的 `bundle.macOS.entitlements` 引用。
`scripts/build-macos.sh` 会在构建后**校验它确实进了产物**,缺了直接报错。

> ad-hoc 签名不启用 hardened runtime,所以这个坑只在换成证书之后才会遇到。

## 换了签名身份之后

TCC 的授权跟着旧身份走,换身份后旧记录就失效了(还会在系统设置里留下同名残留)。清理:

```bash
tccutil reset All com.voiceinput.app
```

这是**清除**授权(更严格),不是绕过。清完下次启动会干净地重新弹窗一次,授完即稳定。
脚本在检测到签名身份变化时会提示你执行这一步,但不会自动执行 ——
何时重新授权应当由你决定。

## 不要用终端启动

macOS 有一套 **responsible process** 归属机制:从终端(或任何其它程序)启动 GUI 应用时,
系统会把这次启动"记在发起方名下"。后果是权限弹窗里显示的是**那个程序**的名字,
授权也记到它头上 —— 你之后从启动台正常打开时,会发现权限又没了。

所以:**装完从启动台或访达双击打开。**

## 隔离属性(仅影响下载来的产物)

浏览器下载的文件会被打上 `com.apple.quarantine`。未签名 + 被隔离 = macOS 报
**"已损坏,应移到废纸篓"** —— 文件其实完好,是 Gatekeeper 的提示文案有误导性。

**本地构建的产物不带隔离属性**,所以走本脚本不会遇到。
只有从 GitHub Actions 下载 CI 产物时才需要:

```bash
xattr -dr com.apple.quarantine "/path/to/Voice Input.app"
```

CI 产物还是 ad-hoc 签名(runner 上没有证书),仅适合做跨平台构建验证,不建议日常使用。

## 分发给别人

本文说的是**自用**。要分发给其他人,需要 **Developer ID 证书 + 公证(notarization)**,
否则对方一样会看到"已损坏"。Tauri 支持公证,需要额外提供
`APPLE_ID` / `APPLE_PASSWORD` / `APPLE_TEAM_ID`(或 API Key 那一组)环境变量。
本脚本不做公证,构建日志里会有一行 `skipping app notarization` 提示。

## 常见问题

**`open` 报 `-600 procNotFound`** —— 整个 bundle 被替换后 LaunchServices 注册过期。
脚本的 `--install` 已经会自动 `lsregister -f`;手工拷贝的话自己补一次:

```bash
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "/Applications/Voice Input.app"
```

**找不到签名身份** —— 见上文「准备一个签名身份」;想先跑起来可以用 `--adhoc`,
但要接受权限每次重新授予。

**权限授了却显示未授权** —— 辅助功能常见需要**重启应用**才生效,退出重开一次。

## 和发布构建的关系

这个脚本产出的是**给你自己装的**包,不是发布包。两者有一处刻意的不同:

脚本用 `--config src-tauri/tauri.no-updater.conf.json` 关掉了更新器产物
(`.app.tar.gz` 和它的 minisign 签名)。原因是 `tauri.conf.json` 里配了 updater
`pubkey`,只要 `createUpdaterArtifacts` 开着,tauri 就**要求**必须有
`TAURI_SIGNING_PRIVATE_KEY`,否则直接报错:

```
A public key has been found, but no private key.
```

本地构建没有理由要求开发者手里有发布签名私钥,所以关掉。发布签名只在
`.github/workflows/build-release.yml` 里做,密钥存在仓库 secrets 里。

## 发版签名(CI 里的 macOS 代码签名)

发版流水线的 macOS 包**也必须用固定证书签名**,和本地构建用同一个身份。

v2.3.0 之前流水线里没有证书,发出去的 `.app` 只有链接器给的 ad-hoc 签名
(`Signature=adhoc`、identifier 是 `voice_input-<随机>`、designated requirement 是 cdhash)。
用户点「更新」装上之后:系统设置里 Voice Input 的开关还开着,但那条授权属于旧的
签名身份,和新的二进制对不上 —— 应用里「输入监控」「辅助功能」显示被拒,快捷键和
自动输入失效,而且每次更新都会再来一遍。

用证书签名后,designated requirement 是这样的,不含任何随构建变化的东西:

```
identifier "com.voiceinput.app" and anchor apple generic
  and certificate leaf[subject.CN] = "Apple Development: <你的名字> (<ID>)"
  and certificate 1[field.1.2.840.113635.100.6.2.1]
```

只认 bundle id 和证书名,所以同名的另一张证书、续期后的新证书签出来的都算同一个
应用;本地 `scripts/build-macos.sh` 签的包和流水线签的包也算同一个,授权可以互通。

### 配置(一次性,需要仓库管理员自己做)

1. 钥匙串访问 → 我的证书 → 找到本地签名用的那张「Apple Development: …」(带私钥的,
   展开能看到一把钥匙)→ 右键「导出」→ 格式选「个人信息交换(.p12)」,设一个导出密码。
2. 在终端里把 .p12 转成 base64 并存成仓库 secret(`gh` 需要已登录,有仓库管理权限):

   ```bash
   base64 -i ~/Desktop/VoiceInput-signing.p12 | gh secret set APPLE_CERTIFICATE
   gh secret set APPLE_CERTIFICATE_PASSWORD      # 粘贴上一步设的导出密码
   gh secret set APPLE_SIGNING_IDENTITY --body "Apple Development: <你的名字> (<ID>)"
   rm ~/Desktop/VoiceInput-signing.p12
   ```

   `APPLE_SIGNING_IDENTITY` 填证书的完整名称,和 `security find-identity -v -p codesigning`
   列出来的引号里那一串一致。

3. 之后打 tag 发版即可。流水线在构建前检查这三个 secret,缺了直接失败(不会再退回
   ad-hoc 悄悄发版);构建后校验签名用的是证书、identifier 是 `com.voiceinput.app`、
   designated requirement 不是 cdhash、`device.audio-input` entitlement 在,且
   `.app.tar.gz`(更新器装的就是它)里的也是签过名的那一份。

这张证书不是 Developer ID,不能公证:浏览器下载的包第一次打开仍要按
「隔离属性」一节处理。但通过应用内更新安装的不受隔离属性影响,权限也不会再丢。

Apple Development 证书一年一续。续期后把新导出的 .p12 重新存进 `APPLE_CERTIFICATE`
即可;证书名不变,已经给出的授权继续有效。

## 发版怎么走

版本号**只有一个来源**:`gui/src-tauri/Cargo.toml` 的 `package.version`。
`tauri.conf.json` 不写 `version`(tauri 缺省回落到 Cargo.toml)。

```bash
# 1. 改版本号(只改这一处)+ 写 CHANGELOG
#    client/__init__.py 的 __version__ 也要跟上,发版流水线会校验
# 2. 打 tag 推上去
git tag v2.2.0 && git push origin v2.2.0
```

流水线会在这些情况下**主动失败**,而不是发出一个装不上的版本:

| 情况 | 为什么必须拦住 |
|------|----------------|
| tag 和 `Cargo.toml` / `client/__init__.py` 的版本号对不上 | 客户端自报的版本和清单里的对不上,会陷入「发现新版本 → 更新 → 还是老版本」的死循环 |
| 构建没产出任何 `.sig` | 签名为空的 `latest.json` 会让每个客户端的更新都失败,且报错和「没网」长得一样 |
| 私钥和 `tauri.conf.json` 里的 pubkey 不是一对 | tauri 本身只警告一句就照常出包,签出来的更新包客户端一个都验不过 |
| 某个平台缺更新产物或签名 | 同上,那个平台的用户会一直更新失败 |

### 各平台的更新产物

这张表是**在 CI 上真跑了一遍三平台构建、把产物拉下来数出来的**,不是照文档抄的
—— 其中 Linux 那两行和直觉不一样,照直觉写会坏:

| 平台 | 给人下载的 | 更新器用的 | `latest.json` 的 key |
|------|-----------|-----------|---------------------|
| macOS | `...aarch64.dmg` | `Voice Input.app.tar.gz` + `.sig` | `darwin-aarch64` |
| Linux (AppImage) | `...amd64.AppImage` | **同一个文件** + `.sig` | `linux-x86_64` |
| Linux (deb) | `...amd64.deb` | **同一个文件** + `.sig` | `linux-x86_64-deb` |
| Windows | `...x64-setup.exe` | **同一个文件** + `.sig` | `windows-x86_64` |

几个容易踩的点:

- **Linux 没有 `.AppImage.tar.gz`。** tauri 2.10 直接给 AppImage 本体签名。
  (插件的 `install_appimage()` 两种都吃:字节流是 gz 就解包,不是就整个覆盖写回去。)
- **`.deb` 必须单独挂一条。** 插件在 Linux 上按**安装方式**分派:用 deb 装的走
  `install_deb()`,那个函数会先校验字节流是不是 deb,拿到 AppImage 只会报
  `InvalidUpdaterFormat`。插件查清单的顺序是 `{os}-{arch}-{installer}` 再回落
  `{os}-{arch}`,所以 `linux-x86_64-deb` 这条是 deb 用户能不能自动更新的全部依靠。
  (装 deb 需要 root,插件会依次尝试 pkexec → zenity/kdialog → sudo。)
- **只有 macOS 的更新产物是独立文件**,另外三个都是安装包本体兼做更新产物。
- **collect 那一步不能写 `-name "*.tar.gz"`。** Linux 的 bundle 目录里还躺着
  deb 拆出来的 `control.tar.gz` / `data.tar.gz`(7MB),会被一并发成 release 资产。
- `targets: "all"` 还会产出 `.msi`(Windows)和 `.rpm`(Linux)。这两个不发布,
  但它们各自带一个 `.sig`,collect 之后要把这种「主文件没收进来」的孤儿签名清掉。

### 怎么自己验一遍签名

不用等发版。把某次构建的产物拉下来,用配置里的 pubkey 验:

```bash
gh run download <run-id> -n tauri-ubuntu-latest -D /tmp/a
python3 - <<'EOF'
import base64, hashlib, json
from nacl.signing import VerifyKey          # pip install pynacl
pk = json.load(open('gui/src-tauri/tauri.conf.json'))['plugins']['updater']['pubkey']
raw = base64.b64decode(base64.b64decode(pk).decode().strip().splitlines()[1])
vk  = VerifyKey(raw[10:42])
f   = '/tmp/a/Voice Input_2.2.0_amd64.AppImage'
sig = base64.b64decode(open(f + '.sig','rb').read()).decode().strip().splitlines()[1]
sig = base64.b64decode(sig)
data = open(f,'rb').read()
msg  = hashlib.blake2b(data, digest_size=64).digest() if sig[:2] == b'ED' else data
vk.verify(msg, sig[10:74]); print('签名验证通过')
EOF
```

### 轮换签名密钥

```bash
cd gui && npx tauri signer generate -w ~/.tauri/vif.key
```

把私钥内容存成仓库 secret `TAURI_SIGNING_PRIVATE_KEY`(有密码的话再存
`TAURI_SIGNING_PRIVATE_KEY_PASSWORD`),把 `~/.tauri/vif.key.pub` 的内容填进
`tauri.conf.json` 的 `plugins.updater.pubkey`。两者必须同时换 ——
**换了 pubkey 之后,用旧密钥签的历史版本就升不上来了**,老用户需要手动装一次新包。
