# Voice Input Framework - Split Architecture Design

## Overview

This document describes the split architecture design for separating STT and LLM services.

## Problem

The current unified architecture has dependency conflicts:
- STT (qwen_asr) requires transformers 4.x
- LLM (mlx-lm) requires transformers 5.x

## Solution

Split into two independent services:

```
┌──────────────────────────────────────────────────────────────────────────────────────┐
│                              Voice Input Framework                                    │
│                                                                                      │
│   ┌─────────────┐        ┌───────────────────────┐        ┌──────────────────────┐   │
│   │   Client    │───────▶│    Gateway Service    │───────▶│   STT Service        │   │
│   │  (macOS)    │◀───────│     (Port 6543)       │◀───────│   (Port 6544)        │   │
│   └─────────────┘        └───────────────────────┘        │                      │   │
│                                    │                      │  vif-stt conda env   │   │
│                                    │                      │  qwen_asr + trans 4.x│   │
│                                    ▼                      │                      │   │
│                          ┌──────────────────────┐        │  /transcribe         │   │
│                          │   LLM Service        │        │  /ws/stream          │   │
│                          │   (Port 6545)        │        └──────────────────────┘   │
│                          │                      │                                     │
│                          │  mlx-test conda env  │                                     │
│                          │  mlx-lm + trans 5.x  │                                     │
│                          │                      │                                     │
│                          │  /process            │                                     │
│                          └──────────────────────┘                                     │
│                                                                                      │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

## Services

### 1. STT Service (Port 6544)

**Environment**: vif-stt (conda)
- transformers 4.x
- qwen_asr
- torch
- numpy

**API Endpoints**:
- `POST /transcribe` - Transcribe audio file
- `WebSocket /ws/stream` - Streaming transcription
- `GET /health` - Health check
- `GET /models` - List available models
- `POST /models/select` - Switch model

**Response Format**:
```json
{
  "text": "识别出的文本",
  "confidence": 1.0,
  "language": "zh",
  "is_final": true,
  "stt_latency_ms": 1234
}
```

### 2. LLM Service (Port 6545)

**Environment**: mlx-test (conda)
- transformers 5.x
- mlx-lm
- mlx

**API Endpoints**:
- `POST /process` - Process text with LLM
- `GET /health` - Health check
- `GET /models` - List available LLM models
- `POST /models/select` - Switch model

**Request Format**:
```json
{
  "text": "原始STT文本",
  "options": {
    "remove_filler": true,
    "add_punctuation": true
  }
}
```

**Response Format**:
```json
{
  "text": "优化后的文本",
  "original_text": "原始STT文本",
  "llm_latency_ms": 567,
  "model": "Qwen3.5-0.8B-OptiQ"
}
```

### 3. Gateway Service (Port 6543) - Optional

Unified entry point for clients. Can be:
1. A simple proxy that forwards requests
2. Client connects directly to STT and LLM services

## Communication Protocol

### HTTP REST API

For simple request-response scenarios:

```
Client → STT: POST /transcribe
STT → Client: {text, ...}
Client → LLM: POST /process
LLM → Client: {processed_text, ...}
```

### WebSocket Streaming

For real-time streaming:

```
Client → STT: WebSocket /ws/stream
  - Send audio chunks
  - Receive transcription
STT → LLM: POST /process (when transcription complete)
LLM → STT: {processed_text, ...}
STT → Client: {text: processed_text, llm_processed: true}
```

## Deployment Options

### Option A: Separate Processes (Recommended)

```bash
# Terminal 1: STT Service
conda activate vif-stt
python -m services.stt_server --port 6544

# Terminal 2: LLM Service
conda activate mlx-test
python -m services.llm_server --port 6545

# Terminal 3: Gateway (optional)
python -m services.gateway --stt-port 6544 --llm-port 6545
```

### Option B: Unified Launcher

Single script that launches both services:

```bash
./scripts/start_services.sh
```

## Configuration

### STT Service Config

```yaml
# config/stt_config.yaml
host: 0.0.0.0
port: 6544
default_model: qwen_asr
models:
  qwen_asr:
    model_id: Qwen/Qwen3-ASR-1.7B
    device: auto
  qwen_asr_small:
    model_id: Qwen/Qwen3-ASR-0.6B
    device: auto
```

### LLM Service Config

```yaml
# config/llm_config.yaml
host: 0.0.0.0
port: 6545
default_model: Qwen3.5-0.8B-OptiQ
models:
  - Qwen3.5-0.8B-OptiQ
  - Qwen3.5-2B-OptiQ
```

## Error Handling

### STT Service Errors

- Model loading timeout → Return error with retry suggestion
- Audio decode error → Return specific error code

### LLM Service Errors

- Model not loaded → Return 503 Service Unavailable
- Processing timeout → Return partial result or error

### Retry Strategy

- Client should retry on connection errors
- Exponential backoff: 1s → 2s → 4s

## Security

- Services listen on localhost by default (not exposed to network)
- Optional API key for production deployments
- CORS configuration for web clients

## Monitoring

- Health check endpoints for each service
- Metrics: latency, throughput, memory usage
- Logs: structured JSON logging

## Future Improvements

1. Add gRPC support for better performance
2. Add service discovery (consul/etcd)
3. Add load balancing for multiple STT instances
4. Add caching layer for LLM results
