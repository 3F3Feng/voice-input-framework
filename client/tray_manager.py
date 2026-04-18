#!/usr/bin/env python3
"""
Voice Input Framework - 系统托盘模块
提供跨平台的系统托盘支持

功能:
- 最小化到系统托盘
- 托盘菜单快速操作
- 托盘图标状态指示
- 跨平台通知
"""

import logging
from typing import Optional, Callable, Dict
from enum import Enum

# 导入通知模块
from .notifier import send_notification as _send_platform_notification
from .notifier import is_notification_available, get_notifier_backend

logger = logging.getLogger(__name__)

# 尝试导入 pystray
try:
    import pystray
    from PIL import Image, ImageDraw
    PYSTRAY_AVAILABLE = True
except ImportError:
    PYSTRAY_AVAILABLE = False
    logger.warning("pystray 或 PIL 未安装,托盘功能不可用")


class TrayStatus(Enum):
    """托盘图标状态"""
    READY = "ready"              # 就绪(灰色)
    RECORDING = "recording"      # 录音中(红色)
    PROCESSING = "processing"    # 处理中(蓝色)
    ERROR = "error"              # 错误(感叹号)
    DISCONNECTED = "disconnected"  # 未连接(灰色半透明)


class TrayIconManager:
    """
    系统托盘图标管理器
    使用 pystray 提供跨平台支持
    """

    def __init__(self):
        """初始化托盘管理器"""
        self.icon: Optional[pystray.Icon] = None
        self.status = TrayStatus.DISCONNECTED
        self.is_visible = False

        # 回调函数
        self.callbacks: Dict[str, Callable] = {}

        # 托盘图标
        self._icons: Dict[TrayStatus, Image.Image] = {}
        self._create_icons()

        # 当前模型
        self.current_model = ""
        self.available_models = []

        # 开机自启动状态
        self.auto_start_enabled = False

    def _create_icons(self):
        """创建不同状态的图标"""
        if not PYSTRAY_AVAILABLE:
            return

        # 图标尺寸
        size = 64

        # 就绪状态 - 灰色麦克风
        self._icons[TrayStatus.READY] = self._draw_microphone_icon(size, (128, 128, 128))

        # 录音中 - 红色麦克风
        self._icons[TrayStatus.RECORDING] = self._draw_microphone_icon(size, (220, 53, 69))

        # 处理中 - 蓝色麦克风
        self._icons[TrayStatus.PROCESSING] = self._draw_microphone_icon(size, (0, 123, 255))

        # 错误 - 感叹号
        self._icons[TrayStatus.ERROR] = self._draw_error_icon(size)

        # 未连接 - 灰色半透明
        self._icons[TrayStatus.DISCONNECTED] = self._draw_microphone_icon(
            size, (128, 128, 128), alpha=128
        )

    def _draw_microphone_icon(self, size: int, color: tuple, alpha: int = 255) -> Image.Image:
        """
        绘制麦克风图标

        Args:
            size: 图标尺寸
            color: RGB 颜色
            alpha: 透明度 (0-255)

        Returns:
            PIL Image 对象
        """
        # 创建 RGBA 图像
        img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # 颜色加 alpha
        rgba = (*color, alpha)

        # 麦克风主体 - 椭圆
        mic_width = size // 3
        mic_height = size // 2
        mic_x = size // 2
        mic_y = size // 2 - size // 8

        # 绘制麦克风头部(椭圆)
        draw.ellipse(
            [mic_x - mic_width // 2, mic_y - mic_height // 2,
             mic_x + mic_width // 2, mic_y + mic_height // 2],
            fill=rgba,
            outline=rgba
        )

        # 绘制麦克风底部(弧线)
        arc_y = mic_y + mic_height // 2 - size // 10
        arc_width = size // 2
        draw.arc(
            [mic_x - arc_width // 2, arc_y,
             mic_x + arc_width // 2, arc_y + size // 3],
            start=0, end=180,
            fill=rgba,
            width=3
        )

        # 绘制麦克风支架(竖线)
        stand_x = mic_x
        stand_y1 = arc_y + size // 6
        stand_y2 = size - size // 8
        draw.line([stand_x, stand_y1, stand_x, stand_y2], fill=rgba, width=2)

        # 绘制底座横线
        base_y = stand_y2
        base_width = size // 4
        draw.line([stand_x - base_width // 2, base_y,
                   stand_x + base_width // 2, base_y],
                  fill=rgba, width=2)

        return img

    def _draw_error_icon(self, size: int) -> Image.Image:
        """
        绘制错误图标(感叹号)

        Args:
            size: 图标尺寸

        Returns:
            PIL Image 对象
        """
        img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # 红色圆形背景
        margin = size // 8
        draw.ellipse(
            [margin, margin, size - margin, size - margin],
            fill=(220, 53, 69, 255)
        )

        # 白色感叹号
        # 竖线
        line_width = size // 8
        line_height = size // 2 - size // 10
        center_x = size // 2
        draw.line(
            [center_x, size // 4, center_x, size // 4 + line_height],
            fill=(255, 255, 255, 255),
            width=line_width
        )

        # 点
        dot_y = size - size // 4
        dot_radius = line_width // 2
        draw.ellipse(
            [center_x - dot_radius, dot_y - dot_radius,
             center_x + dot_radius, dot_y + dot_radius],
            fill=(255, 255, 255, 255)
        )

        return img

    def set_callback(self, action: str, callback: Callable):
        """
        设置回调函数

        Args:
            action: 动作名称 (show_window, hide_window, start_recording,
                            stop_recording, switch_model, quit)
            callback: 回调函数
        """
        self.callbacks[action] = callback

    def set_current_model(self, model: str):
        """设置当前模型,并更新托盘菜单"""
        self.current_model = model
        # 更新托盘菜单以反映新的当前模型
        if self.icon:
            try:
                self.icon.menu = self.create_menu()
                logger.debug(f"托盘菜单已更新: 当前模型 = {model}")
            except Exception as e:
                logger.warning(f"更新托盘菜单失败: {e}")

    def set_available_models(self, models: list):
        """设置可用模型列表,并更新托盘菜单"""
        self.available_models = models
        # 更新托盘菜单以反映新的模型列表
        if self.icon:
            try:
                self.icon.menu = self.create_menu()
                logger.debug(f"托盘菜单已更新: 可用模型 = {len(models)} 个")
            except Exception as e:
                logger.warning(f"更新托盘菜单失败: {e}")

    def create_menu(self) -> pystray.Menu:
        """
        创建托盘菜单

        Returns:
            pystray.Menu 对象
        """
        if not PYSTRAY_AVAILABLE:
            return None

        # 主菜单
        menu_items = [
            # 显示/隐藏窗口
            pystray.MenuItem(
                "显示窗口",
                lambda: self._call_callback("show_window"),
                default=True  # 双击触发
            ),
            pystray.MenuItem(
                "隐藏窗口",
                lambda: self._call_callback("hide_window")
            ),
            pystray.Menu.SEPARATOR,

            # 录音控制
            pystray.MenuItem(
                "开始录音",
                lambda: self._call_callback("start_recording"),
                enabled=lambda item: self.status == TrayStatus.READY
            ),
            pystray.MenuItem(
                "停止录音",
                lambda: self._call_callback("stop_recording"),
                enabled=lambda item: self.status == TrayStatus.RECORDING
            ),
            pystray.Menu.SEPARATOR,

            # 模型选择子菜单
            pystray.MenuItem(
                "模型",
                pystray.Menu(
                    *self._create_model_menu_items(),
                    pystray.Menu.SEPARATOR,
                    pystray.MenuItem(
                        "刷新模型列表",
                        lambda: self._call_callback("refresh_models")
                    )
                )
            ),
            pystray.Menu.SEPARATOR,

            # 检查更新
            pystray.MenuItem(
                "检查更新",
                lambda: self._call_callback("check_update")
            ),
            pystray.Menu.SEPARATOR,

            # 开机自启动
            pystray.MenuItem(
                "开机自启动",
                lambda: self._call_callback("toggle_auto_start"),
                checked=(lambda item: self.auto_start_enabled),
            ),
            pystray.Menu.SEPARATOR,

            # 状态显示 - 使用当前状态生成文本
            pystray.MenuItem(
                f"状态: {self._get_status_text()}",
                None,
                enabled=False
            ),
            pystray.MenuItem(
                f"当前模型: {self.current_model or '未选择'}",
                None,
                enabled=False
            ),
            pystray.Menu.SEPARATOR,

            # 退出
            pystray.MenuItem("退出", lambda: self._call_callback("quit")),
        ]

        return pystray.Menu(*menu_items)
    
    def set_auto_start_enabled(self, enabled: bool):
        """
        设置开机自启动状态并更新菜单
        
        Args:
            enabled: 是否启用
        """
        self.auto_start_enabled = enabled
        # 更新托盘菜单以反映新状态
        if self.icon:
            try:
                self.icon.menu = self.create_menu()
            except Exception as e:
                logger.warning(f"更新托盘菜单失败: {e}")
    
    def _create_model_menu_items(self) -> list:
        """创建模型选择菜单项"""
        items = []
        for model in self.available_models:
            items.append(
                pystray.MenuItem(
                    text=model,
                    action=(lambda m: lambda icon, item: self._switch_model(m))(model),
                    checked=(lambda m: lambda item: self.current_model == m)(model)
                )
            )
        return items

    def _switch_model(self, model_name: str):
        """切换模型"""
        if "switch_model" in self.callbacks:
            self.callbacks["switch_model"](model_name)

    def _call_callback(self, action: str):
        """调用回调函数"""
        if action in self.callbacks:
            self.callbacks[action]()

    def _get_status_text(self) -> str:
        """获取状态文本"""
        status_texts = {
            TrayStatus.READY: "就绪",
            TrayStatus.RECORDING: "🔴 录音中",
            TrayStatus.PROCESSING: "⏳ 处理中",
            TrayStatus.ERROR: "❌ 错误",
            TrayStatus.DISCONNECTED: "未连接",
        }
        return status_texts.get(self.status, "未知")

    def set_status(self, status: TrayStatus):
        """
        设置托盘状态

        Args:
            status: 新状态
        """
        self.status = status
        if self.icon:
            # 更新图标 - 直接修改icon属性
            if status in self._icons:
                try:
                    self.icon.icon = self._icons[status]
                    logger.debug(f"托盘图标已更新: {status.value}")
                except Exception as e:
                    logger.warning(f"更新托盘图标失败: {e}")

            # 更新菜单 - 重新生成菜单项以反映最新状态
            try:
                new_menu = self.create_menu()
                self.icon.menu = new_menu
                logger.debug("托盘菜单已更新")
            except Exception as e:
                logger.warning(f"更新托盘菜单失败: {e}")

    def start(self):
        """启动托盘图标"""
        if not PYSTRAY_AVAILABLE:
            logger.warning("pystray 不可用,跳过托盘启动")
            return

        if self.icon:
            logger.warning("托盘图标已存在")
            return

        # 创建托盘图标
        icon_image = self._icons.get(TrayStatus.DISCONNECTED)
        if not icon_image:
            logger.error("无法创建托盘图标")
            return

        menu = self.create_menu()

        self.icon = pystray.Icon(
            "voice_input",
            icon_image,
            "Voice Input Framework",
            menu
        )

        # 在后台线程中运行
        self.icon.run_detached()
        self.is_visible = True
        logger.info("托盘图标已启动")

    def stop(self):
        """停止托盘图标"""
        if self.icon:
            try:
                self.icon.stop()
            except Exception as e:
                logger.warning(f"停止托盘图标时出错: {e}")
            finally:
                self.icon = None
                self.is_visible = False
                logger.info("托盘图标已停止")

    def update_tooltip(self, text: str):
        """
        更新托盘提示文本

        Args:
            text: 提示文本
        """
        if self.icon:
            self.icon.title = text


    def notify(self, title: str, message: str):
        """
        显示系统通知

        使用独立的跨平台通知模块，不依赖 pystray 的 notify 方法。

        Args:
            title: 通知标题
            message: 通知内容
        """
        if not is_notification_available():
            logger.warning(f"通知功能不可用，使用 tooltip 替代: {title} - {message}")
            self.update_tooltip(f"{title}: {message}")
            return

        logger.info(f"正在发送通知 (后端: {get_notifier_backend()}): {title} - {message}")
        success = _send_platform_notification(title, message)

        if not success:
            # 失败时更新 tooltip 作为备选
            logger.warning("通知发送失败，使用 tooltip 替代")
            self.update_tooltip(f"{title}: {message}")

# 导出
if PYSTRAY_AVAILABLE:
    __all__ = ['TrayIconManager', 'TrayStatus']
else:
    __all__ = []
