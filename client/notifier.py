#!/usr/bin/env python3
"""
跨平台通知模块

支持:
- Windows: 使用 plyer (最可靠) 或 winotify
- macOS: 使用 osascript 或 pyobjc
- Linux: 使用 notify-send
"""

import logging
import platform
import subprocess

logger = logging.getLogger(__name__)

# 检测平台
PLATFORM = platform.system()
IS_WINDOWS = PLATFORM == "Windows"
IS_MACOS = PLATFORM == "Darwin"
IS_LINUX = PLATFORM == "Linux"

# 尝试导入平台特定的通知库
_notifier_backend = None

if IS_WINDOWS:
    # Windows: 优先使用 plyer (最可靠)
    # winotify 需要开始菜单快捷键才能显示通知，plyer 没有这个限制
    try:
        from plyer import notification
        _notifier_backend = "plyer"
        logger.info("使用 plyer 作为 Windows 通知后端")
    except ImportError:
        # 备选: winotify
        try:
            from winotify import Notification, audio
            # 注册 App ID
            try:
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("voiceinput.framework.v1")
            except Exception:
                pass
            _notifier_backend = "winotify"
            logger.warning("⚠️ winotify 需要 Windows 通知权限")
            logger.warning("如果通知不显示，请运行: pip install plyer")
            logger.info("使用 winotify 作为 Windows 通知后端")
        except ImportError:
            # 最后备选: win10toast
            try:
                from win10toast import ToastNotifier
                _notifier_backend = "win10toast"
                logger.info("使用 win10toast 作为 Windows 通知后端")
            except ImportError:
                logger.warning("未找到 Windows 通知库，通知功能不可用")
                logger.info("建议安装: pip install plyer")

elif IS_MACOS:
    # macOS: 使用 osascript
    _notifier_backend = "osascript"
    logger.info("使用 osascript 作为 macOS 通知后端")

elif IS_LINUX:
    # Linux: 尝试 notify-send
    try:
        subprocess.run(["which", "notify-send"], check=True, capture_output=True)
        _notifier_backend = "notify-send"
        logger.info("使用 notify-send 作为 Linux 通知后端")
    except subprocess.CalledProcessError:
        # 备选: plyer
        try:
            from plyer import notification
            _notifier_backend = "plyer"
            logger.info("使用 plyer 作为 Linux 通知后端")
        except ImportError:
            logger.warning("Linux 通知功能不可用")


def send_notification(title: str, message: str, timeout: int = 5) -> bool:
    """
    发送系统通知

    Args:
        title: 通知标题
        message: 通知内容
        timeout: 通知显示时间（秒）

    Returns:
        bool: 是否成功发送
    """
    global _notifier_backend

    if not _notifier_backend:
        logger.warning("没有可用的通知后端")
        return False

    try:
        if _notifier_backend == "plyer":
            # Plyer 跨平台通知
            from plyer import notification
            
            # Windows: 设置 App User Model ID 避免显示 Python 包名
            if IS_WINDOWS:
                try:
                    import ctypes
                    # 注册应用 ID
                    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("voiceinput.framework.v1")
                except Exception:
                    pass
            
            notification.notify(
                title=title,
                message=message,
                app_name="Voice Input",
                timeout=timeout
            )
            logger.info(f"plyer 通知已发送: {title} - {message}")
            return True

        elif _notifier_backend == "winotify":
            # Windows 10+ Toast 通知
            from winotify import Notification, audio
            # 确保 App ID 已注册
            try:
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("voiceinput.framework.v1")
            except Exception:
                pass

            toast = Notification(
                app_id="Voice Input Framework",
                title=title,
                msg=message,
                duration="short",  # short, long
                icon=None
            )
            toast.set_audio(audio.Default, loop=False)
            toast.show()
            logger.info(f"winotify 通知已发送: {title} - {message}")
            return True

        elif _notifier_backend == "win10toast":
            # Windows 10 Toast 通知 (旧版)
            from win10toast import ToastNotifier
            toaster = ToastNotifier()
            toaster.show_toast(
                title,
                message,
                duration=timeout,
                threaded=True
            )
            logger.info(f"win10toast 通知已发送: {title} - {message}")
            return True

        elif _notifier_backend == "osascript":
            # macOS AppleScript 通知
            script = f'''
            display notification "{message}" with title "{title}" sound name "default"
            '''
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                logger.info(f"osascript 通知已发送: {title} - {message}")
                return True
            else:
                logger.warning(f"osascript 通知失败: {result.stderr}")
                return False

        elif _notifier_backend == "notify-send":
            # Linux notify-send
            result = subprocess.run(
                ["notify-send", "-t", str(timeout * 1000), title, message],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                logger.info(f"notify-send 通知已发送: {title} - {message}")
                return True
            else:
                logger.warning(f"notify-send 通知失败: {result.stderr}")
                return False

        else:
            logger.warning(f"未知的通知后端: {_notifier_backend}")
            return False

    except Exception as e:
        logger.error(f"发送通知失败: {e}", exc_info=True)
        return False


def get_notifier_backend() -> str:
    """获取当前使用的通知后端"""
    return _notifier_backend or "none"


def is_notification_available() -> bool:
    """检查通知功能是否可用"""
    return _notifier_backend is not None


# 导出
__all__ = [
    "send_notification",
    "get_notifier_backend",
    "is_notification_available",
    "IS_WINDOWS",
    "IS_MACOS",
    "IS_LINUX"
]
