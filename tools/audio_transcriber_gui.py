#!/usr/bin/env python3
"""
Voice Input Framework - 离线音频转写 GUI (流式输出)

上传音频文件，分段转写，文字逐步显示在界面上。
依赖: pip install httpx numpy
       (音频解码) pip install pydub 或 soundfile
"""
import os, sys, json, threading, time, base64, math
import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox, ttk

import httpx
import numpy as np

# ── 配置 ──
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audiogui_config.json")
DEFAULT_SERVER = os.environ.get("STT_SERVER", "http://localhost:9100")

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                return json.load(f).get("server", DEFAULT_SERVER)
        except Exception:
            pass
    return DEFAULT_SERVER


def save_config(server_url):
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump({"server": server_url}, f)
    except Exception:
        pass


def load_audio(path: str) -> np.ndarray:
    """加载音频文件，返回 16kHz mono float32 数组"""
    import io
    # pydub (支持 MP3/M4A/WAV/FLAC/OGG)
    try:
        from pydub import AudioSegment
        seg = AudioSegment.from_file(path)
        seg = seg.set_frame_rate(16000).set_channels(1)
        raw = seg.raw_data
        return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    except ImportError:
        pass
    # soundfile (支持 WAV/FLAC/OGG)
    try:
        import soundfile as sf
        data, sr = sf.read(path)
        if len(data.shape) > 1:
            data = data.mean(axis=1)
        if sr != 16000:
            from scipy import signal
            data = signal.resample(data, int(len(data) * 16000 / sr))
        return data.astype(np.float32)
    except ImportError:
        pass
    raise ImportError("需要安装 pydub 或 soundfile 来解码音频: pip install pydub")


def audio_to_base64(audio_array: np.ndarray) -> str:
    """将 float32 数组编码为 base64 PCM16 字符串"""
    int16 = (audio_array * 32768.0).astype(np.int16)
    return base64.b64encode(int16.tobytes()).decode()


class AudioTranscriberGUI:
    def __init__(self):
        self.window = tk.Tk()
        self.window.title("🦞 Voice Input - 音频转写 (流式)")
        self.window.geometry("750x700")
        self.window.minsize(600, 500)

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

        self._running = False
        self._stop_flag = False

        self._build_ui()
        self._check_server()
        self.window.protocol("WM_DELETE_WINDOW", self._on_close)

    def _c(self, widget_type, **kwargs):
        kwargs.setdefault("bg", self.bg)
        kwargs.setdefault("fg", self.fg)
        kwargs.setdefault("relief", tk.FLAT)
        kwargs.setdefault("bd", 0)
        return widget_type(**kwargs)

    def _btn(self, master, text, cmd, bg=None, width=None):
        return tk.Button(master, text=text, command=cmd,
                         bg=bg or self.accent, fg="#1e1e2e",
                         activebackground="#74c7ec",
                         relief=tk.RAISED, bd=1,
                         font=("", 10, "bold"),
                         width=width, cursor="hand2")

    def _build_ui(self):
        self._c(tk.Label, master=self.window, text="🦞 音频文件转写 (流式)",
                font=("", 16, "bold")).pack(pady=(15, 2))
        self._c(tk.Label, master=self.window,
                text="分段转写，文字逐步显示", font=("", 9),
                fg="#a6adc8").pack(pady=(0, 10))

        # 服务器配置
        cf = tk.Frame(self.window, bg=self.bg)
        cf.pack(fill=tk.X, padx=20, pady=5)
        self._c(tk.Label, master=cf, text="服务器:",
                width=10, anchor=tk.W, font=("", 9)).pack(side=tk.LEFT)
        tk.Entry(cf, textvariable=self.server_url, bg=self.input_bg,
                 fg=self.fg, insertbackground=self.fg,
                 relief=tk.FLAT, bd=3).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self._btn(cf, "🔄 检测", self._check_server,
                  bg="#a6e3a1", width=8).pack(side=tk.RIGHT)

        # 状态 + 配置
        sf2 = tk.Frame(self.window, bg=self.bg)
        sf2.pack(fill=tk.X, padx=20, pady=(0, 5))
        self.model_label = self._c(tk.Label, master=sf2,
                                    textvariable=self.model_name,
                                    fg="#a6adc8", font=("", 9), anchor=tk.W)
        self.model_label.pack(side=tk.LEFT)

        # 流式信息提示
        self._c(tk.Label, master=sf2, text="  Qwen3 原生流式", font=("", 9),
                fg="#a6adc8").pack(side=tk.LEFT, padx=(10, 2))

        # 文件
        ff = tk.Frame(self.window, bg=self.bg)
        ff.pack(fill=tk.X, padx=20, pady=10)
        self._c(tk.Label, master=ff, text="音频:",
                width=10, anchor=tk.W, font=("", 9)).pack(side=tk.LEFT)
        tk.Entry(ff, textvariable=self.file_path, bg=self.input_bg,
                 fg=self.fg, insertbackground=self.fg,
                 relief=tk.FLAT, bd=3).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._btn(ff, "📂 浏览...", self._choose_file,
                  width=10).pack(side=tk.RIGHT, padx=(5, 0))

        # 按钮
        bf = tk.Frame(self.window, bg=self.bg)
        bf.pack(fill=tk.X, padx=20, pady=5)
        self.start_btn = self._btn(bf, "▶ 开始转写", self._start_transcribe, width=14)
        self.start_btn.pack(side=tk.LEFT, padx=5)
        self.stop_btn = self._btn(bf, "⏹ 停止", self._stop_transcribe,
                                  bg="#f38ba8", width=10)
        self.stop_btn.pack(side=tk.LEFT, padx=5)
        self.stop_btn.config(state=tk.DISABLED)
        self._btn(bf, "📋 复制", self._copy_result,
                  bg="#a6e3a1", width=8).pack(side=tk.LEFT, padx=5)
        self._btn(bf, "🗑 清空", self._clear,
                  bg="#f38ba8", width=8).pack(side=tk.RIGHT, padx=5)

        # 进度
        self.progress = ttk.Progressbar(self.window, mode="determinate", length=710)
        self.progress.pack(padx=20, pady=(0, 5), fill=tk.X)

        # 状态
        self._c(tk.Label, master=self.window, textvariable=self.status_text,
                fg="#a6adc8", font=("", 9)).pack(pady=(0, 5))

        # 结果区域
        rf = tk.Frame(self.window, bg=self.bg)
        rf.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 15))
        self._c(tk.Label, master=rf, text="📝 转写结果 (实时更新):",
                font=("", 10, "bold")).pack(fill=tk.X, pady=(0, 5))
        self.result_text = scrolledtext.ScrolledText(
            rf, wrap=tk.WORD, bg=self.input_bg, fg=self.fg,
            insertbackground=self.fg, font=("Consolas", 11) if sys.platform == "win32" else ("", 11),
            relief=tk.FLAT, bd=3, padx=10, pady=10)
        self.result_text.pack(fill=tk.BOTH, expand=True)

    def _check_server(self):
        self.model_name.set("正在检测...")
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
                                active = next((m for m in models if m.get("is_active")), models[0])
                                self.window.after(0, lambda: self.model_name.set(
                                    f"✅ {active.get('model_name', '?')}"
                                ))
                                self.window.after(0, lambda: self.model_label.config(fg="#a6e3a1"))
                                return
                    except Exception:
                        pass
                    self.window.after(0, lambda: self.model_name.set("✅ 服务器正常"))
                    self.window.after(0, lambda: self.model_label.config(fg="#a6e3a1"))
                else:
                    self.window.after(0, lambda: self.model_name.set(f"❌ HTTP {r.status_code}"))
                    self.window.after(0, lambda: self.model_label.config(fg="#f38ba8"))
            except Exception as e:
                self.window.after(0, lambda: self.model_name.set(f"❌ {str(e)[:40]}"))
                self.window.after(0, lambda: self.model_label.config(fg="#f38ba8"))

        threading.Thread(target=check, daemon=True).start()

    def _choose_file(self):
        path = filedialog.askopenfilename(
            title="选择音频文件",
            filetypes=[("音频文件", "*.wav *.mp3 *.m4a *.flac *.ogg *.aac *.opus"),
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

        self._running = True
        self._stop_flag = False
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.result_text.delete("1.0", tk.END)
        self.progress["value"] = 0

        thread = threading.Thread(target=self._do_transcribe_ws, args=(path,), daemon=True)
        thread.start()

    def _stop_transcribe(self):
        self._stop_flag = True
        self.status_text.set("⏹ 正在停止...")

    def _do_transcribe_ws(self, path):
        url = f"{self.server_url.get().rstrip('/')}/ws/stream"

        try:
            audio = load_audio(path)
        except Exception as e:
            self.window.after(0, self._show_error, f"音频解码失败: {e}\n请安装 pydub: pip install pydub")
            self.window.after(0, self._finish)
            return

        total_samples = len(audio)
        sample_rate = 16000
        total_seconds = total_samples / sample_rate
        segment_seconds = 30  # 发送块大小（不影响流式断句）
        self.window.after(0, lambda: self.status_text.set(
            f"⏳ 音频 {total_seconds/60:.0f}分, 连接服务器..."
        ))

        try:
            with httpx.Client(timeout=900) as client:
                with client.ws(url) as ws:
                    ready = ws.receive_text()
                    ready_data = json.loads(ready)
                    if ready_data.get("model"):
                        self.window.after(0, lambda m=ready_data.get("model"):
                            self.model_name.set(f"✅ {m}"))

                    # 发送语言配置
                    ws.send_text(json.dumps({
                        "type": "config",
                        "language": self.lang.get(),
                        "return_timestamps": False,
                    }))
                    ws.receive_text()  # config_ack

                    # 按 segment 发送音频块
                    samples_per_segment = segment_seconds * sample_rate
                    total_segments = math.ceil(total_samples / samples_per_segment)
                    self.window.after(0, lambda: self.progress.configure(maximum=total_segments))
                    self.window.after(0, lambda: self.status_text.set(
                        f"⏳ 发送音频 ({total_segments} 段)..."
                    ))

                    seg_count = 0
                    for start in range(0, total_samples, samples_per_segment):
                        if self._stop_flag:
                            break
                        end = min(start + samples_per_segment, total_samples)
                        chunk = audio[start:end]
                        b64 = audio_to_base64(chunk)
                        ws.send_text(json.dumps({"type": "audio", "data": b64}))
                        seg_count += 1
                        if seg_count % 5 == 0:
                            self.window.after(0, lambda: self.status_text.set(
                                f"⏳ 已发送 {seg_count}/{total_segments} 段..."
                            ))

                    # 结束发送，开始读取流式结果
                    ws.send_text(json.dumps({"type": "end"}))
                    self.window.after(0, lambda: self.status_text.set("📝 接收转写结果..."))

                    # 读取流式结果
                    result_parts = []
                    while True:
                        try:
                            msg = ws.receive_text()
                            data = json.loads(msg)
                            t = data.get("type", "")

                            if t == "stt_result":
                                text = data.get("text", "")
                                if text:
                                    result_parts.append(text)
                                    self.window.after(0, lambda t=text: self._append_stream(t))

                            elif t == "done":
                                segments = data.get("segments", 0)
                                total_len = sum(len(p) for p in result_parts)
                                self.window.after(0, lambda: self.status_text.set(
                                    f"✅ 完成! {segments} segments, {total_len} 字"
                                ))
                                break

                            elif t == "error":
                                err = data.get("error_message", "")
                                self.window.after(0, lambda: self.result_text.insert(
                                    tk.END, f"\n❌ 服务器错误: {err}\n"))
                                break

                        except Exception:
                            break

        except Exception as e:
            self.window.after(0, lambda: self.status_text.set(f"❌ 连接失败: {e}"))
        finally:
            self.window.after(0, self._finish)

    def _append_stream(self, text):
        self.result_text.insert(tk.END, text)
        self.result_text.see(tk.END)
        self.result_text.update_idletasks()

    def _append_chunk(self, text, idx, total):
        self.result_text.insert(tk.END, f"\n─── 第 {idx}/{total} 段 ───\n{text}\n")
        self.result_text.see(tk.END)

    def _append_error(self, text, idx, total):
        self.result_text.insert(tk.END, f"\n─── 第 {idx}/{total} 段 ───\n{text}\n")
        self.result_text.see(tk.END)

    def _finish(self):
        self._running = False
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.progress["value"] = 0

    def _show_error(self, msg):
        self.result_text.insert("1.0", f"❌ {msg}\n")
        self._finish()

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
        self._stop_flag = True
        save_config(self.server_url.get())
        self.window.destroy()

    def run(self):
        self.window.mainloop()


if __name__ == "__main__":
    AudioTranscriberGUI().run()
