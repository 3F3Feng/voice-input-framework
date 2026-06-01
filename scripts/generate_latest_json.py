#!/usr/bin/env python3
"""
Generate latest.json for Tauri updater plugin.

Run during release build after assets are renamed.
Reads version from RELEASE_TAG env var, finds assets in release-assets/ dir.
Looks for .sig files alongside assets for signature verification.
"""
import json, os, glob, re, datetime

RELEASE_TAG = os.environ.get("RELEASE_TAG", "")
REPO = "3F3Feng/voice-input-framework"
BASE = f"https://github.com/{REPO}/releases/download/{RELEASE_TAG}"
ASSETS_DIR = "release-assets"

version = RELEASE_TAG.lstrip("v")
if not version:
    print("ERROR: RELEASE_TAG not set")
    exit(1)

# Platform patterns: (compiled_regex, tauri_platform_key)
platform_map = [
    (re.compile(r"Windows-.*\.exe"), "windows-x86_64"),
    (re.compile(r"macOS-.*\.dmg"), "darwin-aarch64"),
    (re.compile(r"Linux-.*\.AppImage"), "linux-x86_64"),
]

manifest = {
    "version": version,
    "notes": f"Voice Input Framework v{version}",
    "pub_date": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    "platforms": {}
}

for f in sorted(glob.glob(f"{ASSETS_DIR}/GUI-*")):
    name = os.path.basename(f)
    # Skip Python GUI files (GUI-Python-*), prefer Tauri native GUI
    if "-Python-" in name:
        continue
    for pattern, key in platform_map:
        if pattern.search(name):
            # Look for .sig file next to the asset
            sig_file = f + ".sig"
            sig = ""
            if os.path.exists(sig_file):
                with open(sig_file) as sf:
                    sig = sf.read().strip()
                print(f"  ✅ Signature loaded for {name}")
            else:
                print(f"  ⚠️  No .sig file for {name}")

            manifest["platforms"][key] = {
                "signature": sig,
                "url": f"{BASE}/{name}"
            }
            break

if not manifest["platforms"]:
    print("WARNING: no GUI assets found, listing files:")
    for f in sorted(glob.glob(f"{ASSETS_DIR}/*")):
        print(f"  {os.path.basename(f)}")

with open(f"{ASSETS_DIR}/latest.json", "w") as f:
    json.dump(manifest, f, indent=2)

print(f"✅ {ASSETS_DIR}/latest.json generated (v{version}, {len(manifest['platforms'])} platforms)")
for k, v in manifest["platforms"].items():
    print(f"   {k}: sig={'✓' if v['signature'] else '✗  EMPTY!'}")
