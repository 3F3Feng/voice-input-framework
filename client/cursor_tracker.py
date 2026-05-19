"""
跨应用文本光标位置获取模块 (Windows + macOS)
使用平台原生 API 获取任意应用程序的文本输入光标位置

Windows: 使用 UI Automation API (uiautomation)
macOS: 使用 Accessibility API (ctypes 调用 ApplicationServices)

依赖:
- Windows: pip install uiautomation
- macOS: 无需额外依赖（使用系统 API）

要求:
- Windows: 需要管理员权限
- macOS: 需要在系统设置中启用辅助功能权限
"""
import time
import threading
import logging
from typing import Optional, Tuple, Callable

logger = logging.getLogger(__name__)

# Platform detection
import platform
IS_WINDOWS = platform.system() == "Windows"
IS_MACOS = platform.system() == "Darwin"


# ============================================================================
# Windows Implementation
# ============================================================================
if IS_WINDOWS:
    try:
        import uiautomation as auto
        AUTO_AVAILABLE = True
    except ImportError:
        AUTO_AVAILABLE = False
        logger.warning("uiautomation 未安装，Windows 光标追踪不可用")


    class CursorTracker:
        """跨应用文本光标位置追踪器 (Windows)"""
        
        def __init__(self, poll_interval: float = 0.1):
            self.poll_interval = poll_interval
            self._running = False
            self._thread = None
            self._last_caret_pos: Optional[Tuple[int, int]] = None
            self._last_window_title: str = ""
            self._callback: Optional[Callable] = None
        
        def start(self, callback: Optional[Callable] = None) -> bool:
            """启动追踪"""
            if not AUTO_AVAILABLE:
                logger.error("uiautomation 未安装")
                return False
            
            self._callback = callback
            self._running = True
            self._thread = threading.Thread(target=self._track_loop, daemon=True)
            self._thread.start()
            logger.info("CursorTracker 已启动 (Windows)")
            return True
        
        def stop(self):
            """停止追踪"""
            self._running = False
            if self._thread:
                self._thread.join(timeout=1.0)
        
        def _track_loop(self):
            """主追踪循环"""
            while self._running:
                try:
                    pos = self._get_caret_position()
                    if pos:
                        x, y = pos
                        try:
                            fg = auto.GetForegroundControl()
                            title = fg.Name if fg else ""
                        except:
                            title = ""
                        
                        if (x, y) != self._last_caret_pos or title != self._last_window_title:
                            self._last_caret_pos = (x, y)
                            self._last_window_title = title
                            if self._callback:
                                self._callback(x, y, title)
                except Exception:
                    pass
                time.sleep(self.poll_interval)
        
        def _get_caret_position(self) -> Optional[Tuple[int, int]]:
            """获取当前前景窗口的文本控件光标位置"""
            try:
                foreground = auto.GetForegroundControl()
                if not foreground:
                    return None
                
                # 方法1: EditControl
                edit = foreground.EditControl()
                if edit.Exists(0.2, 0):
                    rect = edit.BoundingRectangle
                    if rect and rect.width > 0:
                        return (rect.left, rect.top)
                
                # 方法2: DocumentControl
                doc = foreground.DocumentControl()
                if doc.Exists(0.2, 0):
                    rect = doc.BoundingRectangle
                    if rect:
                        return (rect.left, rect.top)
                
                # 方法3: 任何文本控件
                for control in foreground.GetChildren():
                    try:
                        if "Edit" in str(control.ControlType) or "Text" in str(control.ControlType):
                            rect = control.BoundingRectangle
                            if rect and rect.width > 0:
                                return (rect.left, rect.top)
                    except:
                        continue
                
                # 方法4: 窗口位置后备
                rect = foreground.BoundingRectangle
                if rect:
                    return (rect.left, rect.top + 50)
            except Exception:
                pass
            return None
        
        def get_current_position(self) -> Optional[Tuple[int, int]]:
            """获取当前光标位置"""
            return self._get_caret_position()


# ============================================================================
# macOS Implementation
# ============================================================================
elif IS_MACOS:
    import ctypes
    import ctypes.util
    
    # 加载 ApplicationServices framework
    _lib = ctypes.CDLL(ctypes.util.find_library('ApplicationServices'))
    
    # 定义常量
    kAXFocusedUIElementAttribute = b"AXFocusedUIElement"
    kAXSelectedTextRangeAttribute = b"AXSelectedTextRange"
    kAXBoundsForRangeParameterizedAttribute = b"AXBoundsForRange"
    
    # 定义类型
    AXUIElement = ctypes.c_void_p
    CFTypeRef = ctypes.c_void_p
    AXError = ctypes.c_int
    AXValueRef = ctypes.c_void_p
    
    # 定义函数签名
    _AXUIElementCreateSystemWide = _lib.AXUIElementCreateSystemWide
    _AXUIElementCreateSystemWide.restype = AXUIElement
    _AXUIElementCreateSystemWide.argtypes = []
    
    _AXUIElementCopyAttributeValue = _lib.AXUIElementCopyAttributeValue
    _AXUIElementCopyAttributeValue.restype = AXError
    _AXUIElementCopyAttributeValue.argtypes = [AXUIElement, ctypes.c_void_p, ctypes.POINTER(CFTypeRef)]
    
    _AXUIElementCopyParameterizedAttributeValue = _lib.AXUIElementCopyParameterizedAttributeValue
    _AXUIElementCopyParameterizedAttributeValue.restype = AXError
    _AXUIElementCopyParameterizedAttributeValue.argtypes = [AXUIElement, ctypes.c_void_p, CFTypeRef, ctypes.POINTER(CFTypeRef)]
    
    _AXValueGetValue = _lib.AXValueGetValue
    _AXValueGetValue.restype = ctypes.c_bool
    _AXValueGetValue.argtypes = [AXValueRef, ctypes.c_int, ctypes.c_void_p]
    
    # CGPoint 和 CGRect 定义
    class CGPoint(ctypes.Structure):
        _fields_ = [("x", ctypes.c_float), ("y", ctypes.c_float)]
    
    class CGSize(ctypes.Structure):
        _fields_ = [("width", ctypes.c_float), ("height", ctypes.c_float)]
    
    class CGRect(ctypes.Structure):
        _fields_ = [("origin", CGPoint), ("size", CGSize)]
    
    AXValueType = ctypes.c_int
    kAXValueCGPointType = 1
    kAXValueCGSizeType = 2
    kAXValueCGRectType = 3
    kAXValueCFRangeType = 4
    
    # CFRange 定义
    class CFRange(ctypes.Structure):
        _fields_ = [("location", ctypes.c_long), ("length", ctypes.c_long)]
    
    # 检查辅助功能权限
    _AXIsProcessTrusted = _lib.AXIsProcessTrusted
    _AXIsProcessTrusted.restype = ctypes.c_bool
    _AXIsProcessTrusted.argtypes = []
    
    def check_accessibility_permissions() -> bool:
        """检查辅助功能权限"""
        return _AXIsProcessTrusted()
    
    def get_text_cursor_position() -> Optional[Tuple[int, int]]:
        """
        获取当前文本光标位置 (macOS)
        
        使用 Accessibility API:
        1. 获取系统范围的 AXUIElement
        2. 获取当前焦点元素
        3. 获取选中文本范围
        4. 获取该范围的边界矩形
        
        Returns:
            (x, y) 屏幕坐标，如果未找到返回 None
        """
        try:
            # 创建系统范围的元素
            system_wide = _AXUIElementCreateSystemWide()
            
            # 获取焦点元素
            focused_element = CFTypeRef()
            error = _AXUIElementCopyAttributeValue(
                system_wide,
                kAXFocusedUIElementAttribute,
                ctypes.byref(focused_element)
            )
            if error != 0:
                return None
            
            # 获取选中文本范围 (CFRange)
            text_range = CFTypeRef()
            error = _AXUIElementCopyAttributeValue(
                focused_element,
                kAXSelectedTextRangeAttribute,
                ctypes.byref(text_range)
            )
            if error != 0:
                # 尝试获取整个元素的边界
                return _get_element_bounds(focused_element)
            
            # 获取范围的边界
            bounds = CFTypeRef()
            error = _AXUIElementCopyParameterizedAttributeValue(
                focused_element,
                kAXBoundsForRangeParameterizedAttribute,
                text_range,
                ctypes.byref(bounds)
            )
            if error != 0:
                return None
            
            # 转换为 CGRect
            rect = CGRect()
            if _AXValueGetValue(bounds, kAXValueCGRectType, ctypes.byref(rect)):
                # macOS 坐标系统 Y 是从底部开始，需要转换
                # 获取主屏幕高度
                screen_height = get_screen_height()
                if screen_height:
                    # 转换坐标：origin.y 从顶部计算
                    return (int(rect.origin.x), int(screen_height - rect.origin.y - rect.size.height))
                return (int(rect.origin.x), int(rect.origin.y))
            
        except Exception as e:
            logger.debug(f"获取 macOS 光标位置失败: {e}")
        
        return None
    
    def _get_element_bounds(element: AXUIElement) -> Optional[Tuple[int, int]]:
        """获取元素边界"""
        try:
            # 尝试获取 AXBoundsForRange 使用整个文本范围
            # 先获取 AXValue of entire text
            pass
        except:
            pass
        return None
    
    def get_screen_height() -> Optional[float]:
        """获取主屏幕高度"""
        try:
            # 使用 CGMainDisplayID 和 CGDisplayBounds
            _CGMainDisplayID = _lib.CGMainDisplayID
            _CGMainDisplayID.restype = ctypes.c_uint32
            _CGMainDisplayID.argtypes = []
            
            _CGDisplayBounds = _lib.CGDisplayBounds
            _CGDisplayBounds.restype = CGRect
            _CGDisplayBounds.argtypes = [ctypes.c_uint32]
            
            display_id = _CGMainDisplayID()
            rect = _CGDisplayBounds(display_id)
            return rect.size.height
        except:
            return None
    
    def get_focused_window_title() -> str:
        """获取焦点窗口标题"""
        try:
            # 获取焦点应用
            system_wide = _AXUIElementCreateSystemWide()
            focused_element = CFTypeRef()
            error = _AXUIElementCopyAttributeValue(
                system_wide,
                kAXFocusedUIElementAttribute,
                ctypes.byref(focused_element)
            )
            if error != 0:
                return ""
            
            # 获取窗口标题 (AXTitle)
            kAXTitleAttribute = b"AXTitle"
            title = CFTypeRef()
            error = _AXUIElementCopyAttributeValue(
                focused_element,
                kAXTitleAttribute,
                ctypes.byref(title)
            )
            if error == 0:
                # title 是 CFString，需要转换
                try:
                    cfstring = ctypes.cast(title, ctypes.c_void_p)
                    # 简单的 CFString 提取
                    return "Window"  # 简化处理
                except:
                    pass
        except:
            pass
        return "Unknown"


    class CursorTracker:
        """跨应用文本光标位置追踪器 (macOS)"""
        
        def __init__(self, poll_interval: float = 0.1):
            self.poll_interval = poll_interval
            self._running = False
            self._thread = None
            self._last_caret_pos: Optional[Tuple[int, int]] = None
            self._last_window_title: str = ""
            self._callback: Optional[Callable] = None
        
        def start(self, callback: Optional[Callable] = None) -> bool:
            """启动追踪"""
            if not check_accessibility_permissions():
                logger.error("macOS 辅助功能权限未启用，请在系统设置中授权")
                return False
            
            self._callback = callback
            self._running = True
            self._thread = threading.Thread(target=self._track_loop, daemon=True)
            self._thread.start()
            logger.info("CursorTracker 已启动 (macOS)")
            return True
        
        def stop(self):
            """停止追踪"""
            self._running = False
            if self._thread:
                self._thread.join(timeout=1.0)
        
        def _track_loop(self):
            """主追踪循环"""
            while self._running:
                try:
                    pos = get_text_cursor_position()
                    if pos:
                        x, y = pos
                        title = get_focused_window_title()
                        
                        if (x, y) != self._last_caret_pos or title != self._last_window_title:
                            self._last_caret_pos = (x, y)
                            self._last_window_title = title
                            if self._callback:
                                self._callback(x, y, title)
                except Exception as e:
                    logger.debug(f"追踪循环错误: {e}")
                
                time.sleep(self.poll_interval)
        
        def get_current_position(self) -> Optional[Tuple[int, int]]:
            """获取当前光标位置"""
            return get_text_cursor_position()


# ============================================================================
# Unsupported Platform
# ============================================================================
else:
    class CursorTracker:
        """不支持的平台"""
        
        def __init__(self, poll_interval: float = 0.1):
            raise NotImplementedError(f"不支持的平台: {platform.system()}")
        
        def start(self, callback=None) -> bool:
            return False
        
        def stop(self):
            pass
        
        def get_current_position(self) -> Optional[Tuple[int, int]]:
            return None


# ============================================================================
# Demo
# ============================================================================
def demo_callback(x: int, y: int, title: str):
    """演示回调函数"""
    print(f"光标位置: ({x}, {y}) | 窗口: {title[:50]}")


def demo():
    """演示模式"""
    print("=" * 60)
    print("跨应用文本光标追踪演示")
    print(f"平台: {platform.system()}")
    print("=" * 60)
    print("请切换到任意文本输入应用（记事本、浏览器、IDE等）")
    print("按 Ctrl+C 退出")
    print()
    
    tracker = CursorTracker(poll_interval=0.2)
    
    if not tracker.start(callback=demo_callback):
        if IS_WINDOWS:
            print("错误: 请先安装 uiautomation")
            print("pip install uiautomation")
        elif IS_MACOS:
            print("错误: macOS 辅助功能权限未启用")
            print("请前往 系统设置 > 隐私与安全性 > 辅助功能 授权")
        return
    
    try:
        while True:
            time.sleep(0.5)
            pos = tracker.get_current_position()
            if not pos:
                print("未检测到文本输入控件...")
    except KeyboardInterrupt:
        print("\n退出...")
    finally:
        tracker.stop()


if __name__ == "__main__":
    demo()
