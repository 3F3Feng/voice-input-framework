# Model Selection Scripts

Scripts to interact with the Voice Input Framework server and change the STT model.

## Available Scripts

### 1. Python Version (Cross-Platform) - Recommended

**File**: `change_model.py`

**Best for**: Most use cases, greatest compatibility

**Requirements**:
```bash
pip install httpx
```

**Usage**:

```bash
# Show current server status
python change_model.py

# Show current server status (verbose)
python change_model.py --info

# List all available models
python change_model.py --list

# Switch to a specific model
python change_model.py --model whisper
python change_model.py --model qwen_asr
python change_model.py --model whisper-small

# Connect to a remote server
python change_model.py --server 192.168.1.100:6543 --list
python change_model.py --server 192.168.1.100:6543 --model whisper
```

**Available Models**:
- `whisper` - OpenAI Whisper model (default)
- `whisper-small` - Smaller Whisper variant
- `qwen_asr` - Alibaba Qwen ASR model

### 2. PowerShell Version (Windows)

**File**: `change_model.ps1`

**Requirements**: 
- PowerShell 5.1+ (built-in on Windows)
- No additional packages needed

**Usage**:

```powershell
# Show current server status
.\change_model.ps1

# List all available models
.\change_model.ps1 -List

# Switch to a specific model
.\change_model.ps1 -Model whisper
.\change_model.ps1 -Model qwen_asr

# Connect to a remote server
.\change_model.ps1 -Server "192.168.1.100:6543" -List
.\change_model.ps1 -Server "192.168.1.100:6543" -Model whisper
```

**First Time Setup** (if you see execution policy error):

```powershell
# Allow local scripts to run (one-time setup)
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### 3. Bash Version (Linux/macOS)

**File**: `change_model.sh`

**Requirements**:
- curl (usually pre-installed)
- python3 (for JSON formatting, optional)

**Usage**:

```bash
# Make the script executable (first time only)
chmod +x change_model.sh

# Show current server status
./change_model.sh

# List all available models
./change_model.sh list

# Switch to a specific model
./change_model.sh whisper
./change_model.sh qwen_asr
```

**With Remote Server**:

```bash
SERVER="192.168.1.100:6543" ./change_model.sh
SERVER="192.168.1.100:6543" ./change_model.sh list
SERVER="192.168.1.100:6543" ./change_model.sh whisper
```

## Examples

### Python Examples

```bash
# Check if server is running
python change_model.py

# See all available models
python change_model.py --list

# Switch to Qwen ASR model
python change_model.py --model qwen_asr

# Connect to a server on different machine
python change_model.py --server 10.0.0.5:6543 --model whisper
```

### PowerShell Examples

```powershell
# Check server status with nice formatting
.\change_model.ps1 -Info

# List models
.\change_model.ps1 -List

# Switch models
.\change_model.ps1 -Model qwen_asr

# Remote server
.\change_model.ps1 -Server "example.com:6543" -Model whisper
```

### Bash Examples

```bash
# Quick status check
./change_model.sh

# List all models
./change_model.sh list

# Switch model
./change_model.sh qwen_asr

# Remote access
SERVER="10.0.0.5:6543" ./change_model.sh list
```

## Programmatic Usage

### Python

```python
import asyncio
from change_model import VoiceServerClient

async def main():
    client = VoiceServerClient(host="localhost", port=6543)
    
    # Get current status
    health = await client.get_health()
    print(f"Current model: {health['current_model']}")
    
    # List models
    models = await client.list_models()
    for model in models:
        print(f"  - {model['name']}")
    
    # Switch model
    result = await client.select_model("qwen_asr")
    print(f"Switched to: {result['current_model']}")

asyncio.run(main())
```

### cURL (HTTP API)

```bash
# Check server status
curl http://localhost:6543/health | jq

# List available models  
curl http://localhost:6543/models | jq

# Switch to a model
curl -X POST http://localhost:6543/models/select \
  -d "model_name=whisper" | jq
```

## Troubleshooting

### Connection refused
- Make sure the server is running: `python server/api.py`
- Check the server port matches (default: 6543)
- For remote servers, check network connectivity and firewall rules

### Model not found
- List available models with `--list` to see what's installed
- Models need to be loaded on the server before use

### PowerShell execution policy error
- Run: `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`

### Python dependencies
- Install requirements: `pip install -r requirements.txt`

## Platform Recommendations

- **Windows**: Use PowerShell script (`change_model.ps1`) for best integration
- **Linux/macOS**: Use Bash script (`change_model.sh`) for simplicity
- **All Platforms**: Use Python script (`change_model.py`) for consistency and advanced features

## Server Configuration

The default server configuration:
- Host: `localhost`
- Port: `6543`
- Default Model: `whisper`

To connect to a different server, use the `--server` parameter in any script.

Examples:
```bash
python change_model.py --server voice.example.com:6543 --list
./change_model.sh --server 192.168.1.5:6543 list
```

## Environment Variables

You can set these environment variables to avoid typing server address:

**Linux/macOS (Bash)**:
```bash
export SERVER="192.168.1.100:6543"
./change_model.sh list
```

**Windows (PowerShell)**:
```powershell
$env:SERVER = "192.168.1.100:6543"
.\change_model.ps1 -List
```

## API Reference

### GET /health
Get server status and current model

```bash
curl http://localhost:6543/health
```

Response:
```json
{
  "status": "ok",
  "version": "1.0.0",
  "uptime_seconds": 123.45,
  "current_model": "whisper",
  "loaded_models": ["whisper", "qwen_asr"],
  "active_connections": 0
}
```

### GET /models
List all available models

```bash
curl http://localhost:6543/models
```

Response:
```json
[
  {
    "name": "whisper",
    "description": "STT model: whisper",
    "supported_languages": ["zh", "en", "auto"],
    "is_loaded": true,
    "is_default": true
  },
  {
    "name": "qwen_asr",
    "description": "STT model: qwen_asr",
    "supported_languages": ["zh", "en", "auto"],
    "is_loaded": false,
    "is_default": false
  }
]
```

### POST /models/select
Switch to a specific model

```bash
curl -X POST http://localhost:6543/models/select \
  -d "model_name=qwen_asr"
```

Response:
```json
{
  "status": "success",
  "current_model": "qwen_asr"
}
```
