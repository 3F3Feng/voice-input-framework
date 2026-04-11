#!/bin/bash
# 卸载 Voice Input Framework 守护进程

PLIST_DIR=~/Library/LaunchAgents

echo "Uninstalling Voice Input Framework daemons..."

# 停止并卸载服务
echo "Stopping services..."
launchctl unload "$PLIST_DIR/com.voiceinput.stt.plist" 2>/dev/null
launchctl unload "$PLIST_DIR/com.voiceinput.llm.plist" 2>/dev/null

# 删除 plist 文件
echo "Removing plist files..."
rm -f "$PLIST_DIR/com.voiceinput.stt.plist"
rm -f "$PLIST_DIR/com.voiceinput.llm.plist"

echo ""
echo "Daemons uninstalled."
echo ""
echo "Note: Log files may still exist at:"
echo "  /tmp/voiceinput-stt.log"
echo "  /tmp/voiceinput-llm.log"
