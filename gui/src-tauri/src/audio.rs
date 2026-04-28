// Audio recording stub
// Audio capture is handled by the web frontend via MediaRecorder API.
// The Rust backend receives raw PCM bytes via IPC and sends them to STT server.
// For direct CoreAudio capture (future): use coreaudio-rs crate.

pub struct AudioRecorder;

impl AudioRecorder {
    pub fn new() -> Self {
        Self
    }

    pub fn start(&mut self) -> Result<(), String> {
        // Frontend handles audio capture via MediaRecorder
        Ok(())
    }

    pub fn stop(&mut self) -> Vec<u8> {
        // Audio data comes via IPC from frontend
        Vec::new()
    }
}
