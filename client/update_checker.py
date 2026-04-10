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
import urllib.request
import json
import re

logger = logging.getLogger(__name__)

# 当前版本
CURRENT_VERSION = "1.1.0"
GITHUB_REPO = "3F3Feng/voice-input-framework"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
GITHUB_RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases"


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
    try:
        # 使用 GitHub API 获取最新 release 信息
        request = urllib.request.Request(
            GITHUB_API_URL,
            headers={
                'Accept': 'application/vnd.github.v3+json',
                'User-Agent': 'VoiceInputFramework'
            }
        )
        
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
        
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
        
    except urllib.error.URLError as e:
        logger.warning(f"检查更新失败（网络错误）: {e}")
        return VersionInfo(
            current_version=CURRENT_VERSION,
            latest_version=CURRENT_VERSION,
            is_outdated=False,
            release_url=GITHUB_RELEASES_URL
        )
    except Exception as e:
        logger.warning(f"检查更新失败: {e}")
        return VersionInfo(
            current_version=CURRENT_VERSION,
            latest_version=CURRENT_VERSION,
            is_outdated=False,
            release_url=GITHUB_RELEASES_URL
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
