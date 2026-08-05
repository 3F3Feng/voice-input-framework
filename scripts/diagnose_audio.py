#!/usr/bin/env python3
"""macOS 录音诊断脚本 — 验证 sounddevice 采集链路

用法(在 Mac 上,激活项目 venv):
    python scripts/diagnose_audio.py
"""

import sys
import time

import numpy as np


def main():
    print("Python:", sys.version.split()[0])
    try:
        import sounddevice as sd
    except Exception as e:
        print(f"❌ sounddevice 导入失败: {e}")
        return

    print("\n=== 设备列表 ===")
    for i, dev in enumerate(sd.query_devices()):
        mark = ""
        try:
            di = sd.default.device[0] if isinstance(sd.default.device, tuple) else sd.default.device
            if i == di:
                mark = "  ← 默认输入"
        except Exception:
            pass
        print(
            f"  [{i}] {dev['name']} (in={dev['max_input_channels']}, out={dev['max_output_channels']}){mark}"
        )

    # 测每个有输入通道的设备
    for i, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] <= 0:
            continue
        print(f"\n=== 测试设备 [{i}] {dev['name']} ===")
        buffer = []
        errors = []

        def cb(indata, frames, t, status):
            if status:
                errors.append(str(status))
            buffer.append(indata.copy())

        try:
            stream = sd.InputStream(
                device=i,
                samplerate=16000,
                channels=1,
                dtype="int16",
                blocksize=1024,
                callback=cb,
            )
            stream.start()
            time.sleep(2.0)  # 采集 2 秒
            stream.stop()
            stream.close()
        except Exception as e:
            print(f"  ❌ InputStream 失败: {type(e).__name__}: {e}")
            continue

        if errors:
            print(f"  ⚠️ 回调状态错误: {errors[:3]}")
        if not buffer:
            print("  ❌ 回调未产生任何数据(静音/未授权/设备被占用)")
            continue
        all_data = np.concatenate(buffer)
        rms = float(np.sqrt(np.mean(all_data.astype(np.float32) ** 2)))
        peak = float(np.max(np.abs(all_data.astype(np.float32))))
        print(f"  ✅ 收到 {len(buffer)} 块, {all_data.size} 样本")
        print(f"     RMS={rms:.1f}  Peak={peak:.1f}  (说话时应明显增大)")
        print(f"     诊断: {'✅ 有声音数据' if rms > 50 else '❌ 数据接近静音'}")


if __name__ == "__main__":
    main()
