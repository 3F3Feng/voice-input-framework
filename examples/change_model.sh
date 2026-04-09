#!/bin/bash
# Voice Input Framework - Model Selection Helper (Linux/macOS)
# 
# Usage:
#   ./change_model.sh              # Show current model
#   ./change_model.sh list         # List available models
#   ./change_model.sh whisper      # Switch to whisper model
#   ./change_model.sh qwen_asr     # Switch to qwen_asr model

SERVER="${SERVER:-localhost:6543}"

if [ $# -eq 0 ]; then
  echo "🔍 Fetching current model status..."
  echo ""
  curl -s "http://${SERVER}/health" | python3 -m json.tool | grep -E '"current_model"|"loaded_models"|"status"|"version"|"uptime_seconds"'
  echo ""
  exit 0
fi

case "$1" in
  list)
    echo "📋 Available models:"
    echo ""
    curl -s "http://${SERVER}/models" | python3 -m json.tool
    ;;
  *)
    model_name="$1"
    echo "🔄 Switching to model: $model_name"
    echo ""
    
    response=$(curl -s -X POST "http://${SERVER}/models/select" \
      -d "model_name=$model_name" \
      -w "\n%{http_code}")
    
    http_code=$(echo "$response" | tail -n1)
    body=$(echo "$response" | head -n-1)
    
    if [ "$http_code" = "200" ]; then
      echo "✅ Success!"
      echo "$body" | python3 -m json.tool
    else
      echo "❌ Error (HTTP $http_code):"
      echo "$body" | python3 -m json.tool 2>/dev/null || echo "$body"
    fi
    ;;
esac
