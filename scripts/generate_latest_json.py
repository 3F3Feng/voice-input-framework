#!/usr/bin/env python3
"""为 Tauri 更新器生成 latest.json。

在 release job 里、资产改完名之后跑。版本号取自 RELEASE_TAG,资产在 release-assets/。

**这个脚本宁可让发版失败,也不生成一份签名为空的 latest.json。** 客户端配了
updater pubkey,签名为空一律验不过:用户点「下载安装」永远失败,而且报的错和
「没网」「服务器挂了」长得一样,根本查不到是发版流程漏了签名。v2.0.10 的
latest.json 三个平台的 signature 全是空串,自动更新从那时起就没有能用过——
旧版本的这个脚本只打一行 ⚠️ 就继续,于是没人发现。
"""

import datetime
import glob
import json
import os
import sys

REPO = "3F3Feng/voice-input-framework"
ASSETS_DIR = "release-assets"

# Tauri 更新器认的平台 key → 该平台的更新产物文件名后缀。
#
# 注意这里挂的**不是**给人下载的安装包:macOS 更新器要的是 .app.tar.gz(它解包
# 之后原地替换 .app),不是 .dmg;Linux 要的是 .AppImage.tar.gz。以前 latest.json
# 指着 .dmg,就算签名是对的,更新器也装不了。Windows 的 NSIS 安装器则正好是同
# 一个文件,所以那一项和下载表里的是同一个 .exe。
PLATFORM_SUFFIX = {
    "darwin-aarch64": ".app.tar.gz",
    "linux-x86_64": ".AppImage.tar.gz",
    "windows-x86_64": ".exe",
}

# 按「当前这个应用是怎么装上的」细分的 key。插件查清单的顺序是
# `{os}-{arch}-{installer}`,查不到才回落到 `{os}-{arch}`。
#
# 为什么 Linux 必须多这一条:插件在 Linux 上是按安装方式分派的 —— 用 .deb 装的
# 走 `install_deb()`,而那个函数会先 `is_deb(bytes)` 校验,拿到 AppImage 的 tar.gz
# 只会报 InvalidUpdaterFormat。没有这一条,所有用 .deb 装的用户点更新必然失败。
#
# 这些是「有就更好」:对应的产物没签名就跳过(并在日志里说清楚),不让发版失败。
OPTIONAL_PLATFORM_SUFFIX = {
    "linux-x86_64-deb": ".deb",
}


def die(msg: str) -> None:
    # ::error:: 让这条直接出现在 GitHub Actions 的摘要里,不用翻日志
    print(f"::error::{msg}")
    sys.exit(1)


def main() -> None:
    tag = os.environ.get("RELEASE_TAG", "").strip()
    if not tag:
        die("RELEASE_TAG 没设置,不知道该发哪个版本")
    version = tag.lstrip("v")
    base = f"https://github.com/{REPO}/releases/download/{tag}"

    platforms: dict[str, dict[str, str]] = {}
    problems: list[str] = []

    for key, suffix in PLATFORM_SUFFIX.items():
        # Windows 那一项要排掉 Python 老客户端的 exe,它不是 Tauri 产物
        matches = [
            f
            for f in sorted(glob.glob(f"{ASSETS_DIR}/GUI-*{suffix}"))
            if "-Python-" not in os.path.basename(f)
        ]
        if not matches:
            problems.append(f"{key}: 找不到 GUI-*{suffix}")
            continue
        if len(matches) > 1:
            problems.append(f"{key}: 匹配到多个产物,不知道该挂哪个 → {matches}")
            continue

        asset = matches[0]
        sig_path = f"{asset}.sig"
        if not os.path.exists(sig_path):
            problems.append(f"{key}: {os.path.basename(asset)} 没有配套的 .sig")
            continue
        with open(sig_path) as fh:
            signature = fh.read().strip()
        if not signature:
            problems.append(f"{key}: {os.path.basename(sig_path)} 是空的")
            continue

        platforms[key] = {
            "signature": signature,
            "url": f"{base}/{os.path.basename(asset)}",
        }
        print(f"  ✅ {key}: {os.path.basename(asset)}")

    # 可选项:拿得到就挂上,拿不到只提醒,不挡发版
    for key, suffix in OPTIONAL_PLATFORM_SUFFIX.items():
        matches = [
            f
            for f in sorted(glob.glob(f"{ASSETS_DIR}/GUI-*{suffix}"))
            if "-Python-" not in os.path.basename(f)
        ]
        if len(matches) != 1:
            print(f"  ⏭  {key}: 没有唯一的 GUI-*{suffix},跳过")
            continue
        asset = matches[0]
        sig_path = f"{asset}.sig"
        if not os.path.exists(sig_path) or not open(sig_path).read().strip():
            print(
                f"  ⏭  {key}: {os.path.basename(asset)} 没有签名,跳过"
                f"(用这种方式安装的用户只能手动更新)"
            )
            continue
        platforms[key] = {
            "signature": open(sig_path).read().strip(),
            "url": f"{base}/{os.path.basename(asset)}",
        }
        print(f"  ✅ {key}: {os.path.basename(asset)}")

    if problems:
        print("release-assets/ 里现有的文件:")
        for f in sorted(glob.glob(f"{ASSETS_DIR}/*")):
            print(f"   {os.path.basename(f)}")
        die(
            "latest.json 生成失败,自动更新会装不上:\n  - "
            + "\n  - ".join(problems)
            + "\n检查 bundle.createUpdaterArtifacts 和 TAURI_SIGNING_PRIVATE_KEY。"
        )

    manifest = {
        "version": version,
        "notes": f"Voice Input Framework v{version}",
        "pub_date": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "platforms": platforms,
    }
    out = f"{ASSETS_DIR}/latest.json"
    with open(out, "w") as fh:
        json.dump(manifest, fh, indent=2)
    print(f"✅ {out} 已生成 (v{version}, {len(platforms)} 个平台,签名齐全)")


if __name__ == "__main__":
    main()
