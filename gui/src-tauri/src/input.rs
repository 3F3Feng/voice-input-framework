//! Auto-input: type text into the active window using keyboard simulation.
//! Uses the `enigo` crate for cross-platform keyboard input.

use enigo::{Enigo, Keyboard};

/// Type text into the currently focused window.
pub fn type_text(text: &str) -> Result<(), String> {
    if text.is_empty() {
        return Ok(());
    }

    let mut enigo = Enigo::new(&Default::default())
        .map_err(|e| format!("Failed to create input simulator: {}", e))?;

    // Type the text character by character using enigo's text() method.
    // This simulates real keyboard input to the active window.
    enigo.text(text)
        .map_err(|e| format!("Failed to type text: {}", e))?;

    Ok(())
}
