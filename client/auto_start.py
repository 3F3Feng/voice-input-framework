#!/usr/bin/env python3
"""
Voice Input Framework - 开机自启动管理模块

功能：
- Windows/macOS/Linux 各平台的开机自启动注册
- 查询当前状态
- 启用/禁用切换
"""

import logging
import os
import platform
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


class AutoStartManager:
    """开机自启动管理器"""
    
    def __init__(self, app_name: str = "VoiceInputFramework"):
        """
        初始化自启动管理器
        
        Args:
            app_name: 应用名称（用于注册表/配置文件）
        """
        self.app_name = app_name
        self.system = platform.system()
        
        # 获取可执行文件路径
        if getattr(sys, 'frozen', False):
            # PyInstaller 打包后的 exe
            self.exe_path = sys.executable
        else:
            # 开发环境下的 python
            self.exe_path = sys.executable
    
    def is_enabled(self) -> bool:
        """
        检查是否已启用开机自启动
        
        Returns:
            True 如果已启用，False 否则
        """
        if self.system == "Windows":
            return self._is_enabled_windows()
        elif self.system == "Darwin":
            return self._is_enabled_macos()
        elif self.system == "Linux":
            return self._is_enabled_linux()
        else:
            logger.warning(f"不支持的平台: {self.system}")
            return False
    
    def enable(self) -> bool:
        """
        启用开机自启动
        
        Returns:
            True 如果成功，False 否则
        """
        if self.system == "Windows":
            return self._enable_windows()
        elif self.system == "Darwin":
            return self._enable_macos()
        elif self.system == "Linux":
            return self._enable_linux()
        else:
            logger.warning(f"不支持的平台: {self.system}")
            return False
    
    def disable(self) -> bool:
        """
        禁用开机自启动
        
        Returns:
            True 如果成功，False 否则
        """
        if self.system == "Windows":
            return self._disable_windows()
        elif self.system == "Darwin":
            return self._disable_macos()
        elif self.system == "Linux":
            return self._disable_linux()
        else:
            logger.warning(f"不支持的平台: {self.system}")
            return False
    
    def toggle(self) -> bool:
        """
        切换开机自启动状态
        
        Returns:
            切换后的状态（True=启用，False=禁用）
        """
        if self.is_enabled():
            self.disable()
            return False
        else:
            self.enable()
            return True
    
    # ========== Windows 实现 ==========
    
    def _is_enabled_windows(self) -> bool:
        """检查 Windows 自启动状态"""
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_READ
            )
            try:
                value, _ = winreg.QueryValueEx(key, self.app_name)
                winreg.CloseKey(key)
                return value is not None
            except FileNotFoundError:
                winreg.CloseKey(key)
                return False
        except Exception as e:
            logger.warning(f"检查 Windows 自启动失败: {e}")
            return False
    
    def _enable_windows(self) -> bool:
        """启用 Windows 自启动"""
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_SET_VALUE
            )
            # 添加 exe 路径和启动参数
            command = f'"{self.exe_path}"'
            winreg.SetValueEx(key, self.app_name, 0, winreg.REG_SZ, command)
            winreg.CloseKey(key)
            logger.info(f"Windows 自启动已启用: {command}")
            return True
        except Exception as e:
            logger.error(f"启用 Windows 自启动失败: {e}")
            return False
    
    def _disable_windows(self) -> bool:
        """禁用 Windows 自启动"""
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_SET_VALUE
            )
            try:
                winreg.DeleteValue(key, self.app_name)
            except FileNotFoundError:
                pass  # 键不存在也算禁用成功
            winreg.CloseKey(key)
            logger.info("Windows 自启动已禁用")
            return True
        except Exception as e:
            logger.error(f"禁用 Windows 自启动失败: {e}")
            return False
    
    # ========== macOS 实现 ==========
    
    def _get_macos_plist_path(self) -> Path:
        """获取 macOS plist 文件路径"""
        return Path.home() / "Library" / "LaunchAgents" / f"com.{self.app_name.lower()}.plist"
    
    def _is_enabled_macos(self) -> bool:
        """检查 macOS 自启动状态"""
        plist_path = self._get_macos_plist_path()
        return plist_path.exists()
    
    def _enable_macos(self) -> bool:
        """启用 macOS 自启动"""
        try:
            plist_path = self._get_macos_plist_path()
            
            # 确保目录存在
            plist_path.parent.mkdir(parents=True, exist_ok=True)
            
            # 创建 plist 内容
            plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.{self.app_name.lower()}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{self.exe_path}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
</dict>
</plist>
"""
            plist_path.write_text(plist_content)
            logger.info(f"macOS 自启动已启用: {plist_path}")
            return True
        except Exception as e:
            logger.error(f"启用 macOS 自启动失败: {e}")
            return False
    
    def _disable_macos(self) -> bool:
        """禁用 macOS 自启动"""
        try:
            plist_path = self._get_macos_plist_path()
            if plist_path.exists():
                plist_path.unlink()
            logger.info("macOS 自启动已禁用")
            return True
        except Exception as e:
            logger.error(f"禁用 macOS 自启动失败: {e}")
            return False
    
    # ========== Linux 实现 ==========
    
    def _get_linux_desktop_path(self) -> Path:
        """获取 Linux desktop 文件路径"""
        config_dir = Path.home() / ".config" / "autostart"
        return config_dir / f"{self.app_name.lower()}.desktop"
    
    def _is_enabled_linux(self) -> bool:
        """检查 Linux 自启动状态"""
        desktop_path = self._get_linux_desktop_path()
        return desktop_path.exists()
    
    def _enable_linux(self) -> bool:
        """启用 Linux 自启动"""
        try:
            desktop_path = self._get_linux_desktop_path()
            
            # 确保目录存在
            desktop_path.parent.mkdir(parents=True, exist_ok=True)
            
            # 创建 desktop 文件内容
            desktop_content = f"""[Desktop Entry]
Type=Application
Name={self.app_name}
Exec={self.exe_path}
X-GNOME-Autostart-enabled=true
"""
            desktop_path.write_text(desktop_content)
            logger.info(f"Linux 自启动已启用: {desktop_path}")
            return True
        except Exception as e:
            logger.error(f"启用 Linux 自启动失败: {e}")
            return False
    
    def _disable_linux(self) -> bool:
        """禁用 Linux 自启动"""
        try:
            desktop_path = self._get_linux_desktop_path()
            if desktop_path.exists():
                desktop_path.unlink()
            logger.info("Linux 自启动已禁用")
            return True
        except Exception as e:
            logger.error(f"禁用 Linux 自启动失败: {e}")
            return False
