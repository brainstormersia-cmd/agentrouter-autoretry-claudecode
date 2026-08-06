#!/bin/bash
# ============================================================
#  ClaudeShield - macOS/Linux launcher
#  Starts the proxy + menu bar icon together.
# ============================================================
set -e

PROXY_SCRIPT="$HOME/.claude/retry-proxy.py"
UPSTREAM="${1:-https://agentrouter.org}"
PORT=8787

if [ ! -f "$PROXY_SCRIPT" ]; then
    echo "retry-proxy.py not found at $PROXY_SCRIPT"
    echo "Run the installer first:"
    echo "  curl -sSL https://raw.githubusercontent.com/brainstormersia-cmd/agentrouter-autoretry-claudecode/main/install.py | python"
    exit 1
fi

# Kill any old proxy on the port
lsof -ti tcp:$PORT | xargs kill -9 2>/dev/null || true

# Start proxy in background
echo "Starting ClaudeShield proxy on port $PORT..."
nohup python3 "$PROXY_SCRIPT" --start --upstream "$UPSTREAM" --port "$PORT" > /dev/null 2>&1 &
PROXY_PID=$!
echo "Proxy PID: $PROXY_PID"

# macOS: show notification
if [ "$(uname)" = "Darwin" ]; then
    osascript -e "display notification \"Proxy running on port $PORT\" with title \"ClaudeShield\""
fi

echo ""
echo "ClaudeShield is running."
echo "  Proxy:   http://127.0.0.1:$PORT"
echo "  Stats:   http://127.0.0.1:$PORT/stats"
echo ""
echo "Press Ctrl+C to stop."

# Wait for proxy process
wait $PROXY_PID
