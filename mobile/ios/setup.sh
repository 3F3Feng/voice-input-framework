#!/usr/bin/env bash
# 一键准备 iOS 工程:找到你的 Team ID,起好 Bundle ID 和 App Group,填进 project.yml,
# 生成 Xcode 工程并打开。之后在 Xcode 里选你的 iPhone、点运行即可。
#
# 用法(在 Mac 上):
#   mobile/ios/setup.sh                       # 全自动
#   mobile/ios/setup.sh A1B2C3D4E5            # 指定 Team ID
#   BUNDLE_ID=com.me.voice mobile/ios/setup.sh  # 指定 Bundle ID
#
# 前提:装了 Xcode,并在 Xcode → Settings → Accounts 里登录过 Apple ID(免费的就行)。
set -euo pipefail
cd "$(dirname "$0")"

say() { printf '\033[1m==> %s\033[0m\n' "$*"; }
die() { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

[ "$(uname)" = Darwin ] || die "要在 Mac 上运行(需要 Xcode)。"
xcode-select -p >/dev/null 2>&1 || die "没找到 Xcode:先从 App Store 装 Xcode 并打开一次。"

# ── 1. XcodeGen ──
if ! command -v xcodegen >/dev/null; then
    command -v brew >/dev/null || die "需要 Homebrew 来装 XcodeGen,见 https://brew.sh"
    say "安装 XcodeGen"
    brew install xcodegen
fi

# ── 2. Team ID ──
# Xcode 登录过的账号和团队记在它的偏好设置里;没有的话再从开发证书里找(证书的 OU 就是 Team ID)。
find_teams() {
    {
        defaults read com.apple.dt.Xcode IDEProvisioningTeamByIdentifier 2>/dev/null || true
        defaults read com.apple.dt.Xcode IDEProvisioningTeams 2>/dev/null || true
    } | sed -n 's/.*teamID = "\{0,1\}\([A-Z0-9]\{10\}\)"\{0,1\};.*/\1/p'

    local dir
    dir=$(mktemp -d)
    security find-certificate -a -c "Apple Development" -p 2>/dev/null |
        awk -v d="$dir" '/BEGIN CERTIFICATE/{n++} n{print > (d "/" n ".pem")}'
    for pem in "$dir"/*.pem; do
        [ -e "$pem" ] || continue
        openssl x509 -in "$pem" -noout -subject 2>/dev/null |
            sed -n 's/.*OU *= *\([A-Z0-9]\{10\}\).*/\1/p'
    done
    rm -rf "$dir"
}

TEAM="${1:-}"
if [ -z "$TEAM" ]; then
    TEAMS=$(find_teams | awk 'NF && !seen[$0]++')
    COUNT=$(printf '%s\n' "$TEAMS" | grep -c . || true)
    if [ "$COUNT" -eq 0 ]; then
        die "没找到 Team ID。先打开 Xcode → Settings → Accounts,点「+」登录 Apple ID,再运行一次。
(也可以直接指定:mobile/ios/setup.sh <Team ID>)"
    elif [ "$COUNT" -eq 1 ]; then
        TEAM="$TEAMS"
    else
        echo "找到多个团队:"
        printf '%s\n' "$TEAMS" | nl
        read -r -p "用第几个? " N
        TEAM=$(printf '%s\n' "$TEAMS" | sed -n "${N}p")
        [ -n "$TEAM" ] || die "没选中。"
    fi
fi
[[ "$TEAM" =~ ^[A-Z0-9]{10}$ ]] || die "Team ID 应该是 10 位大写字母和数字:$TEAM"

# ── 3. Bundle ID / App Group ──
# 默认 io.github.<仓库所有者>.voiceinput;不填就用这个。
if [ -z "${BUNDLE_ID:-}" ]; then
    OWNER=$(git config --get remote.origin.url 2>/dev/null |
        sed -n 's#.*github\.com[:/]\([^/]*\)/.*#\1#p' | tr 'A-Z' 'a-z' | tr -cd 'a-z0-9-')
    BUNDLE_ID="io.github.${OWNER:-$(id -un | tr 'A-Z' 'a-z' | tr -cd 'a-z0-9-')}.voiceinput"
fi
[[ "$BUNDLE_ID" =~ ^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$ ]] || die "Bundle ID 格式不对:$BUNDLE_ID"
GROUP_ID="group.$BUNDLE_ID"

say "Team ID:    $TEAM"
say "Bundle ID:  $BUNDLE_ID(键盘:$BUNDLE_ID.keyboard)"
say "App Group:  $GROUP_ID"

perl -pi -e "
    s/^(\s*DEVELOPMENT_TEAM:).*/\$1 \"$TEAM\"/;
    s/^(\s*BUNDLE_ID_PREFIX:).*/\$1 $BUNDLE_ID/;
    s/^(\s*APP_GROUP_ID:).*/\$1 $GROUP_ID/;
" project.yml

# ── 4. 生成并打开 ──
say "生成 Xcode 工程"
xcodegen generate --quiet
open VoiceInput.xcodeproj

cat <<'EOF'

接下来:
  1. 用数据线连上 iPhone,手机上点「信任此电脑」。
  2. Xcode 顶部选你的 iPhone,按 ⌘R 运行。
     第一次会提示打开「开发者模式」:iPhone 设置 → 隐私与安全性 → 开发者模式,打开后重启。
  3. 装好后如果打不开:iPhone 设置 → 通用 → VPN 与设备管理 → 信任你的开发者证书。
  4. iPhone 设置 → 通用 → 键盘 → 键盘 → 添加新键盘 → 语音输入,
     再点「语音输入」打开「允许完全访问」。
  5. 打开「语音输入」应用,填服务地址和令牌,点「保存并测试连接」。

Xcode 报「…is not available」:Bundle ID 被别人注册过了,换一个再跑:
  BUNDLE_ID=io.github.xxx.voiceinput2 mobile/ios/setup.sh
EOF
