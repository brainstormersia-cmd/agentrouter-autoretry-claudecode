#!/usr/bin/env python3
"""
ClaudeShield Tray - system tray icon wrapper.

Shows the ClaudeShield logo in the Windows system tray with:
- Right-click menu: Start/Stop proxy, Open stats, Quit
- Tooltip showing proxy status
- Green icon = running, red = stopped

Requires: pip install pystray pillow (only for tray mode)
Without these, falls back to headless CLI mode.

Usage:
  python shield-tray.py
"""

import os
import sys
import threading
import subprocess
import time
import json
import urllib.request

PROXY_SCRIPT = os.path.join(os.path.expanduser("~"), ".claude", "retry-proxy.py")
UPSTREAM = "https://agentrouter.org"
PORT = 8787
ICON_PATH = os.path.join(os.path.expanduser("~"), ".claude", "shield-icon.png")


def fetch_stats():
    """Get proxy stats from /stats endpoint."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/stats", timeout=2) as r:
            return json.loads(r.read())
    except Exception:
        return None


def is_proxy_running():
    """Check if the proxy is running by hitting /health."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def run_tray():
    """Run the system tray icon (requires pystray + pillow)."""
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        print("Tray mode requires: pip install pystray pillow")
        print("Falling back to headless mode...")
        run_headless()
        return

    # Create a simple shield icon if no PNG available
    if os.path.exists(ICON_PATH):
        image = Image.open(ICON_PATH)
    else:
        # Generate a simple orange shield icon
        image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        # Shield shape
        draw.polygon([(32, 4), (56, 14), (56, 34), (32, 60), (8, 34), (8, 14)],
                     fill=(255, 112, 77, 255), outline=(255, 128, 93, 255))

    proxy_process = [None]

    def start_proxy():
        if proxy_process[0] and proxy_process[0].poll() is None:
            return  # Already running
        proxy_process[0] = subprocess.Popen(
            [sys.executable, PROXY_SCRIPT, "--start", "--upstream", UPSTREAM, "--port", str(PORT)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def stop_proxy():
        if proxy_process[0] and proxy_process[0].poll() is None:
            proxy_process[0].terminate()
            proxy_process[0] = None

    def on_start(icon, item):
        start_proxy()
        icon.notify("ClaudeShield started on port 8787", "Proxy")

    def on_stop(icon, item):
        stop_proxy()
        icon.notify("ClaudeShield stopped", "Proxy")

    def on_stats(icon, item):
        stats = fetch_stats()
        if stats:
            msg = (f"Requests: {stats['requests']}\n"
                   f"Converted: {stats['converted']}\n"
                   f"Errors: {stats['errors']}\n"
                   f"Uptime: {stats['uptime_human']}")
        else:
            msg = "Proxy not running"
        icon.notify(msg, "ClaudeShield Stats")

    def on_quit(icon, item):
        stop_proxy()
        icon.stop()

    def get_tooltip():
        if is_proxy_running():
            stats = fetch_stats()
            if stats:
                return f"ClaudeShield - {stats['requests']} req, {stats['converted']} converted"
            return "ClaudeShield - Running"
        return "ClaudeShield - Stopped"

    def update_icon(icon):
        while icon.visible:
            tooltip = get_tooltip()
            icon.title = tooltip
            time.sleep(5)

    # Build menu
    menu = pystray.Menu(
        pystray.MenuItem("Start proxy", on_start, default=True),
        pystray.MenuItem("Stop proxy", on_stop),
        pystray.MenuItem("Show stats", on_stats),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", on_quit),
    )

    icon = pystray.Icon("ClaudeShield", image, "ClaudeShield", menu)

    # Start proxy on launch
    start_proxy()

    # Background thread to update tooltip
    t = threading.Thread(target=update_icon, args=(icon,), daemon=True)
    t.start()

    icon.run()


def run_headless():
    """Fallback: just run the proxy without tray."""
    print("ClaudeShield - Headless mode")
    print(f"Starting proxy on port {PORT}...")
    os.execv(sys.executable, [sys.executable, PROXY_SCRIPT, "--start",
                              "--upstream", UPSTREAM, "--port", str(PORT)])


if __name__ == "__main__":
    if os.path.exists(PROXY_SCRIPT):
        run_tray()
    else:
        print(f"retry-proxy.py not found at {PROXY_SCRIPT}")
        print("Run the installer first:")
        print("  curl -sSL https://raw.githubusercontent.com/brainstormersia-cmd/agentrouter-autoretry-claudecode/main/install.py | python")
        sys.exit(1)
