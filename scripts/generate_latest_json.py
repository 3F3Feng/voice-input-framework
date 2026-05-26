#!/usr/bin/env python3
"""
Generate latest.json for Tauri updater plugin.

Run during release build after assets are renamed.
Reads version from RELEASE_TAG env var, finds assets in release-assets/ dir.
"""
import json, os, glob

RELEASE_TAG = os.environ.get("RELEASE_TAG", "")
REPO = "3F3Feng/voice-input-framework"
BASE = f"https://github.com/{REPO}/releases/download/{RELEASE_TAG}"
ASSETS_DIR = "release-assets"

version = RELEASE_TAG.lstrip("v")
if not version:
    print("ERROR: RELEASE_TAG not set")
    exit(1)

# Map platform patterns → Tauri platform key
platform_map = {
    "Windows-": "windows-x86_64",
    "macOS-": "darwin-aarch64",
    "Linux-.*AppImage": "linux-x86_64",
}

manifest = {
    "version": version,
    "notes": f"Voice Input Framework v{version}",
    "pub_date": "",
    "platforms": {}
}

for f in glob.glob(f"{ASSETS_DIR}/GUI-*"):
    name = os.path.basename(f)
    for pattern, key in platform_map.items():
        import re
        if re.search(pattern, name):
            manifest["platforms"][key] = {
                "signature": "",
                "url": f"{BASE}/{name}"
            }
            break

if not manifest["platforms"]:
    print("WARNING: no GUI assets found, listing files:")
    for f in glob.glob(f"{ASSETS_DIR}/*"):
        print(f"  {f}")

out_dir = "release-assets"
with open(f"{out_dir}/latest.json", "w") as f:
    json.dump(manifest, f, indent=2)
print(f"✅ {out_dir}/latest.json generated (v{version}, {len(manifest['platforms'])} platforms)")
