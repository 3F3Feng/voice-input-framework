//! Audio capture using cpal with streaming support.
//! cpal::Stream is not Send, so we wrap it in an unsafe newtype.

use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use serde::{Deserialize, Serialize};
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::{Arc, Mutex};
use tokio::sync::mpsc;

use crate::i18n::t;
use crate::tr;

/// Audio device info for the frontend
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AudioDeviceInfo {
    pub name: String,
    pub channels: u16,
    pub default: bool,
}

// cpal::Stream is not Send, but on all platforms we target it is safe to send.
#[allow(dead_code)]
struct SendStream(Option<cpal::Stream>);
unsafe impl Send for SendStream {}

/// cpal-based audio capture with streaming channel support
pub struct AudioRecorder {
    selected_device: Option<String>,
    /// Device-native capture rate (diagnostics only).
    input_sample_rate: u32,
    /// Rate of the samples actually stored in `self.samples`. The capture
    /// callback resamples to 16 kHz before buffering, so this is always
    /// 16000 — returning `input_sample_rate` here made the batch path
    /// resample an already-16 kHz buffer a second time.
    buffer_sample_rate: u32,
    peak_level: Arc<AtomicU32>,
    is_recording: Arc<AtomicBool>,
    stream: Option<SendStream>,
    samples: Arc<Mutex<Vec<f32>>>,
    chunk_sender: Option<mpsc::UnboundedSender<Vec<u8>>>,
    chunk_receiver: Option<mpsc::UnboundedReceiver<Vec<u8>>>,
}

/// 录音过程中需要让用户知道、但不至于中断录音的问题(设备回落、设备断开)。
/// 在 cpal 的音频线程里被调用,所以必须 `Send + Sync`。
pub type WarningSink = Arc<dyn Fn(String) + Send + Sync>;

impl AudioRecorder {
    pub fn new() -> Self {
        Self {
            selected_device: None,
            input_sample_rate: 16000,
            buffer_sample_rate: 16000,
            peak_level: Arc::new(AtomicU32::new(0)),
            is_recording: Arc::new(AtomicBool::new(false)),
            stream: None,
            samples: Arc::new(Mutex::new(Vec::new())),
            chunk_sender: None,
            chunk_receiver: None,
        }
    }

    /// Create a streaming channel. Call this before start().
    ///
    /// 必须是无界通道。这个通道要到松手、`run_transcription` 起来之后才开始被
    /// 消费,整段录音期间只进不出。以前用的是容量 4096 的有界通道,回调里
    /// `try_send` 满了就静默丢块:按 48 kHz、每回调 512 帧算,大约 44 秒后
    /// 说的话就再也到不了服务端,转写结果少了后半段,而且没有任何报错。
    /// 无界的代价很小:上限 5 分钟的 16 kHz i16 单声道不到 10 MB。
    pub fn create_stream_channel(&mut self) {
        let (tx, rx) = mpsc::unbounded_channel();
        self.chunk_sender = Some(tx);
        self.chunk_receiver = Some(rx);
    }

    /// Take the chunk receiver (consumed by stop_recording to feed the WS task).
    pub fn take_chunk_receiver(&mut self) -> Option<mpsc::UnboundedReceiver<Vec<u8>>> {
        self.chunk_receiver.take()
    }

    /// 当前是否正在录音。调用方要先问这个再动任何状态:`start()` 自己也会挡
    /// 重复启动,但那时 `create_stream_channel` 已经把正在跑的回调的 sender
    /// 换掉了,而失败分支的 `reset()` 更会把采样缓冲一起清空。
    pub fn is_recording(&self) -> bool {
        self.is_recording.load(Ordering::SeqCst)
    }

    /// Get a clone of the peak level Arc for external monitoring.
    pub fn get_peak_level_arc(&self) -> Arc<AtomicU32> {
        self.peak_level.clone()
    }

    /// List all available audio input devices
    pub fn list_devices(&self) -> Vec<AudioDeviceInfo> {
        let host = cpal::default_host();
        let default_name = host.default_input_device().and_then(|d| d.name().ok());
        let devices: Vec<cpal::Device> = match host.input_devices() {
            Ok(d) => d.collect(),
            Err(_) => Vec::new(),
        };
        devices
            .into_iter()
            .filter_map(|d| {
                let name = d.name().ok()?;
                let is_default = Some(name.as_str()) == default_name.as_deref();
                let config = d.default_input_config().ok()?;
                Some(AudioDeviceInfo {
                    name,
                    channels: config.channels(),
                    default: is_default,
                })
            })
            .collect()
    }

    /// 开始录音。
    ///
    /// 返回 `Ok(Some(note))` 表示录音开起来了,但有件事要告诉用户(目前只有
    /// 「配置的麦克风不在,改用了默认麦克风」)。`on_warning` 在录音过程中
    /// 设备出错(比如蓝牙耳机断开)时被调用,每次录音最多一次。
    pub fn start(
        &mut self,
        device_name: Option<String>,
        on_warning: WarningSink,
    ) -> Result<Option<String>, String> {
        if self.is_recording.load(Ordering::SeqCst) {
            return Err(t(
                "正在录音中,请先结束当前录音。",
                "Already recording. Finish the current recording first.",
            )
            .to_string());
        }

        let host = cpal::default_host();
        let (device, note) = self
            .select_device(&host, device_name.as_deref())
            .ok_or_else(|| {
                t(
                    "找不到可用的麦克风,请检查麦克风是否已连接。",
                    "No microphone found. Check that one is connected.",
                )
                .to_string()
            })?;

        let device_name_str = device.name().unwrap_or_else(|_| "unknown".into());
        let config = device.default_input_config().map_err(|e| {
            tr!(
                "读取麦克风「{}」的参数失败:{}",
                "Couldn't read the settings of microphone \"{}\": {}",
                device_name_str,
                e
            )
        })?;

        let sample_format = config.sample_format();
        let sample_rate = config.sample_rate().0;
        let channels = config.channels() as usize;

        eprintln!(
            "[audio] Starting on: '{}' ({} Hz, {} ch, {:?})",
            device_name_str, sample_rate, channels, sample_format,
        );

        let stream_config: cpal::StreamConfig = config.into();

        // On-the-fly resampler: converts device rate → 16kHz for ASR
        struct Resampler {
            ratio: f64,
            position: f64,
            last_sample: f32,
        }
        impl Resampler {
            fn new(src_rate: u32) -> Self {
                Self {
                    ratio: src_rate as f64 / 16000.0,
                    position: 0.0,
                    last_sample: 0.0,
                }
            }
            fn process(&mut self, input: &[f32]) -> Vec<f32> {
                let mut out = Vec::new();
                for &sample in input {
                    self.last_sample = sample;
                    while self.position < 1.0 {
                        out.push(sample);
                        self.position += self.ratio;
                    }
                    self.position -= 1.0;
                }
                out
            }
        }

        let needs_resample = stream_config.sample_rate.0 != 16000;
        let resampler: Option<Arc<Mutex<Resampler>>> = if needs_resample {
            eprintln!(
                "[audio] Resampling {}Hz → 16kHz on-the-fly",
                stream_config.sample_rate.0
            );
            Some(Arc::new(Mutex::new(Resampler::new(
                stream_config.sample_rate.0,
            ))))
        } else {
            None
        };

        let peak = self.peak_level.clone();
        let recording = self.is_recording.clone();
        let samples = self.samples.clone();
        let sender = self.chunk_sender.clone();

        peak.store(0, Ordering::SeqCst);
        *samples.lock().unwrap() = Vec::new();

        // 以前这里只 eprintln:录音中蓝牙耳机断开,用户按着键对着空气说完,
        // 松手只拿到半截或空的结果,完全不知道发生了什么。现在交给调用方
        // 去弹提示。设备断开时 cpal 往往会连着报好几次,只报第一次。
        let reported = Arc::new(AtomicBool::new(false));
        let err_fn = move |err: cpal::StreamError| {
            eprintln!("[audio] Stream error: {}", err);
            if !reported.swap(true, Ordering::SeqCst) {
                on_warning(stream_error_message(&err));
            }
        };

        // Helper: push f32 mono samples to buffer + streaming channel.
        fn push_samples(
            mono_data: &[f32],
            samples: &Arc<Mutex<Vec<f32>>>,
            peak: &Arc<AtomicU32>,
            sender: &Option<mpsc::UnboundedSender<Vec<u8>>>,
            resampler: &Option<Arc<Mutex<Resampler>>>,
        ) {
            let resampled;
            let data = if let Some(ref rs) = resampler {
                if let Ok(mut r) = rs.try_lock() {
                    resampled = r.process(mono_data);
                    &resampled
                } else {
                    mono_data
                }
            } else {
                mono_data
            };

            let mut local_peak = 0.0f32;
            if let Ok(mut buf) = samples.try_lock() {
                for &mono in data {
                    buf.push(mono);
                    let abs = mono.abs();
                    if abs > local_peak {
                        local_peak = abs;
                    }
                }
            } else {
                for &mono in data {
                    let abs = mono.abs();
                    if abs > local_peak {
                        local_peak = abs;
                    }
                }
            }
            peak.store((local_peak * 1000.0) as u32, Ordering::SeqCst);

            if let Some(ref tx) = sender {
                let pcm: Vec<u8> = data
                    .iter()
                    .flat_map(|&mono| {
                        let clamped = mono.clamp(-1.0, 1.0);
                        let s = if clamped < 0.0 {
                            (clamped * 32768.0) as i16
                        } else {
                            (clamped * 32767.0) as i16
                        };
                        s.to_le_bytes()
                    })
                    .collect();
                // 无界通道只有在接收端已经没了(录音被 reset)时才会失败,
                // 那时丢掉就是对的。
                let _ = tx.send(pcm);
            }
        }

        fn to_mono_f32<T: Copy>(
            data: &[T],
            channels: usize,
            convert: impl Fn(T) -> f32,
        ) -> Vec<f32> {
            data.chunks(channels)
                .map(|chunk| {
                    let sum: f32 = chunk.iter().map(|&s| convert(s)).sum();
                    sum / channels as f32
                })
                .collect()
        }

        let stream = match sample_format {
            cpal::SampleFormat::F32 => {
                let (r, p, s, tx, rs) = (
                    recording.clone(),
                    peak.clone(),
                    samples.clone(),
                    sender.clone(),
                    resampler.clone(),
                );
                device.build_input_stream(
                    &stream_config,
                    move |data: &[f32], _| {
                        if !r.load(Ordering::SeqCst) {
                            return;
                        }
                        let mono = to_mono_f32(data, channels, |v| v);
                        push_samples(&mono, &s, &p, &tx, &rs);
                    },
                    err_fn,
                    None,
                )
            }
            cpal::SampleFormat::I16 => {
                let (r, p, s, tx, rs) = (
                    recording.clone(),
                    peak.clone(),
                    samples.clone(),
                    sender.clone(),
                    resampler.clone(),
                );
                device.build_input_stream(
                    &stream_config,
                    move |data: &[i16], _| {
                        if !r.load(Ordering::SeqCst) {
                            return;
                        }
                        let mono = to_mono_f32(data, channels, |v| v as f32 / 32768.0);
                        push_samples(&mono, &s, &p, &tx, &rs);
                    },
                    err_fn,
                    None,
                )
            }
            cpal::SampleFormat::U16 => {
                let (r, p, s, tx, rs) = (
                    recording.clone(),
                    peak.clone(),
                    samples.clone(),
                    sender.clone(),
                    resampler.clone(),
                );
                device.build_input_stream(
                    &stream_config,
                    move |data: &[u16], _| {
                        if !r.load(Ordering::SeqCst) {
                            return;
                        }
                        let mono = to_mono_f32(data, channels, |v| (v as f32 - 32768.0) / 32768.0);
                        push_samples(&mono, &s, &p, &tx, &rs);
                    },
                    err_fn,
                    None,
                )
            }
            cpal::SampleFormat::I32 => {
                let (r, p, s, tx, rs) = (
                    recording.clone(),
                    peak.clone(),
                    samples.clone(),
                    sender.clone(),
                    resampler.clone(),
                );
                device.build_input_stream(
                    &stream_config,
                    move |data: &[i32], _| {
                        if !r.load(Ordering::SeqCst) {
                            return;
                        }
                        let mono = to_mono_f32(data, channels, |v| v as f32 / 2147483648.0);
                        push_samples(&mono, &s, &p, &tx, &rs);
                    },
                    err_fn,
                    None,
                )
            }
            cpal::SampleFormat::U32 => {
                let (r, p, s, tx, rs) = (
                    recording.clone(),
                    peak.clone(),
                    samples.clone(),
                    sender.clone(),
                    resampler.clone(),
                );
                device.build_input_stream(
                    &stream_config,
                    move |data: &[u32], _| {
                        if !r.load(Ordering::SeqCst) {
                            return;
                        }
                        let mono = to_mono_f32(data, channels, |v| {
                            (v as f32 - 2147483648.0) / 2147483648.0
                        });
                        push_samples(&mono, &s, &p, &tx, &rs);
                    },
                    err_fn,
                    None,
                )
            }
            cpal::SampleFormat::F64 => {
                let (r, p, s, tx, rs) = (
                    recording.clone(),
                    peak.clone(),
                    samples.clone(),
                    sender.clone(),
                    resampler.clone(),
                );
                device.build_input_stream(
                    &stream_config,
                    move |data: &[f64], _| {
                        if !r.load(Ordering::SeqCst) {
                            return;
                        }
                        let mono = to_mono_f32(data, channels, |v| v as f32);
                        push_samples(&mono, &s, &p, &tx, &rs);
                    },
                    err_fn,
                    None,
                )
            }
            other => {
                return Err(tr!(
                    "不支持这个麦克风的采样格式:{:?}",
                    "This microphone's sample format isn't supported: {:?}",
                    other
                ))
            }
        }
        .map_err(|e| {
            tr!(
                "打开麦克风「{}」失败:{}",
                "Couldn't open microphone \"{}\": {}",
                device_name_str,
                e
            )
        })?;

        stream.play().map_err(|e| {
            tr!(
                "麦克风「{}」启动录音失败:{}",
                "Microphone \"{}\" couldn't start recording: {}",
                device_name_str,
                e
            )
        })?;

        self.input_sample_rate = sample_rate;
        // push_samples() always resamples to 16 kHz before buffering.
        self.buffer_sample_rate = 16000;
        recording.store(true, Ordering::SeqCst);
        self.stream = Some(SendStream(Some(stream)));
        self.selected_device = device_name;

        eprintln!("[audio] Recording started OK");
        Ok(note)
    }

    pub fn stop(&mut self) -> Result<(Vec<f32>, u32), String> {
        self.is_recording.store(false, Ordering::SeqCst);
        self.peak_level.store(0, Ordering::SeqCst);

        if let Some(s) = self.stream.take() {
            drop(s);
        }
        self.chunk_sender = None;

        let deadline = std::time::Instant::now() + std::time::Duration::from_millis(10);
        while std::time::Instant::now() < deadline {
            std::thread::sleep(std::time::Duration::from_micros(100));
        }

        let (samples, rate) = {
            let mut buf = self.samples.lock().map_err(|e| e.to_string())?;
            let result = buf.clone();
            buf.clear();
            // Return the BUFFER rate, not the device rate: the callback has
            // already resampled. Returning the device rate made the caller
            // resample a second time (pitch/speed corruption in batch mode).
            (result, self.buffer_sample_rate)
        };

        eprintln!(
            "[audio] Stopped: {} samples ({:.2}s at {}Hz, device {}Hz)",
            samples.len(),
            samples.len() as f64 / rate as f64,
            rate,
            self.input_sample_rate
        );
        Ok((samples, rate))
    }

    pub fn reset(&mut self) {
        self.is_recording.store(false, Ordering::SeqCst);
        self.peak_level.store(0, Ordering::SeqCst);
        self.stream = None;
        self.chunk_sender = None;
        self.chunk_receiver = None;
        *self.samples.lock().unwrap() = Vec::new();
    }

    pub fn get_level(&self) -> f32 {
        self.peak_level.load(Ordering::SeqCst) as f32 / 1000.0
    }

    /// 选录音设备。配置了设备名就优先用它;找不到时回落到系统默认设备,
    /// 并附上一句给用户的说明。
    ///
    /// 以前回落是静默的:蓝牙耳机没连上,实际在用笔记本内置麦克风录,
    /// 识别效果变差,用户却以为还在用耳机。
    fn select_device(
        &self,
        host: &cpal::Host,
        name: Option<&str>,
    ) -> Option<(cpal::Device, Option<String>)> {
        let Some(name) = name else {
            return host.default_input_device().map(|d| (d, None));
        };
        // 枚举失败不等于没有设备:以前这里 `ok()?` 直接返回 None,默认麦克风
        // 明明能用也报「找不到麦克风」。
        let devices: Vec<cpal::Device> = host
            .input_devices()
            .map(|d| d.collect())
            .unwrap_or_default();
        if let Some(d) = devices
            .into_iter()
            .find(|d| d.name().ok().as_deref() == Some(name))
        {
            return Some((d, None));
        }
        let fallback = host.default_input_device()?;
        let fallback_name = fallback.name().ok();
        Some((
            fallback,
            Some(fallback_note(name, fallback_name.as_deref())),
        ))
    }
}

/// 配置的麦克风不在、回落到默认设备时给用户的提示。
fn fallback_note(wanted: &str, fallback: Option<&str>) -> String {
    match fallback {
        Some(f) => tr!(
            "找不到麦克风「{}」,本次改用系统默认麦克风「{}」。",
            "Microphone \"{}\" not found. Using the system default \"{}\" this time.",
            wanted,
            f
        ),
        None => tr!(
            "找不到麦克风「{}」,本次改用系统默认麦克风。",
            "Microphone \"{}\" not found. Using the system default microphone this time.",
            wanted
        ),
    }
}

/// 录音过程中设备出错时给用户的提示。
fn stream_error_message(err: &cpal::StreamError) -> String {
    match err {
        cpal::StreamError::DeviceNotAvailable => {
            t(
                "麦克风在录音中断开了,这次录音可能不完整。请检查设备后重新录音。",
                "The microphone disconnected while recording, so this recording may be incomplete. Check the device and record again.",
            )
            .to_string()
        }
        other => tr!(
            "麦克风出错,这次录音可能不完整:{}",
            "Microphone error; this recording may be incomplete: {}",
            other
        ),
    }
}

pub fn encode_wav_resampled(samples: &[f32], src_rate: u32) -> Vec<u8> {
    let resampled = if src_rate != 16000 {
        let ratio = src_rate as f64 / 16000.0;
        let src_len = samples.len();
        let dst_len = (src_len as f64 / ratio).round() as usize;
        let mut out = Vec::with_capacity(dst_len);
        for i in 0..dst_len {
            let src_pos = i as f64 * ratio;
            let idx = src_pos as usize;
            let frac = src_pos - idx as f64;
            let s0 = samples[idx.min(src_len - 1)];
            let s1 = samples[(idx + 1).min(src_len - 1)];
            out.push(s0 + (s1 - s0) * frac as f32);
        }
        out
    } else {
        samples.to_vec()
    };
    encode_wav(&resampled, 16000)
}

fn encode_wav(samples: &[f32], sample_rate: u32) -> Vec<u8> {
    let num_channels: u16 = 1;
    let bits_per_sample: u16 = 16;
    let byte_rate = sample_rate * num_channels as u32 * (bits_per_sample / 8) as u32;
    let block_align = num_channels * (bits_per_sample / 8);
    let data_size = samples.len() as u32 * (bits_per_sample / 8) as u32;
    let total_size = 44 + data_size;
    let mut wav = Vec::with_capacity(total_size as usize);
    wav.extend_from_slice(b"RIFF");
    wav.extend_from_slice(&(36 + data_size).to_le_bytes());
    wav.extend_from_slice(b"WAVE");
    wav.extend_from_slice(b"fmt ");
    wav.extend_from_slice(&16u32.to_le_bytes());
    wav.extend_from_slice(&1u16.to_le_bytes());
    wav.extend_from_slice(&num_channels.to_le_bytes());
    wav.extend_from_slice(&sample_rate.to_le_bytes());
    wav.extend_from_slice(&byte_rate.to_le_bytes());
    wav.extend_from_slice(&block_align.to_le_bytes());
    wav.extend_from_slice(&bits_per_sample.to_le_bytes());
    wav.extend_from_slice(b"data");
    wav.extend_from_slice(&data_size.to_le_bytes());
    for &sample in samples {
        let clamped = sample.clamp(-1.0, 1.0);
        let int_sample = if clamped < 0.0 {
            (clamped * 32768.0) as i16
        } else {
            (clamped * 32767.0) as i16
        };
        wav.extend_from_slice(&int_sample.to_le_bytes());
    }
    wav
}

#[cfg(test)]
mod tests {
    use super::*;

    /// R1 回归:录音期间分块通道没人消费,以前容量 4096、满了静默丢块,
    /// 约 44 秒后的话全丢。这里模拟 5 分钟、48 kHz / 512 帧回调的分块数,
    /// 松手后再一次性取出,一块都不能少。
    #[test]
    fn stream_channel_keeps_every_chunk_until_consumed() {
        let mut rec = AudioRecorder::new();
        rec.create_stream_channel();
        let tx = rec.chunk_sender.take().unwrap();
        let callbacks = 300 * 48_000 / 512; // ≈ 28k
        for i in 0..callbacks as u32 {
            tx.send(i.to_le_bytes().to_vec()).unwrap();
        }
        drop(tx);
        let mut rx = rec.take_chunk_receiver().unwrap();
        let mut n = 0u32;
        while let Ok(chunk) = rx.try_recv() {
            assert_eq!(chunk, n.to_le_bytes().to_vec());
            n += 1;
        }
        assert_eq!(n as usize, callbacks);
    }

    #[test]
    fn fallback_note_names_both_devices() {
        let note = fallback_note("AirPods", Some("MacBook 麦克风"));
        assert!(note.contains("AirPods") && note.contains("MacBook 麦克风"));
        assert!(fallback_note("AirPods", None).contains("AirPods"));
    }
}
