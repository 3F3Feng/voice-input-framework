#!/usr/bin/env python3
"""
Voice Input Framework - 配置文件管理模块

支持 JSON 配置文件，自动加载/保存配置，配置验证
"""

import copy
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# 默认配置
DEFAULT_CONFIG = {
    "server": {
        "host": "100.124.8.85",
        "port": 6543
    },
    "hotkey": {
        "key": "left_ctrl+left_alt",
        "distinguish_left_right": True
    },
    "ui": {
        "start_minimized": False,
        "use_floating_indicator": True,
        "use_tray": True,
        "opacity": 0.8
    },
    "audio": {
        "device": None,
        "language": "auto"
    }
}


class ConfigManager:
    """配置文件管理器"""
    
    def __init__(self, config_path: Optional[str] = None):
        """
        初始化配置管理器
        
        Args:
            config_path: 配置文件路径，默认为 ~/.voice_input_config.json
        """
        if config_path:
            self.config_path = Path(config_path)
        else:
            self.config_path = Path.home() / ".voice_input_config.json"
        
        self.config: Dict[str, Any] = {}
        self._load()
    
    def _load(self) -> None:
        """加载配置文件，如果不存在或损坏则使用默认值"""
        try:
            if self.config_path.exists():
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    loaded_config = json.load(f)
                # 验证并合并配置
                self.config = self._merge_with_defaults(loaded_config)
                logger.info(f"已加载配置文件: {self.config_path}")
            else:
                # 首次运行，使用默认配置并保存
                self.config = copy.deepcopy(DEFAULT_CONFIG)
                self.save()
                logger.info(f"已创建默认配置文件: {self.config_path}")
        except json.JSONDecodeError as e:
            logger.warning(f"配置文件损坏，使用默认值: {e}")
            self.config = copy.deepcopy(DEFAULT_CONFIG)
        except Exception as e:
            logger.warning(f"加载配置文件失败，使用默认值: {e}")
            self.config = copy.deepcopy(DEFAULT_CONFIG)
    
    def _merge_with_defaults(self, loaded_config: Dict[str, Any]) -> Dict[str, Any]:
        """
        将加载的配置与默认配置合并，确保所有必需字段存在
        
        Args:
            loaded_config: 从文件加载的配置
        
        Returns:
            合并后的配置
        """
        result = copy.deepcopy(DEFAULT_CONFIG)
        
        # 递归合并字典
        def deep_merge(base: Dict, override: Dict) -> Dict:
            for key, value in override.items():
                if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                    base[key] = deep_merge(base[key], value)
                else:
                    base[key] = value
            return base
        
        return deep_merge(result, loaded_config)
    
    def save(self) -> bool:
        """
        保存配置到文件
        
        Returns:
            是否保存成功
        """
        try:
            # 确保父目录存在
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
            
            logger.info(f"配置已保存到: {self.config_path}")
            return True
        except Exception as e:
            logger.error(f"保存配置失败: {e}")
            return False
    
    def get(self, key: str, default: Any = None) -> Any:
        """
        获取配置项（支持点号分隔的路径）
        
        Args:
            key: 配置键，如 "server.host"
            default: 默认值
        
        Returns:
            配置值
        """
        keys = key.split('.')
        value = self.config
        
        try:
            for k in keys:
                value = value[k]
            return value
        except (KeyError, TypeError):
            return default
    
    def set(self, key: str, value: Any, save_immediately: bool = False) -> None:
        """
        设置配置项（支持点号分隔的路径）
        
        Args:
            key: 配置键，如 "server.host"
            value: 配置值
            save_immediately: 是否立即保存到文件
        """
        keys = key.split('.')
        config = self.config
        
        # 导航到目标位置
        for k in keys[:-1]:
            if k not in config or not isinstance(config[k], dict):
                config[k] = {}
            config = config[k]
        
        # 设置值
        config[keys[-1]] = value
        
        if save_immediately:
            self.save()
    
    # ======== 便捷属性访问器 ========
    
    @property
    def server_host(self) -> str:
        """服务器地址"""
        return self.get('server.host', DEFAULT_CONFIG['server']['host'])
    
    @server_host.setter
    def server_host(self, value: str) -> None:
        self.set('server.host', value)
    
    @property
    def server_port(self) -> int:
        """服务器端口"""
        return self.get('server.port', DEFAULT_CONFIG['server']['port'])
    
    @server_port.setter
    def server_port(self, value: int) -> None:
        self.set('server.port', value)
    
    @property
    def hotkey(self) -> str:
        """快捷键"""
        return self.get('hotkey.key', DEFAULT_CONFIG['hotkey']['key'])
    
    @hotkey.setter
    def hotkey(self, value: str) -> None:
        self.set('hotkey.key', value)
    
    @property
    def distinguish_left_right(self) -> bool:
        """是否区分左右修饰键"""
        return self.get('hotkey.distinguish_left_right', DEFAULT_CONFIG['hotkey']['distinguish_left_right'])
    
    @distinguish_left_right.setter
    def distinguish_left_right(self, value: bool) -> None:
        self.set('hotkey.distinguish_left_right', value)
    
    @property
    def start_minimized(self) -> bool:
        """启动时最小化"""
        return self.get('ui.start_minimized', DEFAULT_CONFIG['ui']['start_minimized'])
    
    @start_minimized.setter
    def start_minimized(self, value: bool) -> None:
        self.set('ui.start_minimized', value)
    
    @property
    def use_floating_indicator(self) -> bool:
        """使用悬浮指示器"""
        return self.get('ui.use_floating_indicator', DEFAULT_CONFIG['ui']['use_floating_indicator'])
    
    @use_floating_indicator.setter
    def use_floating_indicator(self, value: bool) -> None:
        self.set('ui.use_floating_indicator', value)
    
    @property
    def use_tray(self) -> bool:
        """使用系统托盘"""
        return self.get('ui.use_tray', DEFAULT_CONFIG['ui']['use_tray'])
    
    @use_tray.setter
    def use_tray(self, value: bool) -> None:
        self.set('ui.use_tray', value)
    
    @property
    def opacity(self) -> float:
        """浮标透明度"""
        return self.get('ui.opacity', DEFAULT_CONFIG['ui']['opacity'])
    
    @opacity.setter
    def opacity(self, value: float) -> None:
        self.set('ui.opacity', value)
    
    @property
    def selected_device(self) -> Optional[int]:
        """麦克风设备"""
        return self.get('audio.device', DEFAULT_CONFIG['audio']['device'])
    
    @selected_device.setter
    def selected_device(self, value: Optional[int]) -> None:
        self.set('audio.device', value)
    
    @property
    def language(self) -> str:
        """识别语言"""
        return self.get('audio.language', DEFAULT_CONFIG['audio']['language'])
    
    @language.setter
    def language(self, value: str) -> None:
        self.set('audio.language', value)
    
    def validate(self) -> bool:
        """
        验证配置是否有效
        
        Returns:
            配置是否有效
        """
        try:
            # 验证服务器配置
            if not isinstance(self.server_port, int) or self.server_port < 1 or self.server_port > 65535:
                logger.warning(f"无效的端口号: {self.server_port}")
                return False
            
            # 验证透明度
            if not isinstance(self.opacity, (int, float)) or self.opacity < 0 or self.opacity > 1:
                logger.warning(f"无效的透明度: {self.opacity}")
                return False
            
            return True
        except Exception as e:
            logger.error(f"配置验证失败: {e}")
            return False
    
    def reset_to_defaults(self) -> None:
        """重置为默认配置"""
        self.config = copy.deepcopy(DEFAULT_CONFIG)
        self.save()
        logger.info("配置已重置为默认值")
    
    def get_all(self) -> Dict[str, Any]:
        """获取完整配置"""
        return self.config.copy()
