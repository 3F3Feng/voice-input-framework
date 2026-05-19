#!/usr/bin/env python3
"""
Voice Input Framework - 客户端应用控制器

协调 AudioRecorder + SttClient/LlmClient + MainWindow/TrayMenu。
处理热键、录音、网络通信、UI 事件循环。
"""

import asyncio
import json
import logging
import os
import queue
import threading
import time
from typing import Optional

import numpy as np

from client.audio import AudioRecorder, AUDIO_SAMPLE_RATE
from client.network import SttClient, LlmClient
from client.ui import MainWindow, TrayMenu
from client.config_manager import ConfigManager
from client.hotkey_manager import HotkeyManager, HotkeyPresets
from client.cursor_tracker import get_input_cursor_position, restore_focus_later, CLIPBOARD_METHOD

logger = logging.getLogger(__name__)


class VoiceInputApp:
    """语音输入应用控制器"""

    def __init__(self, server_host: str = None, server_port: int = None):
        self.config = ConfigManager()
        self.server_host = server_host or self.config.server_host
        self.server_port = server_port or self.config.server_port

        # 服务客户端
        self.stt = SttClient(self.server_host, self.server_port)
        self.llm = LlmClient(f"http://{self.server_host}:6545")

        # 音频
        self.audio = AudioRecorder()
        self.selected_mic: Optional[int] = None

        # UI
        self.window: Optional[MainWindow] = None
        self.tray: Optional[TrayMenu] = None

        # 状态
        self.is_running = False
        self._hotkey_pressed = False

        # 异步
        self.async_loop: Optional[asyncio.AbstractEventLoop] = None
        self.loop_thread: Optional[threading.Thread] = None

        # 热键
        self.hotkey_manager = HotkeyManager()

        # UI 回调：音频电平
        self._get_audio_level = self.audio.get_level

    def _make_audio_level_callback(self):
        """音量回调包装（兼容悬浮指示器接口）"""
        return lambda: (self._get_audio_level(), self._get_audio_level())

    # ── 服务器连接 ──

    async def _connect(self):
        await self.stt.connect()
        if self.stt.is_connected:
            models = await self.stt.get_models()
            if self.window:
                self.window.set_status(f"已连接 {self.server_host}:{self.server_port}", "green")
                self.window.update_model_list(
                    [m["name"] for m in models],
                    self.stt.current_model or (models[0]["name"] if models else ""),
                )
        else:
            if self.window:
                self.window.set_status("连接失败", "red")

    # ── 模型管理 ──

    async def _fetch_models(self):
        if not self.stt.is_connected:
            await self._connect()
        models = await self.stt.get_models()
        if self.window:
            names = [m["name"] for m in models]
            self.window.update_model_list(names, self.stt.current_model or "")
            self.window.set_status(f"已加载 {len(models)} 个模型", "green")

    async def _switch_model(self, name: str):
        if self.window:
            self.window.set_status(f"切换模型: {name}...", "yellow")
        result = await self.stt.switch_model(name)
        if self.window:
            self.window.set_model_status(result.get("message", ""), "yellow")
        # 轮询加载状态
        await self._poll_model_loading(name)

    async def _poll_model_loading(self, name: str):
        for _ in range(30):
            status = await self.stt.get_model_status(name)
            if status.get("is_loaded"):
                if self.window:
                    self.window.set_model_status(f"模型 {name} 已加载 ✅", "green")
                return
            if not status.get("is_loading"):
                break
            await asyncio.sleep(1)
        if self.window:
            self.window.set_model_status(f"模型 {name} 加载超时", "red")

    # ── LLM 管理 ──

    async def _fetch_llm_models(self):
        models = await self.llm.get_models()
        if self.window:
            names = [m["name"] for m in models]
            current = self.llm.current_model or (models[0]["name"] if models else "")
            self.window.update_llm_model_list(names, current)

    async def _switch_llm_model(self, name: str):
        if self.window:
            self.window.update_llm_model_status(f"切换模型: {name}...", "cyan")
        result = await self.llm.switch_model(name)
        if self.window:
            self.window.update_llm_model_status(result.get("message", ""), "cyan")

    async def _load_prompt(self):
        prompt = await self.llm.load_prompt()
        if self.window:
            self.window.update_prompt(prompt)

    async def _save_prompt(self, text: str):
        await self.llm.save_prompt(text)
        if self.window:
            self.window.update_prompt_status("已保存 ✅", "green")

    # ── 录音和转录 ──

    async def _start_recording(self):
        self.audio.start_recording(device=self.selected_mic)
        self._hotkey_pressed = True
        if self.window:
            self.window.write_event_value("-REC-STARTED-", "")

    async def _stop_recording(self):
        self._hotkey_pressed = False
        if self.window:
            self.window.write_event_value("-REC-STOPPED-", "")

    def _on_recording_started(self):
        if hasattr(self.window, 'floating_indicator') and self.window.floating_indicator:
            pos = get_input_cursor_position()
            self.window.floating_indicator.show(pos)

    def _on_recording_stopped(self):
        if hasattr(self.window, 'floating_indicator') and self.window.floating_indicator:
            self.window.floating_indicator.hide()
        if hasattr(self.window, 'processing_indicator') and self.window.processing_indicator:
            self.window.processing_indicator.show()

    async def _process_audio(self):
        """处理已录制的音频"""
        audio_data = self.audio.stop_recording()
        if hasattr(self.window, 'processing_indicator') and self.window.processing_indicator:
            self.window.processing_indicator.hide()
        if not audio_data or len(audio_data) < 320:
            return

        # 通过 LLM 后处理或直接返回
        llm_enabled = self.config.llm_enabled
        self.window.set_status("正在识别...", "yellow")
        result = await self.stt.transcribe(audio_data, language="auto")
        text = result.get("text", "")

        if text and llm_enabled:
            self.window.set_status("正在 LLM 后处理...", "cyan")
            text = await self.llm.process(text)

        # 显示和输入
        if self.window:
            self.window.update_result(text)
            self.window.write_event_value("-AUTO-INPUT-", text)
            self.window.set_status("就绪", "green")

    # ── 文本输入 ──

    def _auto_input_text(self, text: str):
        if not text:
            return
        try:
            if CLIPBOARD_METHOD:
                import subprocess
                process = subprocess.Popen(
                    ["osascript", "-e",
                     f'tell application "System Events" to keystroke "{text}"'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
                process.wait(timeout=5)
            else:
                import pyautogui
                pyautogui.typewrite(text, interval=0.01)
        except Exception as e:
            logger.error(f"自动输入失败: {e}")

    # ── 异步线程 ──

    def _run_async_loop(self):
        self.async_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.async_loop)
        self.async_loop.run_forever()

    # ── 主循环 ──

    def run(self):
        """主事件循环"""
        devices = AudioRecorder.get_devices()
        self.window = MainWindow(
            config_manager=self.config,
            audio_devices=devices,
            audio_level_callback=self._make_audio_level_callback(),
        )
        _window = self.window.create_window(start_minimized=self.config.start_minimized)
        self.is_running = True

        # 启动异步线程
        self.loop_thread = threading.Thread(target=self._run_async_loop, daemon=True)
        self.loop_thread.start()
        time.sleep(0.1)

        # 设置托盘
        self.tray = TrayMenu(self.window._tray_manager) if _window else None

        # 热键
        self.hotkey_manager.set_hotkey(self.config.hotkey)
        self.hotkey_manager.on_press = lambda: self._async_task(self._start_recording())
        self.hotkey_manager.on_release = lambda: self._async_task(self._stop_recording())

        # 自动连接
        self._async_task(self._connect())

        # 主事件循环
        while self.is_running:
            try:
                if not _window:
                    break
                event, values = _window.read(timeout=100)
                if event == sg.WIN_CLOSED or event == "-EXIT-":
                    break
                self._handle_event(event, values, _window)
            except Exception as e:
                logger.error(f"Main loop error: {e}")

        self._cleanup()

    def _handle_event(self, event, values, window):
        if event == "-CONNECT-":
            self.server_host = values.get("-HOST-") or self.server_host
            port_str = values.get("-PORT-") or str(self.server_port)
            try:
                self.server_port = int(port_str)
            except ValueError:
                return
            self.stt = SttClient(self.server_host, self.server_port)
            self._async_task(self._connect())

        elif event == "-REFRESH-":
            self._async_task(self._fetch_models())

        elif event == "-SWITCH-":
            name = values.get("-MODEL-")
            if name:
                self._async_task(self._switch_model(name))

        elif event == "-REFRESH-LLM-":
            self._async_task(self._fetch_llm_models())

        elif event == "-SWITCH-LLM-":
            name = values.get("-LLM-MODEL-")
            if name:
                self._async_task(self._switch_llm_model(name))

        elif event == "-LLM-ENABLED-":
            self.config.llm_enabled = values["-LLM-ENABLED-"]
            self.config.save()

        elif event == "-LOAD-PROMPT-":
            self._async_task(self._load_prompt())

        elif event == "-SAVE-PROMPT-":
            self._async_task(self._save_prompt(values.get("-PROMPT-", "")))

        elif event == "-UPDATE-HOTKEY-":
            hotkey = values.get("-HOTKEY-") or self.config.hotkey
            self.hotkey_manager.set_hotkey(hotkey)
            window["-HOTKEY-"].update(hotkey)
            self.config.hotkey = hotkey
            self.config.save()
            self.window.log(f"快捷键已更新: {hotkey}")

        elif event == "-RECORD-HOTKEY-":
            self.hotkey_manager.start_recording(
                lambda k: window["-HOTKEY-"].update(k)
            )

        elif event == "-CLEAR-HOTKEY-":
            window["-HOTKEY-"].update("")

        elif event == "-PRESET-":
            name = values.get("-PRESET-")
            if name:
                preset = HotkeyPresets.get_hotkey(name)
                if preset:
                    window["-HOTKEY-"].update(preset)
                    self.window.log(f"预设 {name} 已应用: {preset}")

        elif event == "-APPLY-PRESET-":
            self._handle_event("-UPDATE-HOTKEY-", values, window)

        elif event == "-REC-STARTED-":
            self._on_recording_started()

        elif event == "-REC-STOPPED-":
            self._on_recording_stopped()
            self._async_task(self._process_audio())

        elif event == "-COPY-":
            result = window["-RESULT-"].get()
            if result:
                import subprocess
                subprocess.run(["pbcopy"], input=result.encode("utf-8"))
                self.window.log("已复制到剪贴板")

        elif event == "-CLEAR-":
            window["-RESULT-"].update("")

        elif event == "-INPUT-":
            text = window["-RESULT-"].get()
            self._auto_input_text(text)

        elif event == "-HIDE-":
            window.hide()
            if self.tray:
                self.tray.start()

        elif event == "-SHOW-":
            window.un_hide()

        elif event == "-DISTINGUISH-":
            val = values.get("-DISTINGUISH-")
            self.config.distinguish_left_right = val
            self.config.save()

        elif event == "-FLOATING-INDICATOR-":
            val = values.get("-FLOATING-INDICATOR-")
            self.config.use_floating_indicator = val
            self.config.save()

        elif event == "-START-MINIMIZED-":
            val = values.get("-START-MINIMIZED-")
            self.config.start_minimized = val
            self.config.save()

        elif event == "-CHECK-UPDATE-":
            self.window.write_event_value("-DONE-UPDATE-", "")
            self._async_task(self._check_update())

    async def _check_update(self):
        from client.update_checker import check_for_updates, format_version_message
        try:
            result = await check_for_updates()
            if result:
                msg = format_version_message(result)
                self.window.log(msg)
            else:
                self.window.log("已是最新版本")
        except Exception as e:
            self.window.log(f"检查更新失败: {e}")

    def _async_task(self, coro):
        if self.async_loop:
            asyncio.run_coroutine_threadsafe(coro, self.async_loop)

    def _cleanup(self):
        self.is_running = False
        if self.async_loop:
            self.async_loop.call_soon_threadsafe(self.async_loop.stop)
        if hasattr(self, 'window') and self.window and hasattr(self.window, '_window'):
            self.window.close()
        logger.info("客户端已关闭")
