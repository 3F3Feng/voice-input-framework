#!/usr/bin/env python3
"""
Voice Input Framework - 版本检查和更新管理模块

功能：
- 检查 GitHub 最新版本
- 比较当前版本
- 提供更新下载链接
"""

import logging
from dataclasses import dataclass
from typing import Optional
import subprocess
import json
import re

logger = logging.getLogger(__name__)

# 当前版本
# 从 __init__.py 导入版本号，保持单一来源
from . import __version__ as CURRENT_VERSION
GITHUB_REPO = "3F3Feng/voice-input-framework"
GITHUB_RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases"


def gh_api(endpoint: str) -> Optional[dict]:
    """
    使用 gh CLI 执行 GitHub API 请求
    
    Args:
        endpoint: API 端点 (如 'repos/owner/repo/releases/latest')
    
    Returns:
        JSON 响应字典，或失败时返回 None
    """
    try:
        result = subprocess.run(
            ['gh', 'api', endpoint],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
        else:
            logger.warning(f"gh api failed: {result.stderr.strip()}")
            return None
    except FileNotFoundError:
        logger.warning("gh CLI 未安装，无法使用 GitHub API")
        return None
    except Exception as e:
        logger.warning(f"gh api 请求失败: {e}")
        return None


@dataclass
class VersionInfo:
    """版本信息"""
    current_version: str
    latest_version: str
    is_outdated: bool
    release_url: str
    download_url: Optional[str] = None
    release_notes: Optional[str] = None


def parse_version(version_str: str) -> tuple:
    """
    解析版本字符串为元组
    
    Args:
        version_str: 版本字符串，如 "v1.2.3" 或 "1.2.3"
    
    Returns:
        (major, minor, patch) 元组
    """
    # 移除 'v' 前缀
    version_str = version_str.lstrip('v')
    
    # 分割版本号
    parts = version_str.split('.')
    
    try:
        major = int(parts[0]) if len(parts) > 0 else 0
        minor = int(parts[1]) if len(parts) > 1 else 0
        patch = int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        logger.warning(f"无法解析版本号: {version_str}")
        return (0, 0, 0)
    
    return (major, minor, patch)


def compare_versions(version1: str, version2: str) -> int:
    """
    比较两个版本号
    
    Args:
        version1: 版本1
        version2: 版本2
    
    Returns:
        -1 如果 version1 < version2
         0 如果 version1 == version2
         1 如果 version1 > version2
    """
    v1 = parse_version(version1)
    v2 = parse_version(version2)
    
    if v1 < v2:
        return -1
    elif v1 > v2:
        return 1
    else:
        return 0


def check_for_updates() -> VersionInfo:
    """
    检查 GitHub 最新版本
    
    Returns:
        VersionInfo 对象，包含版本比较结果
    """
    # 使用 gh api 获取最新 release 信息
    data = gh_api(f'repos/{GITHUB_REPO}/releases/latest')
    
    if not data:
        # gh api 失败，尝试直接使用 GitHub Releases 页面作为后备
        logger.warning("GitHub API 获取失败，使用 releases 页面作为后备")
        return VersionInfo(
            current_version=CURRENT_VERSION,
            latest_version=CURRENT_VERSION,
            is_outdated=False,
            release_url=GITHUB_RELEASES_URL
        )
    
    latest_version = data.get('tag_name', '')
    if not latest_version:
        logger.warning("GitHub API 未返回版本信息")
        return VersionInfo(
            current_version=CURRENT_VERSION,
            latest_version=CURRENT_VERSION,
            is_outdated=False,
            release_url=GITHUB_RELEASES_URL
        )
    
    # 检查是否需要更新
    is_outdated = compare_versions(CURRENT_VERSION, latest_version) < 0
    
    # 获取下载链接（Windows exe）
    download_url = None
    assets = data.get('assets', [])
    for asset in assets:
        if asset.get('name', '').endswith('.exe'):
            download_url = asset.get('browser_download_url')
            break
    
    # 获取 release notes（取前500字符）
    release_notes = data.get('body', '')
    if release_notes and len(release_notes) > 500:
        release_notes = release_notes[:500] + "..."
    
    logger.info(f"版本检查完成: 当前={CURRENT_VERSION}, 最新={latest_version}, 需要更新={is_outdated}")
    
    return VersionInfo(
        current_version=CURRENT_VERSION,
        latest_version=latest_version,
        is_outdated=is_outdated,
        release_url=data.get('html_url', GITHUB_RELEASES_URL),
        download_url=download_url,
        release_notes=release_notes
    )


def format_version_message(version_info: VersionInfo) -> str:
    """
    格式化版本信息为用户可读的消息
    
    Args:
        version_info: 版本信息
    
    Returns:
        格式化的消息字符串
    """
    if version_info.is_outdated:
        lines = [
            f"发现新版本: {version_info.latest_version}",
            f"当前版本: {version_info.current_version}",
            "",
            "更新内容:",
            version_info.release_notes or "无",
            "",
            f"下载: {version_info.release_url}"
        ]
        return "\n".join(filter(None, lines))
    else:
        return f"当前已是最新版本 ({version_info.current_version})"
