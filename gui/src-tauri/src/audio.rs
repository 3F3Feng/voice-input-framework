//! Audio capture using cpal with streaming support.
//! cpal::Stream is not Send, so we wrap it in an unsafe newtype.

use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use serde::{Deserialize, Serialize};
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::{Arc, Mutex};
use tokio::sync::mpsc;

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
    input_sample_rate: u32,
    peak_level: Arc<AtomicU32>,
    is_recording: Arc<AtomicBool>,
    stream: Option<SendStream>,
    samples: Arc<Mutex<Vec<f32>>>,
    chunk_sender: Option<mpsc::Sender<Vec<u8>>>,
    chunk_receiver: Option<mpsc::Receiver<Vec<u8>>>,
}

impl AudioRecorder {
    pub fn new() -> Self {
        Self {
            selected_device: None,
            input_sample_rate: 16000,
            peak_level: Arc::new(AtomicU32::new(0)),
            is_recording: Arc::new(AtomicBool::new(false)),
            stream: None,
            samples: Arc::new(Mutex::new(Vec::new())),
            chunk_sender: None,
            chunk_receiver: None,
        }
    }

    /// Create a streaming channel. Call this before start().
    pub fn create_stream_channel(&mut self, buffer_size: usize) {
        let (tx, rx) = mpsc::channel(buffer_size);
        self.chunk_sender = Some(tx);
        self.chunk_receiver = Some(rx);
    }

    /// Take the chunk receiver (consumed by stop_recording to feed the WS task).
    pub fn take_chunk_receiver(&mut self) -> Option<mpsc::Receiver<Vec<u8>>> {
        self.chunk_receiver.take()
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

    pub fn start(&mut self, device_name: Option<String>) -> Result<(), String> {
        if self.is_recording.load(Ordering::SeqCst) {
            return Err("Already recording".to_string());
        }

        let host = cpal::default_host();
        let device = self
            .select_device(&host, device_name.as_deref())
            .ok_or_else(|| "No audio input device found".to_string())?;

        let device_name_str = device.name().unwrap_or_else(|_| "unknown".into());
        let config = device
            .default_input_config()
            .map_err(|e| format!("Failed to get input config for '{}': {}", device_name_str, e))?;

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
                Self { ratio: src_rate as f64 / 16000.0, position: 0.0, last_sample: 0.0 }
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
            eprintln!("[audio] Resampling {}Hz → 16kHz on-the-fly", stream_config.sample_rate.0);
            Some(Arc::new(Mutex::new(Resampler::new(stream_config.sample_rate.0))))
        } else {
            None
        };

        let peak = self.peak_level.clone();
        let recording = self.is_recording.clone();
        let samples = self.samples.clone();
        let sender = self.chunk_sender.clone();

        peak.store(0, Ordering::SeqCst);
        *samples.lock().unwrap() = Vec::new();

        let err_fn = move |err| eprintln!("[audio] Stream error: {}", err);

        // Helper: push f32 mono samples to buffer + streaming channel.
        fn push_samples(
            mono_data: &[f32],
            samples: &Arc<Mutex<Vec<f32>>>,
            peak: &Arc<AtomicU32>,
            sender: &Option<mpsc::Sender<Vec<u8>>>,
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
                    if abs > local_peak { local_peak = abs; }
                }
            } else {
                for &mono in data {
                    let abs = mono.abs();
                    if abs > local_peak { local_peak = abs; }
                }
            }
            peak.store((local_peak * 1000.0) as u32, Ordering::SeqCst);

            if let Some(ref tx) = sender {
                let pcm: Vec<u8> = data.iter().flat_map(|&mono| {
                    let clamped = mono.clamp(-1.0, 1.0);
                    let s = if clamped < 0.0 { (clamped * 32768.0) as i16 } else { (clamped * 32767.0) as i16 };
                    s.to_le_bytes()
                }).collect();
                let _ = tx.try_send(pcm);
            }
        }

        fn to_mono_f32<T: Copy>(data: &[T], channels: usize, convert: impl Fn(T) -> f32) -> Vec<f32> {
            data.chunks(channels).map(|chunk| {
                let sum: f32 = chunk.iter().map(|&s| convert(s)).sum();
                sum / channels as f32
            }).collect()
        }

        let stream = match sample_format {
            cpal::SampleFormat::F32 => {
                let (r, p, s, tx, rs) = (recording.clone(), peak.clone(), samples.clone(), sender.clone(), resampler.clone());
                device.build_input_stream(&stream_config, move |data: &[f32], _| {
                    if !r.load(Ordering::SeqCst) { return; }
                    let mono = to_mono_f32(data, channels, |v| v);
                    push_samples(&mono, &s, &p, &tx, &rs);
                }, err_fn, None)
            }
            cpal::SampleFormat::I16 => {
                let (r, p, s, tx, rs) = (recording.clone(), peak.clone(), samples.clone(), sender.clone(), resampler.clone());
                device.build_input_stream(&stream_config, move |data: &[i16], _| {
                    if !r.load(Ordering::SeqCst) { return; }
                    let mono = to_mono_f32(data, channels, |v| v as f32 / 32768.0);
                    push_samples(&mono, &s, &p, &tx, &rs);
                }, err_fn, None)
            }
            cpal::SampleFormat::U16 => {
                let (r, p, s, tx, rs) = (recording.clone(), peak.clone(), samples.clone(), sender.clone(), resampler.clone());
                device.build_input_stream(&stream_config, move |data: &[u16], _| {
                    if !r.load(Ordering::SeqCst) { return; }
                    let mono = to_mono_f32(data, channels, |v| (v as f32 - 32768.0) / 32768.0);
                    push_samples(&mono, &s, &p, &tx, &rs);
                }, err_fn, None)
            }
            cpal::SampleFormat::I32 => {
                let (r, p, s, tx, rs) = (recording.clone(), peak.clone(), samples.clone(), sender.clone(), resampler.clone());
                device.build_input_stream(&stream_config, move |data: &[i32], _| {
                    if !r.load(Ordering::SeqCst) { return; }
                    let mono = to_mono_f32(data, channels, |v| v as f32 / 2147483648.0);
                    push_samples(&mono, &s, &p, &tx, &rs);
                }, err_fn, None)
            }
            cpal::SampleFormat::U32 => {
                let (r, p, s, tx, rs) = (recording.clone(), peak.clone(), samples.clone(), sender.clone(), resampler.clone());
                device.build_input_stream(&stream_config, move |data: &[u32], _| {
                    if !r.load(Ordering::SeqCst) { return; }
                    let mono = to_mono_f32(data, channels, |v| (v as f32 - 2147483648.0) / 2147483648.0);
                    push_samples(&mono, &s, &p, &tx, &rs);
                }, err_fn, None)
            }
            cpal::SampleFormat::F64 => {
                let (r, p, s, tx, rs) = (recording.clone(), peak.clone(), samples.clone(), sender.clone(), resampler.clone());
                device.build_input_stream(&stream_config, move |data: &[f64], _| {
                    if !r.load(Ordering::SeqCst) { return; }
                    let mono = to_mono_f32(data, channels, |v| v as f32);
                    push_samples(&mono, &s, &p, &tx, &rs);
                }, err_fn, None)
            }
            other => return Err(format!("Unsupported sample format: {:?}", other)),
        }
        .map_err(|e| format!("Failed to create audio stream: {}", e))?;

        stream.play().map_err(|e| format!("Failed to start stream: {}", e))?;

        self.input_sample_rate = sample_rate;
        recording.store(true, Ordering::SeqCst);
        self.stream = Some(SendStream(Some(stream)));
        self.selected_device = device_name;

        eprintln!("[audio] Recording started OK");
        Ok(())
    }

    pub fn stop(&mut self) -> Result<(Vec<f32>, u32), String> {
        self.is_recording.store(false, Ordering::SeqCst);
        self.peak_level.store(0, Ordering::SeqCst);

        if let Some(s) = self.stream.take() { drop(s); }
        self.chunk_sender = None;

        let deadline = std::time::Instant::now() + std::time::Duration::from_millis(10);
        while std::time::Instant::now() < deadline {
            std::thread::sleep(std::time::Duration::from_micros(100));
        }

        let (samples, _original_rate) = {
            let mut buf = self.samples.lock().map_err(|e| e.to_string())?;
            let result = buf.clone();
            buf.clear();
            (result, self.input_sample_rate)
        };

        // Audio was already resampled to 16kHz during recording
        // Return 16000 as the rate to avoid double resampling
        eprintln!("[audio] Stopped: {} samples ({:.2}s at 16kHz)", samples.len(), samples.len() as f64 / 16000.0);
        Ok((samples, 16000))
    }

    pub fn reset(&mut self) {
        self.is_recording.store(false, Ordering::SeqCst);
        self.peak_level.store(0, Ordering::SeqCst);
        self.stream = None;
        self.chunk_sender = None;
        self.chunk_receiver = None;
        *self.samples.lock().unwrap() = Vec::new();
    }

    pub fn is_recording(&self) -> bool {
        self.is_recording.load(Ordering::SeqCst)
    }

    pub fn get_level(&self) -> f32 {
        self.peak_level.load(Ordering::SeqCst) as f32 / 1000.0
    }

    fn select_device(&self, host: &cpal::Host, name: Option<&str>) -> Option<cpal::Device> {
        if let Some(name) = name {
            let devices: Vec<cpal::Device> = host.input_devices().ok()?.collect();
            devices.into_iter().find(|d| d.name().ok().as_deref() == Some(name)).or_else(|| host.default_input_device())
        } else {
            host.default_input_device()
        }
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
        let int_sample = if clamped < 0.0 { (clamped * 32768.0) as i16 } else { (clamped * 32767.0) as i16 };
        wav.extend_from_slice(&int_sample.to_le_bytes());
    }
    wav
}
