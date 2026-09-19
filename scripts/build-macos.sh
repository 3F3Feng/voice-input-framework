#!/usr/bin/env bash
#
# 用本机的开发者证书构建并签名 macOS 客户端。
#
# 为什么需要这个脚本(而不是直接 npm run tauri build):
#
#   1. 不带证书构建时 Tauri 只做 ad-hoc 签名。ad-hoc 签名的身份是 cdhash,
#      每次重新构建都不一样,而 macOS 的 TCC(隐私权限)是按签名身份记录授权的 ——
#      结果就是每构建一次,麦克风/输入监控/辅助功能三项权限全部要重新授予,
#      系统设置里还会堆一串同名条目。用固定证书签名则身份稳定,授权一次长期有效。
#
#   2. 一旦用真证书签名,Tauri 会自动启用 hardened runtime。hardened runtime 下
#      访问受保护资源必须显式声明 entitlement,否则系统「直接拒绝」而不是弹窗询问。
#      本仓库的 gui/src-tauri/Entitlements.plist 已声明 device.audio-input,
#      本脚本会在构建后校验它确实进了产物 —— 少了这一条,麦克风会以一种
#      极难排查的方式失效(不弹窗、不进设置列表、状态直接是 denied)。
#
# 用法:
#   scripts/build-macos.sh                 # 自动挑选可用的签名身份
#   scripts/build-macos.sh --identity <SHA-1 或 证书名>
#   scripts/build-macos.sh --install       # 构建后安装到 /Applications
#   scripts/build-macos.sh --adhoc         # 不用证书,仅 ad-hoc 签名(见上文代价)
#   scripts/build-macos.sh --dmg           # 额外打 .dmg(见下方说明)
#
# 默认只产出 .app。.dmg 只在分发时才需要,而分发本来就还需要公证(notarization);
# 且 Tauri 的 bundle_dmg.sh 会用 osascript 驱动 Finder 排版 DMG 窗口,
# 在没有 Finder 自动化权限的环境(ssh、CI、受限终端)里会直接失败。
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GUI_DIR="$REPO_ROOT/gui"
APP_NAME="Voice Input.app"
BUNDLE_ID="com.voiceinput.app"

IDENTITY=""
DO_INSTALL=0
USE_ADHOC=0
BUNDLES="app"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --identity) IDENTITY="${2:?--identity 需要一个参数}"; shift 2 ;;
    --install)  DO_INSTALL=1; shift ;;
    --adhoc)    USE_ADHOC=1; shift ;;
    --dmg)      BUNDLES="app,dmg"; shift ;;
    -h|--help)  sed -n '2,31p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "未知参数: $1(用 --help 查看用法)" >&2; exit 2 ;;
  esac
done

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[警告]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[错误]\033[0m %s\n' "$*" >&2; exit 1; }

[[ "$(uname -s)" == "Darwin" ]] || die "本脚本仅适用于 macOS。"

# ── 选择签名身份 ────────────────────────────────────────────────────────────
if [[ $USE_ADHOC -eq 1 ]]; then
  warn "使用 ad-hoc 签名:每次构建的身份都会变,三项系统权限每次都要重新授予。"
  unset APPLE_SIGNING_IDENTITY || true
else
  if [[ -z "$IDENTITY" ]]; then
    # 取第一个可用的代码签名身份。同名证书可能有多份(重复下载),
    # 用 SHA-1 指纹而不是证书名,避免 codesign 因同名而报歧义。
    IDENTITY="$(security find-identity -v -p codesigning 2>/dev/null \
                | sed -n 's/^ *[0-9]*) \([0-9A-F]*\) .*/\1/p' | head -1)"
  fi
  if [[ -z "$IDENTITY" ]]; then
    die "找不到可用的代码签名身份。
  · 有 Apple 开发者账号:在 Xcode 里登录账号后会自动安装证书。
  · 没有账号:钥匙串访问 → 证书助理 → 创建证书,
    类型选「代码签名」、身份类型选「自签名根」,然后用 --identity <证书名> 指定。
  · 只想先跑起来:加 --adhoc(代价见脚本头部说明)。"
  fi
  say "签名身份: $IDENTITY"
  export APPLE_SIGNING_IDENTITY="$IDENTITY"
fi

# ── 构建标识 ───────────────────────────────────────────────────────────────
# 每次构建现生成一个 UUID 传给 build.rs。版本号只在发版时才动,而两次发版之间
# 可能有几十次本地构建 —— 没有这个 ID,装到 /Applications 里的产物根本认不出是
# 哪一次构建的。ID 会出现在启动日志、设置界面的「关于」里。
#
# build.rs 声明了 rerun-if-env-changed=VIF_BUILD_ID,所以每换一个 ID 都会真的
# 重新编译进二进制,不会被增量编译跳过。
BUILD_ID="$(uuidgen | tr 'A-Z' 'a-z')"
BUILD_TIME="$(date -u '+%Y-%m-%d %H:%M UTC')"
export VIF_BUILD_ID="$BUILD_ID" VIF_BUILD_TIME="$BUILD_TIME"
say "构建 ID: $BUILD_ID  ($BUILD_TIME)"

# ── 构建 ───────────────────────────────────────────────────────────────────
[[ -d "$GUI_DIR/node_modules" ]] || { say "安装前端依赖..."; (cd "$GUI_DIR" && npm ci); }

say "构建中(release 编译,首次可能需要数分钟)..."
if [[ "$BUNDLES" == *dmg* ]]; then
  warn "打 DMG 需要 Finder 自动化权限(bundle_dmg.sh 用 osascript 排版窗口);
  在 ssh / CI / 受限终端里会失败。失败时改用默认的只打 .app。"
fi
(cd "$GUI_DIR" && npm run tauri build -- --bundles "$BUNDLES")

APP_PATH="$GUI_DIR/src-tauri/target/release/bundle/macos/$APP_NAME"
[[ -d "$APP_PATH" ]] || die "构建产物未找到: $APP_PATH"

# ── 校验 ───────────────────────────────────────────────────────────────────
say "校验产物..."

codesign --verify --deep "$APP_PATH" 2>/dev/null \
  || die "签名校验未通过: $APP_PATH"

# 先把输出整个抓进变量再解析。不要写成 `codesign ... | awk '/x/{print;exit}'`:
# awk 提前 exit 会让上游 codesign 收到 SIGPIPE 而返回非零,配合
# `set -o pipefail` + `set -e` 会导致脚本在这里静默退出。
CS_INFO="$(codesign -dv --verbose=2 "$APP_PATH" 2>&1 || true)"
AUTHORITY="$(printf '%s\n' "$CS_INFO" | sed -n 's/^Authority=//p' | head -1)"
FLAGS="$(printf '%s\n' "$CS_INFO" | sed -n 's/.*flags=\([^ ]*\).*/\1/p' | head -1)"
echo "    签名者 : ${AUTHORITY:-ad-hoc(无证书)}"
echo "    标志   : ${FLAGS:-未知}"

# hardened runtime(flags 含 0x10000)下,没有 audio-input entitlement 的话
# 麦克风会被直接拒绝且不弹窗 —— 这是最难排查的一种失效,必须在这里拦住。
if [[ "$FLAGS" == *"runtime"* ]]; then
  if codesign -d --entitlements - --xml "$APP_PATH" 2>/dev/null \
      | plutil -p - 2>/dev/null | grep -q "com.apple.security.device.audio-input"; then
    echo "    entitlement: com.apple.security.device.audio-input ✓"
  else
    die "已启用 hardened runtime,但产物缺少 com.apple.security.device.audio-input。
  麦克风会被系统直接拒绝(不弹窗、不出现在「系统设置 → 隐私与安全性 → 麦克风」列表里)。
  请检查 gui/src-tauri/Entitlements.plist 以及 tauri.conf.json 的 bundle.macOS.entitlements。"
  fi
fi

if grep -qa "$BUILD_ID" "$APP_PATH/Contents/MacOS/voice-input" 2>/dev/null; then
  echo "    构建 ID: $BUILD_ID ✓"
else
  die "构建 ID 没有进到产物里(增量编译跳过了 build.rs?)。
  界面上会显示上一次构建的 ID,比没有 ID 更容易认错人。
  清一次缓存再来: rm -rf \"$GUI_DIR/src-tauri/target/release/build\""
fi

# 本地构建的产物不带隔离属性(那是浏览器下载时才加的),这里顺带确认一下。
if xattr -p com.apple.quarantine "$APP_PATH" >/dev/null 2>&1; then
  warn "产物带有 com.apple.quarantine,按理不该出现。清除: xattr -dr com.apple.quarantine \"$APP_PATH\""
else
  echo "    隔离属性: 无 ✓"
fi

say "构建完成: $APP_PATH  (build $BUILD_ID)"

# ── 安装 ───────────────────────────────────────────────────────────────────
if [[ $DO_INSTALL -eq 1 ]]; then
  DEST="/Applications/$APP_NAME"
  if pgrep -f "$APP_NAME/Contents/MacOS/" >/dev/null 2>&1; then
    say "退出正在运行的实例..."
    osascript -e 'quit app "Voice Input"' >/dev/null 2>&1 || true
    sleep 2
  fi

  # 签名身份变了的话,旧的 TCC 授权记录已经失效(TCC 按签名身份记录),
  # 留着只会在系统设置里堆同名条目。这里只提示,不自动清除 —— 清除会让
  # 用户下次启动重新授权一遍,应当由用户自己决定时机。
  OLD_AUTH=""
  if [[ -d "$DEST" ]]; then
    OLD_CS="$(codesign -dv --verbose=2 "$DEST" 2>&1 || true)"
    OLD_AUTH="$(printf '%s\n' "$OLD_CS" | sed -n 's/^Authority=//p' | head -1)"
  fi

  say "安装到 $DEST ..."
  rm -rf "$DEST"
  cp -R "$APP_PATH" /Applications/

  # 整个 bundle 被替换后 LaunchServices 的注册会过期,
  # 表现为 open 报 -600(procNotFound)或启动台图标不更新。
  /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister \
    -f "$DEST" >/dev/null 2>&1 || true

  say "安装完成。"
  if [[ -n "$OLD_AUTH" && "$OLD_AUTH" != "${AUTHORITY:-}" ]]; then
    warn "签名身份与上一版不同(旧: ${OLD_AUTH:-ad-hoc})。
  旧的权限授权已随身份失效,建议清掉残留记录后重新授权一次:
      tccutil reset All $BUNDLE_ID"
  fi
  echo
  echo "请从**启动台**打开,不要用终端 open —— 从终端启动会让 macOS 把本应用的"
  echo "权限归属记到终端(或调用方)名下,授权弹窗里显示的也会是那个程序的名字。"
else
  echo
  echo "安装: scripts/build-macos.sh --install"
fi
