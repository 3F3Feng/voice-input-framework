#!/usr/bin/env python3
"""
Generate latest.json for Tauri updater plugin.

Run during release build to create the update manifest.
Format: https://tauri.app/plugin/updater/
"""
import json, os, hashlib, sys

RELEASE_TAG = os.environ.get("RELEASE_TAG", "")
REPO = "3F3Feng/voice-input-framework"
BASE = f"https://github.com/{REPO}/releases/download/{RELEASE_TAG}"

def file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

platforms = {
    "windows-x86_64": {"url": f"{BASE}/GUI-Windows-2.0.1-x64.exe", "ext": ".exe"},
    "darwin-aarch64": {"url": f"{BASE}/GUI-macOS-2.0.1-aarch64.dmg", "ext": ".dmg"},
    "linux-x86_64":   {"url": f"{BASE}/GUI-Linux-2.0.1-x64.AppImage", "ext": ".AppImage"},
}

version = RELEASE_TAG.lstrip("v")
manifest = {"version": version, "notes": "Hotkey debounce fix for mouse button bounce", "pub_date": "", "platforms": {}}

for key, info in platforms.items():
    path = f"dist/{os.path.basename(info['url'])}"
    if os.path.exists(path):
        manifest["platforms"][key] = {
            "signature": "",
            "url": info["url"]
        }
    else:
        print(f"  ⚠️ {path} not found, skipping")
        manifest["platforms"][key] = {"signature": "", "url": info["url"]}

with open("dist/latest.json", "w") as f:
    json.dump(manifest, f, indent=2)
print(f"✅ dist/latest.json generated for v{version}")
