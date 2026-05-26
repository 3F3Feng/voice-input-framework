#!/usr/bin/env python3
"""
Voice Input Framework - 离线音频转写 GUI (跨平台)

上传音频文件到 STT 服务器，显示实时进度和转写结果。

用法:
  python3 tools/audio_transcriber_gui.py

依赖:
  pip install httpx
  (Windows: pip install httpx)

服务器默认地址: http://localhost:9100
可通过环境变量 STT_SERVER 或在 GUI 中修改。
"""
import os, sys, json, threading, time
import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox, ttk

import httpx

# ── 配置 ──
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audiogui_config.json")
DEFAULT_SERVER = os.environ.get("STT_SERVER", "http://localhost:9100")


def load_config():
    """加载保存的服务器地址"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                cfg = json.load(f)
            return cfg.get("server", DEFAULT_SERVER)
        except Exception:
            pass
    return DEFAULT_SERVER


def save_config(server_url):
    """保存服务器地址"""
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump({"server": server_url}, f)
    except Exception:
        pass


class AudioTranscriberGUI:
    def __init__(self):
        self.window = tk.Tk()
        self.window.title("🦞 Voice Input - 音频转写")
        self.window.geometry("750x680")
        self.window.minsize(600, 500)

        # 跨平台暗色主题兼容
        self.bg = "#1e1e2e"
        self.fg = "#cdd6f4"
        self.input_bg = "#313244"
        self.accent = "#89b4fa"
        self.window.configure(bg=self.bg)

        self.file_path = tk.StringVar()
        self.server_url = tk.StringVar(value=load_config())
        self.status_text = tk.StringVar(value="就绪")
        self.model_name = tk.StringVar(value="检测中...")
        self.lang = tk.StringVar(value="auto")

        self._build_ui()
        self._check_server()

        # 关闭时保存配置
        self.window.protocol("WM_DELETE_WINDOW", self._on_close)

    def _c(self, widget_type, **kwargs):
        """统一设置跨平台暗色主题"""
        kwargs.setdefault("bg", self.bg)
        kwargs.setdefault("fg", self.fg)
        kwargs.setdefault("relief", tk.FLAT)
        kwargs.setdefault("bd", 0)
        return widget_type(**kwargs)

    def _btn(self, master, text, cmd, bg=None, width=None):
        """统一按钮样式"""
        btn = tk.Button(master, text=text, command=cmd,
                        bg=bg or self.accent, fg="#1e1e2e",
                        activebackground="#74c7ec",
                        relief=tk.RAISED, bd=1,
                        font=("", 10, "bold"),
                        width=width, cursor="hand2")
        return btn

    def _build_ui(self):
        # Header
        self._c(tk.Label, master=self.window,
               text="🦞 音频文件转写", font=("", 16, "bold")
               ).pack(pady=(15, 2))
        self._c(tk.Label, master=self.window,
               text="选择音频文件，上传到 STT 服务器进行语音识别",
               font=("", 9), fg="#a6adc8"
               ).pack(pady=(0, 10))

        # ── 服务器配置 ──
        cfg_frame = tk.Frame(self.window, bg=self.bg)
        cfg_frame.pack(fill=tk.X, padx=20, pady=5)
        self._c(tk.Label, master=cfg_frame, text="STT 服务器:",
               width=10, anchor=tk.W, font=("", 9)).pack(side=tk.LEFT)
        self.server_entry = tk.Entry(cfg_frame, textvariable=self.server_url,
                                      bg=self.input_bg, fg=self.fg,
                                      insertbackground=self.fg,
                                      relief=tk.FLAT, bd=3,
                                      font=("", 9))
        self.server_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self._btn(cfg_frame, "🔄 检测", self._check_server,
                 bg="#a6e3a1", width=8).pack(side=tk.RIGHT)

        # Server status
        status_frame = tk.Frame(self.window, bg=self.bg)
        status_frame.pack(fill=tk.X, padx=20, pady=(0, 5))
        self.model_label = self._c(tk.Label, master=status_frame,
                                   textvariable=self.model_name,
                                   fg="#a6adc8", font=("", 9),
                                   anchor=tk.W)
        self.model_label.pack(fill=tk.X)

        # ── 语言选择 ──
        lang_frame = tk.Frame(self.window, bg=self.bg)
        lang_frame.pack(fill=tk.X, padx=20, pady=5)
        self._c(tk.Label, master=lang_frame, text="语言:",
               width=10, anchor=tk.W, font=("", 9)).pack(side=tk.LEFT)
        lang_menu = tk.OptionMenu(lang_frame, self.lang,
                                  "auto", "zh", "en", "ja", "ko", "fr", "de")
        lang_menu.config(bg=self.input_bg, fg=self.fg,
                         relief=tk.FLAT, bd=0,
                         activebackground="#45475a",
                         activeforeground=self.fg)
        lang_menu.pack(side=tk.LEFT)

        # 时间戳选项
        self.timestamps_var = tk.BooleanVar(value=True)
        self._c(tk.Checkbutton, master=lang_frame,
               text="返回时间戳", variable=self.timestamps_var,
               selectcolor=self.input_bg,
               font=("", 9)).pack(side=tk.LEFT, padx=(15, 0))

        # ── 文件选择 ──
        file_frame = tk.Frame(self.window, bg=self.bg)
        file_frame.pack(fill=tk.X, padx=20, pady=10)
        self._c(tk.Label, master=file_frame, text="音频文件:",
               width=10, anchor=tk.W, font=("", 9)).pack(side=tk.LEFT)
        tk.Entry(file_frame, textvariable=self.file_path,
                 bg=self.input_bg, fg=self.fg,
                 insertbackground=self.fg,
                 relief=tk.FLAT, bd=3).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._btn(file_frame, "📂 浏览...", self._choose_file,
                 width=10).pack(side=tk.RIGHT, padx=(5, 0))

        # ── 操作按钮 ──
        btn_frame = tk.Frame(self.window, bg=self.bg)
        btn_frame.pack(fill=tk.X, padx=20, pady=8)
        self.transcribe_btn = self._btn(btn_frame, "▶ 开始转写",
                                        self._start_transcribe, width=14)
        self.transcribe_btn.pack(side=tk.LEFT, padx=5)

        self._btn(btn_frame, "📋 复制结果", self._copy_result,
                 bg="#a6e3a1", width=12).pack(side=tk.LEFT, padx=5)
        self._btn(btn_frame, "🗑 清空", self._clear,
                 bg="#f38ba8", width=8).pack(side=tk.RIGHT, padx=5)

        # ── 进度条 ──
        self.progress = ttk.Progressbar(self.window, mode="indeterminate",
                                        length=710)
        self.progress.pack(padx=20, pady=(0, 5), fill=tk.X)

        # ── 状态栏 ──
        self._c(tk.Label, master=self.window, textvariable=self.status_text,
               fg="#a6adc8", font=("", 9)).pack(pady=(0, 5))

        # ── 结果区域 ──
        result_frame = tk.Frame(self.window, bg=self.bg)
        result_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 15))
        self._c(tk.Label, master=result_frame, text="📝 转写结果:",
               font=("", 10, "bold")).pack(fill=tk.X, pady=(0, 5))

        self.result_text = scrolledtext.ScrolledText(
            result_frame, wrap=tk.WORD,
            bg=self.input_bg, fg=self.fg,
            insertbackground=self.fg,
            font=("Consolas", 11) if sys.platform == "win32" else ("", 11),
            relief=tk.FLAT, bd=3, padx=10, pady=10)
        self.result_text.pack(fill=tk.BOTH, expand=True)

    def _check_server(self):
        """检测服务器连通性"""
        self.model_name.set("正在检测服务器...")
        self.model_label.config(fg="#f9e2af")

        def check():
            url = self.server_url.get().rstrip("/")
            try:
                r = httpx.get(f"{url}/health", timeout=5)
                if r.status_code == 200:
                    try:
                        mr = httpx.get(f"{url}/models", timeout=5)
                        if mr.status_code == 200:
                            models = mr.json()
                            if models:
                                active = next((m for m in models
                                               if m.get("is_active")), models[0])
                                self.window.after(0, lambda:
                                    self.model_name.set(f"✅ {active.get('model_name', '?')}")
                                )
                                self.window.after(0, lambda:
                                    self.model_label.config(fg="#a6e3a1")
                                )
                                return
                        self.window.after(0, lambda:
                            self.model_name.set(f"✅ 服务器正常 (unable to list models)")
                        )
                    except Exception:
                        self.window.after(0, lambda:
                            self.model_name.set("✅ 服务器正常")
                        )
                    self.window.after(0, lambda: self.model_label.config(fg="#a6e3a1"))
                else:
                    self.window.after(0, lambda:
                        self.model_name.set(f"❌ 服务器异常 (HTTP {r.status_code})")
                    )
                    self.window.after(0, lambda: self.model_label.config(fg="#f38ba8"))
            except Exception as e:
                self.window.after(0, lambda:
                    self.model_name.set(f"❌ {str(e)[:40]}")
                )
                self.window.after(0, lambda: self.model_label.config(fg="#f38ba8"))

        threading.Thread(target=check, daemon=True).start()

    def _choose_file(self):
        path = filedialog.askopenfilename(
            title="选择音频文件",
            filetypes=[("音频文件", "*.wav *.mp3 *.m4a *.flac *.ogg *.aac *.opus *.wma"),
                       ("所有文件", "*.*")]
        )
        if path:
            self.file_path.set(path)
            size_mb = os.path.getsize(path) / 1024 / 1024
            self.status_text.set(f"已选择: {os.path.basename(path)} ({size_mb:.1f} MB)")

    def _start_transcribe(self):
        path = self.file_path.get()
        if not path:
            messagebox.showwarning("提示", "请先选择音频文件")
            return
        if not os.path.exists(path):
            messagebox.showerror("错误", "文件不存在")
            return

        self.transcribe_btn.config(state=tk.DISABLED)
        self.progress.start()
        self.status_text.set("⏳ 正在上传并转写，请稍候...")
        self.result_text.delete("1.0", tk.END)
        thread = threading.Thread(target=self._do_transcribe, args=(path,), daemon=True)
        thread.start()

    def _do_transcribe(self, path):
        start = time.time()
        size_mb = os.path.getsize(path) / 1024 / 1024
        try:
            with open(path, "rb") as f:
                files = {"file": (os.path.basename(path), f, "audio/wav")}
                data = {
                    "language": self.lang.get(),
                    "return_timestamps": "true" if self.timestamps_var.get() else "false"
                }
                url = f"{self.server_url.get().rstrip('/')}/transcribe"

                with httpx.Client(timeout=900) as client:
                    resp = client.post(url, files=files, data=data)

            elapsed = time.time() - start
            if resp.status_code == 200:
                result = resp.json()
                text = result.get("text", "")
                latency = result.get("stt_latency_ms", 0)
                conf = result.get("confidence", 1.0)
                model = result.get("model", "?")
                lang_out = result.get("language", "?")

                self.window.after(0, self._show_result,
                                 text, latency, conf, model, lang_out, elapsed, size_mb)
            else:
                self.window.after(0, self._show_error,
                                 f"服务器错误 ({resp.status_code}):\n{resp.text[:300]}")
        except Exception as e:
            self.window.after(0, self._show_error, f"请求失败: {e}")
        finally:
            self.window.after(0, self.progress.stop)
            self.window.after(0, lambda: self.transcribe_btn.config(state=tk.NORMAL))

    def _show_result(self, text, latency, conf, model, lang_out, elapsed, size_mb):
        self.result_text.insert("1.0", text)
        summary = (f"\n\n{'─'*60}\n"
                   f"📁 文件: {size_mb:.1f} MB  |  "
                   f"⏱ 耗时: {elapsed:.1f}s\n"
                   f"🎙 模型: {model}  |  语言: {lang_out}  |  "
                   f"📊 置信度: {conf:.2%}\n"
                   f"📝 字数: {len(text)}  |  "
                   f"⚡ STT延迟: {latency:.0f}ms")
        self.result_text.insert(tk.END, summary)
        self.result_text.see("1.0")
        self.status_text.set(f"✅ 完成! {elapsed:.1f}s, {len(text)} 字")

    def _show_error(self, msg):
        self.result_text.insert("1.0", f"❌ 转写失败\n\n{msg}")
        self.status_text.set("❌ 转写失败")

    def _copy_result(self):
        text = self.result_text.get("1.0", tk.END).strip()
        if text:
            self.window.clipboard_clear()
            self.window.clipboard_append(text)
            self.status_text.set("📋 已复制到剪贴板")
        else:
            self.status_text.set("没有内容可复制")

    def _clear(self):
        self.result_text.delete("1.0", tk.END)
        self.status_text.set("已清空")

    def _on_close(self):
        save_config(self.server_url.get())
        self.window.destroy()

    def run(self):
        self.window.mainloop()


if __name__ == "__main__":
    AudioTranscriberGUI().run()
