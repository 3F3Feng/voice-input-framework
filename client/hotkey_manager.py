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
    def parse(cls, hotkey_str: str, distinguish_left_right: bool = False) -> Tuple[List[str], Optional[str]]:
        """
        解析快捷键字符串
        
        Args:
            hotkey_str: 快捷键字符串，如 "right_alt+v", "ctrl+shift", "ctrl+shift+a"
            distinguish_left_right: 是否区分左右修饰键
            
        Returns:
            (修饰键列表, 主键或None)
            例如: (['right_alt'], 'v') 或 (['ctrl', 'shift'], None)
        """
        # 清理字符串
        cleaned = hotkey_str.lower().strip()
        cleaned = cleaned.replace('<', '').replace('>', '').replace(' ', '')
        
        # 分割
        parts = [p.strip() for p in cleaned.split('+')]
        
        if not parts:
            raise ValueError("快捷键不能为空")
        
        modifiers = []
        main_key = None
        
        for part in parts:
            if not part:  # 跳过空部分
                continue
                
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
                    raise ValueError(f"快捷键 '{hotkey_str}' 包含多个主键或格式错误")
                main_key = part
        
        # 允许只有修饰键的快捷键（不要求主键）
        if not modifiers and main_key is None:
            raise ValueError(f"快捷键 '{hotkey_str}' 必须至少包含一个修饰键或主键")
        
        return modifiers, main_key
    
    @classmethod
    def to_string(cls, modifiers: List[str], main_key: Optional[str] = None) -> str:
        """
        将修饰键和主键转换为字符串（可被 parse 解析的格式）
        
        Args:
            modifiers: 修饰键列表
            main_key: 主键（可选）
            
        Returns:
            快捷键字符串，如 "right_alt+v" 或 "ctrl+shift"
        """
        parts = []
        
        # 添加修饰键
        for mod in modifiers:
            parts.append(mod)
        
        # 添加主键（如果有）
        if main_key:
            parts.append(main_key)
        
        return '+'.join(parts)


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
        
        # 快捷键状态追踪
        self._hotkey_triggered = False  # 快捷键是否已触发
        
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
            
            # 检查是否匹配当前快捷键，且未触发过
            if not self._hotkey_triggered and self._check_hotkey_match():
                self._hotkey_triggered = True
                logger.info(f"快捷键已触发: 修饰键: {self.current_modifiers}, 主键: {self.current_main_key}")
                if self.on_press_callback:
                    try:
                        self.on_press_callback()
                    except Exception as e:
                        logger.error(f"快捷键按下回调出错: {e}")
        
        except Exception as e:
            logger.error(f"按键处理错误: {e}", exc_info=True)
    
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
                # 如果只有修饰键，也在释放任何修饰键时完成
                elif self.recorded_modifiers and self.on_record_callback and self._is_modifier_key(key, self._get_key_name(key)):
                    hotkey_str = HotkeyParser.to_string(self.recorded_modifiers, None)
                    self.on_record_callback(hotkey_str)
                    self.is_recording = False
                return
            
            # 检查快捷键是否被按下（通过释放事件检查状态变化）
            # 如果快捷键曾被触发过，且现在不再匹配，则触发释放回调
            if self._hotkey_triggered and not self._check_hotkey_match():
                self._hotkey_triggered = False
                if self.on_release_callback:
                    try:
                        self.on_release_callback()
                    except Exception as e:
                        logger.error(f"快捷键释放回调出错: {e}")
        
        except Exception as e:
            logger.error(f"按键释放处理错误: {e}")
    
    def _handle_recording_press(self, key):
        """处理录制模式的按键"""
        # 获取键的名称
        key_name = self._get_key_name(key)
        
        logger.info(f"录制模式：键按下 - key: {key}, key_name: {key_name}")
        
        # 检查是否是修饰键
        if self._is_modifier_key(key, key_name):
            # 添加修饰键
            mod_name = self._get_modifier_name(key, key_name)
            if mod_name and mod_name not in self.recorded_modifiers:
                self.recorded_modifiers.append(mod_name)
                logger.info(f"录制模式：添加修饰键 - {mod_name}，当前修饰键: {self.recorded_modifiers}")
        else:
            # 这是主键
            if not self.recorded_main_key:
                self.recorded_main_key = key_name
                logger.info(f"录制模式：设置主键 - {key_name}")
    
    def _get_key_name(self, key) -> str:
        """获取键的名称，使用多重策略确保可靠性"""
        try:
            # 策略1: 尝试使用 key.name（最可靠，对所有特殊键有效）
            if hasattr(key, 'name') and key.name:
                name = str(key.name).lower()
                # 移除可能的修饰符前缀
                if name.startswith('key.'):
                    name = name.split('.')[-1]
                return name
            
            # 策略2: 对于字符键，使用 key.char
            if hasattr(key, 'char') and key.char:
                char = str(key.char).lower()
                # 只有在是有效字符的情况下才使用
                if char and char != 'none':
                    return char
            
            # 策略3: 使用虚拟键码
            if hasattr(key, 'vk') and key.vk is not None:
                vk = key.vk
                # 对于字母键 (A-Z 的虚拟键码是 0x41-0x5A)
                if 0x41 <= vk <= 0x5A:
                    return chr(vk).lower()
                # 对于数字键 (0-9 的虚拟键码是 0x30-0x39)
                elif 0x30 <= vk <= 0x39:
                    return chr(vk)
                # 其他特殊键
                else:
                    return f'vk_{vk}'
        except Exception as e:
            logger.debug(f"获取键名异常: {e}")
        
        return 'unknown'
    
    def _is_modifier_key(self, key, key_name: str) -> bool:
        """判断是否是修饰键 - 优先使用 pynput 内置对象"""
        # 首先检查是否是 pynput 的标准修饰键对象
        standard_modifiers = [
            keyboard.Key.shift_l, keyboard.Key.shift_r,
            keyboard.Key.ctrl_l, keyboard.Key.ctrl_r,
            keyboard.Key.alt_l, keyboard.Key.alt_r,
        ]
        
        # 添加通用修饰键对象（如果存在）
        if hasattr(keyboard.Key, 'shift'):
            standard_modifiers.append(keyboard.Key.shift)
        if hasattr(keyboard.Key, 'ctrl'):
            standard_modifiers.append(keyboard.Key.ctrl)
        if hasattr(keyboard.Key, 'alt'):
            standard_modifiers.append(keyboard.Key.alt)
        
        # 添加 Cmd 键支持
        if hasattr(keyboard.Key, 'cmd_l'):
            standard_modifiers.extend([keyboard.Key.cmd_l, keyboard.Key.cmd_r])
            if hasattr(keyboard.Key, 'cmd'):
                standard_modifiers.append(keyboard.Key.cmd)
        
        if key in standard_modifiers:
            return True
        
        # 对于名称识别，只检查完整的修饰键名称，不检查别名
        # 这避免了字母键被误识别的问题
        modifier_names = {
            'shift', 'shift_l', 'shift_r',
            'ctrl', 'ctrl_l', 'ctrl_r', 'control', 'control_l', 'control_r',
            'alt', 'alt_l', 'alt_r', 'altgr',
            'cmd', 'cmd_l', 'cmd_r', 'option', 'option_l', 'option_r',
        }
        
        return key_name in modifier_names
    
    def _get_modifier_name(self, key, key_name: str) -> Optional[str]:
        """获取修饰键名称"""
        # 首先尝试使用 pynput 标准对象匹配
        modifier_map = {
            keyboard.Key.shift: 'shift',
            keyboard.Key.shift_l: 'left_shift',
            keyboard.Key.shift_r: 'right_shift',
            keyboard.Key.ctrl: 'ctrl',
            keyboard.Key.ctrl_l: 'left_ctrl',
            keyboard.Key.ctrl_r: 'right_ctrl',
            keyboard.Key.alt: 'alt',
            keyboard.Key.alt_l: 'left_alt',
            keyboard.Key.alt_r: 'right_alt',
            keyboard.Key.cmd: 'cmd',
            keyboard.Key.cmd_l: 'left_cmd',
            keyboard.Key.cmd_r: 'right_cmd',
        }
        
        if key in modifier_map:
            return modifier_map[key]
        
        # 备选：使用键名映射
        key_name_map = {
            'shift': 'shift',
            'shift_l': 'left_shift',
            'shift_r': 'right_shift',
            'ctrl': 'ctrl',
            'ctrl_l': 'left_ctrl',
            'ctrl_r': 'right_ctrl',
            'control': 'ctrl',
            'control_l': 'left_ctrl',
            'control_r': 'right_ctrl',
            'alt': 'alt',
            'alt_l': 'left_alt',
            'alt_r': 'right_alt',
            'altgr': 'right_alt',
            'cmd': 'cmd',
            'cmd_l': 'left_cmd',
            'cmd_r': 'right_cmd',
            'option': 'alt',
            'option_l': 'left_alt',
            'option_r': 'right_alt',
        }
        
        if key_name in key_name_map:
            return key_name_map[key_name]
        
        return None
    
    def _check_hotkey_match(self) -> bool:
        """检查当前按下的键是否匹配快捷键"""
        # 如果没有配置任何快捷键，则不匹配
        if not self.current_modifiers and not self.current_main_key:
            return False
        
        # 如果有主键定义，则主键必须按下
        if self.current_main_key and not self._is_main_key_pressed():
            return False
        
        # 检查所有修饰键是否按下
        for mod in self.current_modifiers:
            if not self._is_modifier_pressed(mod):
                logger.debug(f"修饰键 '{mod}' 未按下，快捷键不匹配")
                return False
        
        logger.debug(f"快捷键匹配成功: 修饰键: {self.current_modifiers}, 主键: {self.current_main_key}")
        return True
    
    def _is_modifier_pressed(self, mod_name: str) -> bool:
        """检查指定修饰键是否按下"""
        if not mod_name:
            return False
        
        # 标准化修饰键名称
        mod_lower = mod_name.lower()
        
        # 定义修饰键的所有可能形式
        modifier_key_mappings = {
            'left_shift': [keyboard.Key.shift_l],
            'right_shift': [keyboard.Key.shift_r],
            'left_ctrl': [keyboard.Key.ctrl_l],
            'right_ctrl': [keyboard.Key.ctrl_r],
            'left_alt': [keyboard.Key.alt_l],
            'right_alt': [keyboard.Key.alt_r],
            # 通用修饰键（匹配任意左右）
            'shift': [keyboard.Key.shift_l, keyboard.Key.shift_r],
            'ctrl': [keyboard.Key.ctrl_l, keyboard.Key.ctrl_r],
            'alt': [keyboard.Key.alt_l, keyboard.Key.alt_r],
        }
        
        # 添加通用修饰键对象（如果存在）
        if hasattr(keyboard.Key, 'shift'):
            modifier_key_mappings['shift'].insert(0, keyboard.Key.shift)
        if hasattr(keyboard.Key, 'ctrl'):
            modifier_key_mappings['ctrl'].insert(0, keyboard.Key.ctrl)
        if hasattr(keyboard.Key, 'alt'):
            modifier_key_mappings['alt'].insert(0, keyboard.Key.alt)
        
        # 为 Cmd 键添加支持
        if hasattr(keyboard.Key, 'cmd_l'):
            modifier_key_mappings['left_cmd'] = [keyboard.Key.cmd_l]
            modifier_key_mappings['right_cmd'] = [keyboard.Key.cmd_r]
            modifier_key_mappings['cmd'] = [keyboard.Key.cmd_l, keyboard.Key.cmd_r]
            if hasattr(keyboard.Key, 'cmd'):
                modifier_key_mappings['cmd'].insert(0, keyboard.Key.cmd)
        
        # 检查是否有对应的修饰键被按下
        if mod_lower in modifier_key_mappings:
            target_keys = modifier_key_mappings[mod_lower]
            for pressed_key in self.pressed_keys:
                if pressed_key in target_keys:
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
        """检查主键是否按下，使用多重策略确保可靠性"""
        if not self.current_main_key:
            return False
        
        for key in self.pressed_keys:
            try:
                # 策略1: 使用 key.name（最可靠）
                if hasattr(key, 'name') and key.name:
                    name = str(key.name).lower()
                    if name.startswith('key.'):
                        name = name.split('.')[-1]
                    if name == self.current_main_key:
                        return True
                
                # 策略2: 对于字符键，使用 key.char
                if hasattr(key, 'char') and key.char:
                    char = str(key.char).lower()
                    if char and char != 'none' and char == self.current_main_key:
                        return True
                
                # 策略3: 使用虚拟键码
                if hasattr(key, 'vk') and key.vk is not None:
                    vk = key.vk
                    # 对于字母键
                    if 0x41 <= vk <= 0x5A:
                        if chr(vk).lower() == self.current_main_key:
                            return True
                    # 对于数字键
                    elif 0x30 <= vk <= 0x39:
                        if chr(vk) == self.current_main_key:
                            return True
            except Exception as e:
                logger.debug(f"检查主键时异常: {e}")
                continue
        
        return False


# 预设快捷键方案
class HotkeyPresets:
    """快捷键预设方案"""
    
    PRESETS = {
        'default': {
            'name': '默认 (Left Cmd + Left Alt)',
            'hotkey': 'left_cmd+left_alt',
            'description': '推荐：使用左侧 Cmd 和 Alt 键'
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
