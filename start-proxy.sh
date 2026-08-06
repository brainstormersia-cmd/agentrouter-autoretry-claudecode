#!/bin/bash
# ============================================================
#  Claude Code Retry Proxy - macOS/Linux Auto-Start
#  Place this file in: ~/.local/bin/start-claude-proxy.sh
#  Then run: chmod +x ~/.local/bin/start-claude-proxy.sh
# ============================================================

# --- Configure your upstream gateway here ---
UPSTREAM="https://agentrouter.org"
PORT=8787

# --- Path to the proxy script ---
PROXY_SCRIPT="$HOME/.claude/retry-proxy.py"

# --- Start in background ---
python3 "$PROXY_SCRIPT" --upstream "$UPSTREAM" --port "$PORT" &
echo "Proxy started on http://127.0.0.1:$PORT -> $UPSTREAM"
echo "PID: $!"
