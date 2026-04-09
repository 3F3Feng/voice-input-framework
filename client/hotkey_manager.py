#!/usr/bin/env python3
"""
Voice Input Framework - 快捷键管理模块
支持区分左右修饰键的快捷键管理器

功能：
- 区分左右 Shift/Alt/Ctrl/Cmd
- 快捷键录制功能
- 快捷键冲突检测
- 多套快捷键配置方案
"""

import logging
from typing import Optional, Callable, Set, Dict, List, Tuple
from pynput import keyboard

logger = logging.getLogger(__name__)


# 左右修饰键的 KeyCode (跨平台)
# 参考：https://pynput.readthedocs.io/en/latest/keyboard.html
class ModifierKey:
    """修饰键定义，区分左右"""
    
    # macOS 特定键码
    LEFT_CMD_MACOS = 0x37
    RIGHT_CMD_MACOS = 0x36
    LEFT_SHIFT_MACOS = 0x38
    RIGHT_SHIFT_MACOS = 0x3C
    LEFT_ALT_MACOS = 0x3A
    RIGHT_ALT_MACOS = 0x3D
    LEFT_CTRL_MACOS = 0x3B
    RIGHT_CTRL_MACOS = 0x3E
    
    # Windows/Linux 键码 (需要测试)
    LEFT_SHIFT_WIN = 0xA0
    RIGHT_SHIFT_WIN = 0xA1
    LEFT_CTRL_WIN = 0xA2
    RIGHT_CTRL_WIN = 0xA3
    LEFT_ALT_WIN = 0xA4
    RIGHT_ALT_WIN = 0xA5
    
    @classmethod
    def get_modifier_keys(cls) -> Dict[str, List[keyboard.KeyCode]]:
        """获取所有修饰键的映射"""
        return {
            # macOS
            'left_cmd': [keyboard.KeyCode.from_vk(cls.LEFT_CMD_MACOS)],
            'right_cmd': [keyboard.KeyCode.from_vk(cls.RIGHT_CMD_MACOS)],
            'left_shift': [keyboard.KeyCode.from_vk(cls.LEFT_SHIFT_MACOS)],
            'right_shift': [keyboard.KeyCode.from_vk(cls.RIGHT_SHIFT_MACOS)],
            'left_alt': [keyboard.KeyCode.from_vk(cls.LEFT_ALT_MACOS)],
            'right_alt': [keyboard.KeyCode.from_vk(cls.RIGHT_ALT_MACOS)],
            'left_ctrl': [keyboard.KeyCode.from_vk(cls.LEFT_CTRL_MACOS)],
            'right_ctrl': [keyboard.KeyCode.from_vk(cls.RIGHT_CTRL_MACOS)],
        }


class HotkeyParser:
    """快捷键解析器"""
    
    # 修饰键的映射表（支持多种写法）
    MODIFIER_ALIASES = {
        # 左右区分
        'left_shift': ['left_shift', 'lshift', '<left shift>'],
        'right_shift': ['right_shift', 'rshift', '<right shift>'],
        'left_alt': ['left_alt', 'lalt', '<left alt>', '<left option>'],
        'right_alt': ['right_alt', 'ralt', '<right alt>', '<right option>'],
        'left_ctrl': ['left_ctrl', 'lctrl', '<left ctrl>', '<left control>'],
        'right_ctrl': ['right_ctrl', 'rctrl', '<right ctrl>', '<right control>'],
        'left_cmd': ['left_cmd', 'lcmd', '<left cmd>', '<left command>'],
        'right_cmd': ['right_cmd', 'rcmd', '<right cmd>', '<right command>'],
        
        # 通用修饰键（不区分左右）
        'shift': ['shift', '<shift>'],
        'alt': ['alt', 'option', '<alt>', '<option>'],
        'ctrl': ['ctrl', 'control', '<ctrl>', '<control>'],
        'cmd': ['cmd', 'command', '<cmd>', '<command>'],
    }
    
    @classmethod
    def parse(cls, hotkey_str: str, distinguish_left_right: bool = False) -> Tuple[List[str], str]:
        """
        解析快捷键字符串
        
        Args:
            hotkey_str: 快捷键字符串，如 "right_alt+v", "ctrl+shift+a"
            distinguish_left_right: 是否区分左右修饰键
            
        Returns:
            (修饰键列表, 主键)
            例如: (['right_alt'], 'v')
        """
        # 清理字符串
        cleaned = hotkey_str.lower().strip()
        cleaned = cleaned.replace('<', '').replace('>', '')
        
        # 分割
        parts = [p.strip() for p in cleaned.split('+')]
        
        if not parts:
            raise ValueError("快捷键不能为空")
        
        modifiers = []
        main_key = None
        
        for part in parts:
            is_modifier = False
            
            # 检查是否是修饰键
            for mod_name, aliases in cls.MODIFIER_ALIASES.items():
                if part in aliases:
                    # 如果不区分左右，将 left_xxx 或 right_xxx 转为通用修饰键
                    if not distinguish_left_right and (mod_name.startswith('left_') or mod_name.startswith('right_')):
                        # 提取基础修饰键名称
                        base_mod = mod_name.split('_', 1)[1]
                        if base_mod not in modifiers:
                            modifiers.append(base_mod)
                    else:
                        if mod_name not in modifiers:
                            modifiers.append(mod_name)
                    is_modifier = True
                    break
            
            if not is_modifier:
                # 这是主键
                if main_key is not None:
                    raise ValueError(f"快捷键 '{hotkey_str}' 包含多个主键")
                main_key = part
        
        if main_key is None:
            raise ValueError(f"快捷键 '{hotkey_str}' 缺少主键")
        
        return modifiers, main_key
    
    @classmethod
    def to_string(cls, modifiers: List[str], main_key: str) -> str:
        """
        将修饰键和主键转换为字符串
        
        Args:
            modifiers: 修饰键列表
            main_key: 主键
            
        Returns:
            快捷键字符串，如 "Right Alt + V"
        """
        mod_strs = []
        for mod in modifiers:
            # 格式化显示
            if mod.startswith('left_') or mod.startswith('right_'):
                # left_alt -> Left Alt
                parts = mod.split('_')
                mod_str = ' '.join(p.capitalize() for p in parts)
            else:
                mod_str = mod.capitalize()
            mod_strs.append(mod_str)
        
        main_key_display = main_key.upper() if len(main_key) == 1 else main_key.capitalize()
        
        return ' + '.join(mod_strs + [main_key_display])


class HotkeyManager:
    """
    快捷键管理器
    支持区分左右修饰键、快捷键录制、冲突检测
    """
    
    def __init__(self, distinguish_left_right: bool = True):
        """
        初始化快捷键管理器
        
        Args:
            distinguish_left_right: 是否区分左右修饰键
        """
        self.distinguish_left_right = distinguish_left_right
        self.listener: Optional[keyboard.Listener] = None
        self.pressed_keys: Set[keyboard.KeyCode] = set()
        
        # 当前快捷键配置
        self.current_modifiers: List[str] = []
        self.current_main_key: str = ''
        
        # 回调函数
        self.on_press_callback: Optional[Callable] = None
        self.on_release_callback: Optional[Callable] = None
        
        # 录制模式
        self.is_recording = False
        self.recorded_modifiers: List[str] = []
        self.recorded_main_key: str = ''
        self.on_record_callback: Optional[Callable] = None
        
    def set_hotkey(self, hotkey_str: str) -> bool:
        """
        设置快捷键
        
        Args:
            hotkey_str: 快捷键字符串
            
        Returns:
            是否设置成功
        """
        try:
            modifiers, main_key = HotkeyParser.parse(hotkey_str, self.distinguish_left_right)
            self.current_modifiers = modifiers
            self.current_main_key = main_key
            logger.info(f"设置快捷键: {HotkeyParser.to_string(modifiers, main_key)}")
            return True
        except ValueError as e:
            logger.error(f"快捷键解析失败: {e}")
            return False
    
    def start_listener(self, on_press: Callable, on_release: Callable):
        """
        启动快捷键监听器
        
        Args:
            on_press: 快捷键按下时的回调函数
            on_release: 快捷键释放时的回调函数
        """
        self.on_press_callback = on_press
        self.on_release_callback = on_release
        
        if self.listener:
            try:
                self.listener.stop()
            except:
                pass
        
        self.listener = keyboard.Listener(
            on_press=self._on_key_press,
            on_release=self._on_key_release
        )
        self.listener.start()
        logger.info("快捷键监听器已启动")
    
    def stop_listener(self):
        """停止快捷键监听器"""
        if self.listener:
            try:
                self.listener.stop()
            except:
                pass
            self.listener = None
            logger.info("快捷键监听器已停止")
    
    def start_recording(self, callback: Callable[[str], None]):
        """
        开始录制快捷键
        
        Args:
            callback: 录制完成后的回调函数，参数为快捷键字符串
        """
        self.is_recording = True
        self.recorded_modifiers = []
        self.recorded_main_key = ''
        self.on_record_callback = callback
        logger.info("开始录制快捷键，请按下目标快捷键...")
    
    def stop_recording(self):
        """停止录制"""
        self.is_recording = False
        logger.info("停止录制快捷键")
    
    def _on_key_press(self, key):
        """按键按下处理"""
        try:
            # 添加到已按键集合
            self.pressed_keys.add(key)
            
            # 录制模式
            if self.is_recording:
                self._handle_recording_press(key)
                return
            
            # 检查是否匹配当前快捷键
            if self._check_hotkey_match():
                if self.on_press_callback:
                    self.on_press_callback()
        
        except Exception as e:
            logger.error(f"按键处理错误: {e}")
    
    def _on_key_release(self, key):
        """按键释放处理"""
        try:
            # 从已按键集合移除
            self.pressed_keys.discard(key)
            
            # 录制模式
            if self.is_recording:
                # 录制模式下，释放主键时完成录制
                if self.recorded_main_key and self.on_record_callback:
                    hotkey_str = HotkeyParser.to_string(
                        self.recorded_modifiers, 
                        self.recorded_main_key
                    )
                    self.on_record_callback(hotkey_str)
                    self.is_recording = False
                return
            
            # 检查快捷键是否释放
            if not self._check_hotkey_match():
                if self.on_release_callback:
                    self.on_release_callback()
        
        except Exception as e:
            logger.error(f"按键释放处理错误: {e}")
    
    def _handle_recording_press(self, key):
        """处理录制模式的按键"""
        # 获取键的名称
        key_name = self._get_key_name(key)
        
        # 检查是否是修饰键
        if self._is_modifier_key(key, key_name):
            # 添加修饰键
            mod_name = self._get_modifier_name(key, key_name)
            if mod_name and mod_name not in self.recorded_modifiers:
                self.recorded_modifiers.append(mod_name)
        else:
            # 这是主键
            if not self.recorded_main_key:
                self.recorded_main_key = key_name
    
    def _get_key_name(self, key) -> str:
        """获取键的名称"""
        try:
            if hasattr(key, 'char') and key.char:
                return key.char.lower()
            elif hasattr(key, 'name') and key.name:
                return key.name.lower()
            elif hasattr(key, 'vk'):
                # 尝试从虚拟键码获取名称
                return f'vk_{key.vk}'
        except:
            pass
        return 'unknown'
    
    def _is_modifier_key(self, key, key_name: str) -> bool:
        """判断是否是修饰键"""
        # 检查键名
        for mod_name, aliases in HotkeyParser.MODIFIER_ALIASES.items():
            if key_name in aliases:
                return True
        
        # 检查键码（用于区分左右）
        if hasattr(key, 'vk'):
            vk = key.vk
            mod_keys = ModifierKey.get_modifier_keys()
            for mod_list in mod_keys.values():
                for mod_key in mod_list:
                    if hasattr(mod_key, 'vk') and mod_key.vk == vk:
                        return True
        
        return False
    
    def _get_modifier_name(self, key, key_name: str) -> Optional[str]:
        """获取修饰键名称"""
        # 首先检查键名
        for mod_name, aliases in HotkeyParser.MODIFIER_ALIASES.items():
            if key_name in aliases:
                return mod_name
        
        # 检查键码（用于区分左右）
        if hasattr(key, 'vk'):
            vk = key.vk
            
            # macOS 键码
            if vk == ModifierKey.LEFT_CMD_MACOS:
                return 'left_cmd'
            elif vk == ModifierKey.RIGHT_CMD_MACOS:
                return 'right_cmd'
            elif vk == ModifierKey.LEFT_SHIFT_MACOS:
                return 'left_shift'
            elif vk == ModifierKey.RIGHT_SHIFT_MACOS:
                return 'right_shift'
            elif vk == ModifierKey.LEFT_ALT_MACOS:
                return 'left_alt'
            elif vk == ModifierKey.RIGHT_ALT_MACOS:
                return 'right_alt'
            elif vk == ModifierKey.LEFT_CTRL_MACOS:
                return 'left_ctrl'
            elif vk == ModifierKey.RIGHT_CTRL_MACOS:
                return 'right_ctrl'
            
            # Windows 键码
            if vk == ModifierKey.LEFT_SHIFT_WIN:
                return 'left_shift'
            elif vk == ModifierKey.RIGHT_SHIFT_WIN:
                return 'right_shift'
            elif vk == ModifierKey.LEFT_CTRL_WIN:
                return 'left_ctrl'
            elif vk == ModifierKey.RIGHT_CTRL_WIN:
                return 'right_ctrl'
            elif vk == ModifierKey.LEFT_ALT_WIN:
                return 'left_alt'
            elif vk == ModifierKey.RIGHT_ALT_WIN:
                return 'right_alt'
        
        return None
    
    def _check_hotkey_match(self) -> bool:
        """检查当前按下的键是否匹配快捷键"""
        if not self.current_main_key:
            return False
        
        # 检查所有修饰键是否按下
        for mod in self.current_modifiers:
            if not self._is_modifier_pressed(mod):
                return False
        
        # 检查主键是否按下
        return self._is_main_key_pressed()
    
    def _is_modifier_pressed(self, mod_name: str) -> bool:
        """检查指定修饰键是否按下"""
        # 获取对应的 pynput 键对象
        mod_keys = ModifierKey.get_modifier_keys()
        
        if mod_name in mod_keys:
            # 指定了左右
            for key in mod_keys[mod_name]:
                if key in self.pressed_keys:
                    return True
            # 也检查通用修饰键对象
            generic_keys = self._get_generic_modifier_keys(mod_name)
            for key in generic_keys:
                if key in self.pressed_keys:
                    return True
        else:
            # 通用修饰键，不区分左右
            generic_keys = self._get_generic_modifier_keys(mod_name)
            for key in generic_keys:
                if key in self.pressed_keys:
                    return True
        
        return False
    
    def _get_generic_modifier_keys(self, mod_name: str) -> List:
        """获取通用修饰键的所有可能键对象"""
        result = []
        
        # pynput 的通用键对象
        if mod_name in ('shift', 'left_shift', 'right_shift'):
            result.extend([keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r])
        elif mod_name in ('alt', 'left_alt', 'right_alt'):
            result.extend([keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r])
        elif mod_name in ('ctrl', 'left_ctrl', 'right_ctrl'):
            result.extend([keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r])
        elif mod_name in ('cmd', 'left_cmd', 'right_cmd'):
            result.extend([keyboard.Key.cmd, keyboard.Key.cmd_l, keyboard.Key.cmd_r])
        
        return result
    
    def _is_main_key_pressed(self) -> bool:
        """检查主键是否按下"""
        for key in self.pressed_keys:
            key_name = self._get_key_name(key)
            if key_name == self.current_main_key:
                return True
        
        return False


# 预设快捷键方案
class HotkeyPresets:
    """快捷键预设方案"""
    
    PRESETS = {
        'default': {
            'name': '默认 (Right Alt + V)',
            'hotkey': 'right_alt+v',
            'description': '推荐：使用右侧 Alt 键，不影响左侧 Alt 的系统功能'
        },
        'game_mode': {
            'name': '游戏模式 (F13)',
            'hotkey': 'f13',
            'description': '单键快捷键，不占用常用组合键'
        },
        'left_hand': {
            'name': '左手模式 (Left Alt + V)',
            'hotkey': 'left_alt+v',
            'description': '使用左侧 Alt，适合右手操作鼠标的用户'
        },
        'classic': {
            'name': '经典模式 (Alt + V)',
            'hotkey': 'alt+v',
            'description': '不区分左右 Alt，兼容旧版本'
        },
    }
    
    @classmethod
    def get_preset_names(cls) -> List[str]:
        """获取所有预设方案名称"""
        return list(cls.PRESETS.keys())
    
    @classmethod
    def get_preset(cls, name: str) -> Optional[dict]:
        """获取预设方案"""
        return cls.PRESETS.get(name)
