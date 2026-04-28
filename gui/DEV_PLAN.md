# Voice Input Framework — Tauri 客户端全功能移植计划

## 旧版 (Python) 配置文件兼容

**旧版位置**: `~/.voice_input_config.json`

**格式**:
```json
{
  "server": {"host": "localhost", "port": 6544},
  "hotkey": {"key": "left_ctrl+left_alt", "distinguish_left_right": true},
  "ui": {"start_minimized": false, "use_floating_indicator": true, "use_tray": true, "opacity": 0.8},
  "audio": {"device": null, "language": "auto"},
  "llm": {"enabled": true}
}
```

**兼容策略**:
- Tauri 版首次启动时检查 `~/.voice_input_config.json` 是否存在
- 若存在 → 自动导入并转换到 Tauri 格式（`~/.config/voice-input/config.json` 或 Tauri app_data_dir）
- 支持 UI 中的"手动导入"按钮
- 转换后重命名旧文件为 `.voice_input_config.json.bak`

---

## 📐 实施顺序

```
Phase 1 ───────────────────── (2-3 周)
  ├── 1. 录音改进 (cpal + 设备选择)
  ├── 4. 配置持久化 (依赖 1)
  ├── 2. LLM 管理  
  ├── 6. 结果管理增强
  ├── 5. WebSocket (可选开始)
  └── 3. 自动输入

Phase 2 ───────────────────── (1-2 周)
  ├── 9. 托盘菜单
  ├── 10. 最小化到托盘
  ├── 7. 悬浮指示器
  ├── 8. 处理指示器
  └── 11. 音频电平

Phase 3 ───────────────────── (1-2 周)
  ├── 12. 热键自定义
  ├── 13. 左右键区分
  ├── 14. 热键预设
  ├── 15. 开机自启动
  └── 16. 启动模式

Phase 4 ───────────────────── (按需)
  ├── 17. 更新检查
  ├── 18. 焦点管理
  ├── 19. 光标追踪
  ├── 20. 通知
  ├── 21. Streaming
  └── 22. 多语言
```
