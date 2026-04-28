//! Audio capture using cpal.
//! cpal::Stream is not Send, so we wrap it in an unsafe newtype.

use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use serde::{Deserialize, Serialize};
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::{Arc, Mutex};

/// Audio device info for the frontend
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AudioDeviceInfo {
    pub name: String,
    pub channels: u16,
    pub default: bool,
}

// cpal::Stream is not Send, but on all platforms we target it is safe to send.
struct SendStream(Option<cpal::Stream>);
unsafe impl Send for SendStream {}

/// cpal-based audio capture
pub struct AudioRecorder {
    selected_device: Option<String>,
    peak_level: Arc<AtomicU32>,
    is_recording: Arc<AtomicBool>,
    stream: Option<SendStream>,
    samples: Arc<Mutex<Vec<f32>>>,
}

impl AudioRecorder {
    pub fn new() -> Self {
        Self {
            selected_device: None,
            peak_level: Arc::new(AtomicU32::new(0)),
            is_recording: Arc::new(AtomicBool::new(false)),
            stream: None,
            samples: Arc::new(Mutex::new(Vec::new())),
        }
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

    /// Start recording
    pub fn start(&mut self, device_name: Option<String>) -> Result<(), String> {
        if self.is_recording.load(Ordering::SeqCst) {
            return Err("Already recording".to_string());
        }

        let host = cpal::default_host();
        let device = self.select_device(&host, device_name.as_deref())
            .ok_or_else(|| "No audio input device found".to_string())?;

        let config = device
            .default_input_config()
            .map_err(|e| format!("Failed to get input config: {}", e))?;

        let sample_format = config.sample_format();
        let sample_rate = config.sample_rate().0;
        let channels = config.channels() as usize;

        eprintln!("[audio] Starting on: {} ({} Hz, {} ch, {:?})",
            device.name().unwrap_or_default(),
            sample_rate,
            channels,
            sample_format,
        );

        let stream_config: cpal::StreamConfig = config.into();
        let peak = self.peak_level.clone();
        let recording = self.is_recording.clone();
        let samples = self.samples.clone();

        peak.store(0, Ordering::SeqCst);
        recording.store(true, Ordering::SeqCst);
        *samples.lock().unwrap() = Vec::new();

        let err_fn = move |err| eprintln!("[audio] Stream error: {}", err);

        let stream = match sample_format {
            cpal::SampleFormat::F32 => {
                device.build_input_stream(
                    &stream_config,
                    move |data: &[f32], _: &cpal::InputCallbackInfo| {
                        if !recording.load(Ordering::SeqCst) { return; }
                        let mut buf = samples.lock().unwrap();
                        let mut local_peak = 0.0f32;
                        for chunk in data.chunks(channels) {
                            let mono: f32 = chunk.iter().sum::<f32>() / channels as f32;
                            buf.push(mono);
                            let abs = mono.abs();
                            if abs > local_peak { local_peak = abs; }
                        }
                        peak.store((local_peak * 1000.0) as u32, Ordering::SeqCst);
                    },
                    err_fn,
                    None,
                )
            }
            cpal::SampleFormat::I16 => {
                device.build_input_stream(
                    &stream_config,
                    move |data: &[i16], _: &cpal::InputCallbackInfo| {
                        if !recording.load(Ordering::SeqCst) { return; }
                        let mut buf = samples.lock().unwrap();
                        let mut local_peak = 0.0f32;
                        for chunk in data.chunks(channels) {
                            let mono: f32 = chunk.iter().map(|&v| v as f32 / 32768.0).sum::<f32>() / channels as f32;
                            buf.push(mono);
                            let abs = mono.abs();
                            if abs > local_peak { local_peak = abs; }
                        }
                        peak.store((local_peak * 1000.0) as u32, Ordering::SeqCst);
                    },
                    err_fn,
                    None,
                )
            }
            _ => return Err("Unsupported sample format".to_string()),
        }
        .map_err(|e| format!("Failed to create audio stream: {}", e))?;

        stream.play().map_err(|e| format!("Failed to start stream: {}", e))?;

        self.stream = Some(SendStream(Some(stream)));
        self.selected_device = device_name;

        Ok(())
    }

    /// Stop recording and return WAV bytes
    pub fn stop(&mut self) -> Result<Vec<u8>, String> {
        self.is_recording.store(false, Ordering::SeqCst);
        self.peak_level.store(0, Ordering::SeqCst);

        // Drop the stream (stops audio capture)
        if let Some(s) = self.stream.take() {
            drop(s);
        }

        // Small delay for any remaining audio callbacks to finish
        std::thread::sleep(std::time::Duration::from_millis(50));

        let samples = {
            let mut buf = self.samples.lock().map_err(|e| e.to_string())?;
            let result = buf.clone();
            buf.clear();
            result
        };

        if samples.is_empty() {
            return Ok(Vec::new());
        }

        Ok(encode_wav(&samples, 16000))
    }

    /// Get current peak audio level (0.0 – 1.0)
    pub fn get_level(&self) -> f32 {
        self.peak_level.load(Ordering::SeqCst) as f32 / 1000.0
    }

    fn select_device(&self, host: &cpal::Host, name: Option<&str>) -> Option<cpal::Device> {
        if let Some(name) = name {
            // Collect devices first to avoid borrow conflicts
            let devices: Vec<cpal::Device> = host.input_devices().ok()?.collect();
            devices
                .into_iter()
                .find(|d| d.name().ok().as_deref() == Some(name))
                .or_else(|| host.default_input_device())
        } else {
            host.default_input_device()
        }
    }
}

/// Encode f32 PCM samples as 16-bit mono WAV
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
    wav.extend_from_slice(&1u16.to_le_bytes());      // PCM
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
