#!/usr/bin/env python3
"""
Voice Input Framework - 配置管理器测试
"""

import json
import tempfile
from pathlib import Path
import pytest
import shutil
import os

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from client.config_manager import ConfigManager, DEFAULT_CONFIG


class TestConfigManager:
    """ConfigManager 测试类"""
    
    def test_default_config(self, tmp_path):
        """测试默认配置创建"""
        config_path = tmp_path / "test_default.json"
        manager = ConfigManager(config_path=str(config_path))
        
        assert manager.config is not None
        assert manager.config["server"]["host"] == "100.124.8.85"
        assert manager.config["server"]["port"] == 6543
        assert manager.config["hotkey"]["key"] == "left_ctrl+left_alt"
        assert manager.config["hotkey"]["distinguish_left_right"] is True
    
    def test_save_and_load(self, tmp_path):
        """测试配置保存和加载"""
        config_path = tmp_path / "test_save.json"
        
        # 创建并修改配置
        manager1 = ConfigManager(config_path=str(config_path))
        manager1.set("server.host", "192.168.1.1")
        manager1.set("server.port", 8080)
        manager1.save()
        
        # 重新加载
        manager2 = ConfigManager(config_path=str(config_path))
        
        assert manager2.get("server.host") == "192.168.1.1"
        assert manager2.get("server.port") == 8080
    
    def test_get_nested_value(self, tmp_path):
        """测试嵌套值获取"""
        config_path = tmp_path / "test_nested.json"
        manager = ConfigManager(config_path=str(config_path))
        
        assert manager.get("server.host") == "100.124.8.85"
        assert manager.get("hotkey.distinguish_left_right") is True
        assert manager.get("audio.language") == "auto"
    
    def test_get_nonexistent_key(self, tmp_path):
        """测试获取不存在的键"""
        config_path = tmp_path / "test_nonexistent.json"
        manager = ConfigManager(config_path=str(config_path))
        
        assert manager.get("nonexistent.key") is None
        assert manager.get("nonexistent.key", "default") == "default"
    
    def test_set_nested_value(self, tmp_path):
        """测试设置嵌套值"""
        config_path = tmp_path / "test_set.json"
        manager = ConfigManager(config_path=str(config_path))
        
        manager.set("ui.opacity", 0.5)
        assert manager.get("ui.opacity") == 0.5
        
        manager.set("hotkey.key", "right_ctrl")
        assert manager.get("hotkey.key") == "right_ctrl"
    
    def test_load_corrupted_config(self, tmp_path):
        """测试加载损坏的配置文件"""
        config_path = tmp_path / "corrupted.json"
        config_path.write_text("{ invalid json }")
        
        manager = ConfigManager(config_path=str(config_path))
        
        # 应该回退到默认配置
        assert manager.config["server"]["host"] == "100.124.8.85"
        assert manager.config["server"]["port"] == 6543
    
    def test_load_from_existing_config(self, tmp_path):
        """测试从已存在的配置文件加载"""
        config_path = tmp_path / "existing.json"
        custom_config = {
            "server": {"host": "custom.host.com", "port": 9999},
            "hotkey": {"key": "f13", "distinguish_left_right": False},
            "ui": {"start_minimized": True},
            "audio": {"device": "default", "language": "en"}
        }
        config_path.write_text(json.dumps(custom_config))
        
        manager = ConfigManager(config_path=str(config_path))
        
        assert manager.get("server.host") == "custom.host.com"
        assert manager.get("server.port") == 9999
        assert manager.get("hotkey.key") == "f13"
        assert manager.get("hotkey.distinguish_left_right") is False
        assert manager.get("ui.start_minimized") is True
        assert manager.get("audio.language") == "en"
    
    def test_properties(self, tmp_path):
        """测试便捷属性访问器"""
        config_path = tmp_path / "test_props.json"
        manager = ConfigManager(config_path=str(config_path))
        
        # server_host 属性
        assert manager.server_host == "100.124.8.85"
        manager.server_host = "new.host.com"
        assert manager.server_host == "new.host.com"
        
        # hotkey 属性
        assert manager.hotkey == "left_ctrl+left_alt"
        manager.hotkey = "right_alt+v"
        assert manager.hotkey == "right_alt+v"
        
        # opacity 属性
        assert manager.opacity == 0.8
        manager.opacity = 0.5
        assert manager.opacity == 0.5
    
    def test_merge_with_defaults_partial(self, tmp_path):
        """测试部分配置与默认合并"""
        config_path = tmp_path / "partial.json"
        partial_config = {
            "server": {"host": "partial.host.com"}
        }
        config_path.write_text(json.dumps(partial_config))
        
        manager = ConfigManager(config_path=str(config_path))
        
        # 自定义值
        assert manager.get("server.host") == "partial.host.com"
        # 默认值
        assert manager.get("server.port") == 6543
        assert manager.get("hotkey.key") == "left_ctrl+left_alt"
    
    def test_config_manager_equality(self, tmp_path):
        """测试配置管理器相等性"""
        config_path = tmp_path / "equality.json"
        
        manager1 = ConfigManager(config_path=str(config_path))
        manager2 = ConfigManager(config_path=str(config_path))
        
        # 相同配置路径，内容应该相同
        assert manager1.config == manager2.config


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
