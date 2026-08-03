#!/usr/bin/env python3
"""
Voice Input Framework - 版本检查和更新管理模块

功能：
- 检查 GitHub 最新版本（HTTP API，带超时）
- 比较当前版本
- 后台定时检查
- 提供更新下载链接和下载进度
"""

import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

from . import __version__ as CURRENT_VERSION

GITHUB_REPO = "3F3Feng/voice-input-framework"
GITHUB_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
GITHUB_RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases"
CHECK_TIMEOUT = 10  # 秒
PERIODIC_INTERVAL = 6 * 3600  # 6小时


@dataclass
class VersionInfo:
    current_version: str
    latest_version: str
    is_outdated: bool
    release_url: str
    download_url: str | None = None
    release_notes: str | None = None


def parse_version(v: str) -> tuple:
    v = v.lstrip("v")
    parts = v.split(".")
    try:
        return (int(parts[0]) if len(parts) > 0 else 0,
                int(parts[1]) if len(parts) > 1 else 0,
                int(parts[2]) if len(parts) > 2 else 0)
    except ValueError:
        return (0, 0, 0)


def compare_versions(v1: str, v2: str) -> int:
    a, b = parse_version(v1), parse_version(v2)
    return -1 if a < b else 1 if a > b else 0


def check_for_updates() -> VersionInfo | None:
    """检查 GitHub 最新版本（HTTP API，10秒超时）"""
    try:
        req = Request(GITHUB_API, headers={"Accept": "application/vnd.github.v3+json", "User-Agent": "VoiceInput/2.0"})
        with urlopen(req, timeout=CHECK_TIMEOUT) as r:
            data = json.loads(r.read())
    except URLError as e:
        logger.warning(f"GitHub API 超时: {e}")
        return None
    except Exception as e:
        logger.warning(f"GitHub API 请求失败: {e}")
        return None

    latest_version = data.get("tag_name", "")
    if not latest_version:
        return None

    is_outdated = compare_versions(CURRENT_VERSION, latest_version) < 0
    download_url = None
    for asset in data.get("assets", []):
        name = asset.get("name", "")
        if name.endswith(".exe") or name.endswith(".dmg") or name.endswith(".AppImage"):
            download_url = asset.get("browser_download_url")
            break

    body = (data.get("body") or "")[:500]
    if len(body) >= 500:
        body += "..."

    logger.info(f"版本检查: 当前={CURRENT_VERSION}, 最新={latest_version}, 需要更新={is_outdated}")
    return VersionInfo(
        current_version=CURRENT_VERSION,
        latest_version=latest_version,
        is_outdated=is_outdated,
        release_url=data.get("html_url", GITHUB_RELEASES_URL),
        download_url=download_url,
        release_notes=body or None,
    )


class UpdateChecker:
    """后台更新检查器（定时检查 + 回调通知）"""

    def __init__(self, on_update_available: Callable[[VersionInfo], None] | None = None):
        self._on_update = on_update_available
        self._timer: threading.Timer | None = None
        self._running = False

    def start(self):
        """启动后台定时检查"""
        if self._running:
            return
        self._running = True
        self._schedule()

    def stop(self):
        """停止后台检查"""
        self._running = False
        if self._timer:
            self._timer.cancel()
            self._timer = None

    def _schedule(self):
        if not self._running:
            return
        self._timer = threading.Timer(PERIODIC_INTERVAL, self._check)
        self._timer.daemon = True
        self._timer.start()

    def _check(self):
        try:
            info = check_for_updates()
            if info and info.is_outdated and self._on_update:
                self._on_update(info)
        except Exception:
            logger.exception("后台更新检查异常")
        self._schedule()

    def check_now(self) -> VersionInfo | None:
        """立即检查一次"""
        return check_for_updates()
