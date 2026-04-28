// Audio recording module - placeholder
// macOS audio capture via cpal will be implemented here

/// Placeholder for audio recording functionality.
/// Will use cpal to capture system/microphone audio on macOS.
pub struct AudioRecorder {
    // TODO: cpal stream
}

impl AudioRecorder {
    pub fn new() -> Self {
        Self {}
    }

    pub fn start(&mut self) -> Result<(), String> {
        // TODO: Start recording via cpal
        Ok(())
    }

    pub fn stop(&mut self) -> Result<Vec<u8>, String> {
        // TODO: Stop recording, return WAV bytes
        Ok(Vec::new())
    }
}
