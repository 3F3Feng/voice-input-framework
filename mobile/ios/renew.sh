#!/usr/bin/env bash
# 给装在 iPhone 上的应用续期:重新签名、重新装一遍。
#
# 免费的 Apple ID 签出来的应用 7 天过期,过期后打不开、键盘也用不了。苹果不给延长,
# 只能隔几天重签一次。这个脚本把这件事做完,适合交给定时任务(launchd)每天跑一次:
# 离过期还远就什么都不做;手机不在线就退出,等下一次。
#
# 用法(先跑过一次 setup.sh,并且用 Xcode 往手机上装成功过):
#   mobile/ios/renew.sh            # 离过期不到 RENEW_BEFORE_DAYS 天才续
#   mobile/ios/renew.sh --force    # 现在就续
#
# 可调的环境变量:
#   VIF_IOS_DEVICE     手机的 UDID(默认:第一台配对过的 iPhone / iPad)
#   RENEW_BEFORE_DAYS  离过期不到几天就续(默认 4)
#
# 手机和 Mac 在同一个 Wi‑Fi 下就行,不用插线,锁屏也能装(实测过)。
#
# 退出码:0 续好了或者还不用续;75 这次做不了(手机不在线),下次再试;其余是出错了。
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
cd "$(dirname "$0")"

say() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
die() { say "$*" >&2; exit 1; }

FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1
RENEW_BEFORE_DAYS="${RENEW_BEFORE_DAYS:-4}"

WORK="$HOME/Library/Caches/voice-input-framework-ios"
STAMP="$WORK/expires-at"
PROFILES="$HOME/Library/Developer/Xcode/UserData/Provisioning Profiles"
mkdir -p "$WORK"

# ── 1. 还用不用续 ──
now=$(date +%s)
if [ "$FORCE" = 0 ] && [ -f "$STAMP" ]; then
    left=$(( $(cat "$STAMP") - now ))
    if [ "$left" -gt $(( RENEW_BEFORE_DAYS * 86400 )) ]; then
        say "还有 $(( left / 3600 )) 小时才过期,不用续。"
        exit 0
    fi
fi

BUNDLE=$(sed -n 's/^ *BUNDLE_ID_PREFIX: *//p' project.yml | tr -d '"' | head -1)
[ -n "$BUNDLE" ] || die "project.yml 里没有 BUNDLE_ID_PREFIX。"
case "$BUNDLE" in com.example.*) die "project.yml 还没填:先跑 mobile/ios/setup.sh。" ;; esac

# ── 2. 手机在不在 ──
DEVICE="${VIF_IOS_DEVICE:-}"
xcrun devicectl list devices --json-output "$WORK/devices.json" >/dev/null 2>&1 ||
    die "xcrun devicectl 跑不了(要 Xcode 15 以上)。"
DEVICE=$(/usr/bin/python3 - "$WORK/devices.json" "$DEVICE" <<'EOF'
import json, sys

want = sys.argv[2]
for d in json.load(open(sys.argv[1]))["result"]["devices"]:
    hw, conn = d.get("hardwareProperties", {}), d.get("connectionProperties", {})
    if hw.get("reality") != "physical" or hw.get("platform") != "iOS":
        continue
    if want and hw.get("udid") != want:
        continue
    # 配对过、现在连得上(插着线或者在同一个网络里)
    if conn.get("pairingState") == "paired" and conn.get("transportType"):
        print(hw["udid"])
        break
EOF
)
if [ -z "$DEVICE" ]; then
    say "手机现在连不上(不在同一个 Wi‑Fi,或者没配对过),下次再试。"
    exit 75
fi

# ── 3. 让苹果发新的描述文件 ──
# 本地缓存的那份没过期时 Xcode 会一直用它(实测:直接重新编译,过期时间不变)。
# 先把这个应用的挪走,编译时才会去要一份新的——新的从签发那一刻起算 7 天。
HELD="$WORK/held-profiles"
rm -rf "$HELD" && mkdir -p "$HELD"
restore() { [ -d "$HELD" ] && find "$HELD" -name '*.mobileprovision' -exec mv -n {} "$PROFILES/" \; 2>/dev/null || true; }
if [ -d "$PROFILES" ]; then
    for p in "$PROFILES"/*.mobileprovision; do
        [ -e "$p" ] || continue
        id=$(security cms -D -i "$p" 2>/dev/null |
            plutil -extract Entitlements.application-identifier raw - 2>/dev/null || true)
        case "$id" in *."$BUNDLE" | *."$BUNDLE".keyboard) mv "$p" "$HELD/" ;; esac
    done
fi

# ── 4. 编译、签名 ──
say "重新签名($BUNDLE)…"
command -v xcodegen >/dev/null || { restore; die "没有 xcodegen:先跑 mobile/ios/setup.sh。"; }
xcodegen generate --quiet
APP="$WORK/DerivedData/Build/Products/Debug-iphoneos/VoiceInput.app"
rm -rf "$APP"
if ! xcodebuild -project VoiceInput.xcodeproj -scheme VoiceInput -configuration Debug \
    -destination "id=$DEVICE" -derivedDataPath "$WORK/DerivedData" \
    -allowProvisioningUpdates build >"$WORK/build.log" 2>&1; then
    restore
    grep -E " error[: ]" "$WORK/build.log" | sort -u | tail -5 >&2 || true
    die "编译 / 签名失败,完整输出在 $WORK/build.log"
fi
rm -rf "$HELD"

expires=$(security cms -D -i "$APP/embedded.mobileprovision" 2>/dev/null |
    plutil -extract ExpirationDate raw -)
expires_at=$(date -j -u -f '%Y-%m-%dT%H:%M:%SZ' "$expires" +%s)

# ── 5. 装到手机上 ──
if ! xcrun devicectl device install app --device "$DEVICE" "$APP" >"$WORK/install.log" 2>&1; then
    tail -5 "$WORK/install.log" >&2
    say "签好了但没装上(手机可能刚断开),下次再试。"
    exit 75
fi
echo "$expires_at" >"$STAMP"
say "续好了,新的过期时间:$(date -r "$expires_at" '+%Y-%m-%d %H:%M')。"
