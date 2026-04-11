#!/bin/bash
# 安装 Voice Input Framework 守护进程

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_DIR=~/Library/LaunchAgents

echo "Installing Voice Input Framework daemons..."

# 检查 plist 文件是否存在
if [[ ! -f "$SCRIPT_DIR/com.voiceinput.stt.plist" ]]; then
    echo "Error: com.voiceinput.stt.plist not found"
    exit 1
fi

if [[ ! -f "$SCRIPT_DIR/com.voiceinput.llm.plist" ]]; then
    echo "Error: com.voiceinput.llm.plist not found"
    exit 1
fi

# 创建 LaunchAgents 目录（如果不存在）
mkdir -p "$PLIST_DIR"

# 停止已有服务（如果存在）
echo "Stopping existing services..."
launchctl unload "$PLIST_DIR/com.voiceinput.stt.plist" 2>/dev/null
launchctl unload "$PLIST_DIR/com.voiceinput.llm.plist" 2>/dev/null

# 复制 plist 文件
echo "Copying plist files..."
cp "$SCRIPT_DIR/com.voiceinput.stt.plist" "$PLIST_DIR/"
cp "$SCRIPT_DIR/com.voiceinput.llm.plist" "$PLIST_DIR/"

# 加载服务
echo "Loading services..."
launchctl load "$PLIST_DIR/com.voiceinput.stt.plist"
launchctl load "$PLIST_DIR/com.voiceinput.llm.plist"

echo ""
echo "Daemons installed and started."
echo ""
echo "Logs:"
echo "  STT: /tmp/voiceinput-stt.log"
echo "  LLM: /tmp/voiceinput-llm.log"
echo ""
echo "Check status:"
echo "  launchctl list | grep voiceinput"
echo ""
echo "Stop services:"
echo "  launchctl unload $PLIST_DIR/com.voiceinput.stt.plist"
echo "  launchctl unload $PLIST_DIR/com.voiceinput.llm.plist"
