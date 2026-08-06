#!/usr/bin/env python3
"""
ClaudeShield Tray - system tray controller for the proxy.

Shows the ClaudeShield badge icon in the Windows system tray.
Green dot overlay = proxy running. Red = stopped.
Click to toggle. Right-click for menu.

Native Windows toast notifications on start/stop/error.
Falls back to headless mode if pystray is not installed.

Usage:
  python shield-tray.py
  python shield-tray.py --upstream https://api.lumosel.vip

Optional deps (tray mode only):
  pip install pystray pillow
"""

import os
import sys
import threading
import subprocess
import time
import json
import urllib.request

PROXY_SCRIPT = os.path.join(os.path.expanduser("~"), ".claude", "retry-proxy.py")
ICON_PATH = os.path.join(os.path.expanduser("~"), ".claude", "shield-icon.png")
DEFAULT_UPSTREAM = "https://agentrouter.org"
PORT = 8787

# Colors matching the brand
COLOR_ORANGE = (255, 112, 77, 255)
COLOR_GREEN = (131, 217, 87, 255)
COLOR_RED = (239, 98, 88, 255)
COLOR_DARK = (17, 21, 26, 255)


def fetch_stats():
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/stats", timeout=2) as r:
            return json.loads(r.read())
    except Exception:
        return None


def is_proxy_running():
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def windows_toast(title, message, icon_path=None):
    """Show a native Windows toast notification via PowerShell."""
    if sys.platform != "win32":
        print(f"[{title}] {message}")
        return
    ps_script = f'''
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null

$template = @"
<toast>
    <visual>
        <binding template="ToastGeneric">
            <text>{title}</text>
            <text>{message}</text>
        </binding>
    </visual>
</toast>
"@

$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($template)
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("ClaudeShield").Show($toast)
'''
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True, timeout=5
        )
    except Exception:
        # Fallback: just print
        print(f"[{title}] {message}")


def make_icon(running=True):
    """Create tray icon image. Uses PNG if available, else generates shield."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None

    if os.path.exists(ICON_PATH):
        img = Image.open(ICON_PATH).convert("RGBA")
        img = img.resize((64, 64), Image.NEAREST)
    else:
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.polygon(
            [(32, 4), (56, 14), (56, 34), (32, 60), (8, 34), (8, 14)],
            fill=COLOR_ORANGE, outline=(255, 128, 93, 255),
        )

    # Draw status dot in bottom-right corner
    draw = ImageDraw.Draw(img)
    dot_color = COLOR_GREEN if running else COLOR_RED
    dot_x, dot_y = 48, 48
    draw.ellipse(
        [(dot_x - 8, dot_y - 8), (dot_x + 8, dot_y + 8)],
        fill=COLOR_DARK, outline=dot_color, width=2,
    )
    draw.ellipse(
        [(dot_x - 5, dot_y - 5), (dot_x + 5, dot_y + 5)],
        fill=dot_color,
    )
    return img


class TrayController:
    def __init__(self, upstream=DEFAULT_UPSTREAM):
        self.upstream = upstream
        self.proxy_proc = None
        self.should_stop = False
        self.last_stats = None
        self.last_converted = 0
        self.icon = None

    def start_proxy(self, notify=True):
        if self.proxy_proc and self.proxy_proc.poll() is None:
            return
        self.proxy_proc = subprocess.Popen(
            [sys.executable, PROXY_SCRIPT, "--start",
             "--upstream", self.upstream, "--port", str(PORT)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(2)
        if is_proxy_running() and notify:
            windows_toast(
                "ClaudeShield Started",
                f"Proxy running on port {PORT}.\nUpstream: {self.upstream}",
            )
            self._update_icon()

    def stop_proxy(self, notify=True):
        if self.proxy_proc and self.proxy_proc.poll() is None:
            self.proxy_proc.terminate()
            try:
                self.proxy_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proxy_proc.kill()
            self.proxy_proc = None
        if notify:
            windows_toast("ClaudeShield Stopped", "Proxy has been stopped.")
            self._update_icon()

    def toggle(self, icon=None, item=None):
        if is_proxy_running():
            self.stop_proxy()
        else:
            self.start_proxy()

    def show_stats(self, icon=None, item=None):
        stats = fetch_stats()
        if stats:
            msg = (
                f"Version: {stats['version']}\n"
                f"Uptime: {stats['uptime_human']}\n"
                f"Requests: {stats['requests']}\n"
                f"Converted: {stats['converted']}\n"
                f"Passed: {stats['passed']}\n"
                f"Errors: {stats['errors']}\n"
                f"Retry rate: {stats['retry_rate']:.1%}"
            )
        else:
            msg = "Proxy is not running."
        windows_toast("ClaudeShield Stats", msg)

    def quit(self, icon=None, item=None):
        self.stop_proxy(notify=False)
        self.should_stop = True
        if self.icon:
            self.icon.stop()

    def _update_icon(self):
        if self.icon:
            running = is_proxy_running()
            img = make_icon(running)
            if img:
                self.icon.icon = img
            self.icon.title = self._tooltip()

    def _tooltip(self):
        stats = fetch_stats()
        if stats:
            return (f"ClaudeShield - {stats['requests']} req, "
                    f"{stats['converted']} converted")
        if is_proxy_running():
            return "ClaudeShield - Running"
        return "ClaudeShield - Stopped"

    def _monitor_loop(self):
        """Background thread: update tooltip, detect new conversions."""
        while not self.should_stop:
            time.sleep(5)
            stats = fetch_stats()
            if stats:
                # Notify on new conversions
                new_converted = stats.get("converted", 0)
                if new_converted > self.last_converted and self.last_converted >= 0:
                    diff = new_converted - self.last_converted
                    windows_toast(
                        "ClaudeShield: Auto-retry triggered",
                        f"Converted {diff} error(s) to 429.\n"
                        f"Total: {new_converted} conversions, "
                        f"{stats['requests']} requests."
                    )
                self.last_converted = new_converted
            if self.icon:
                self.icon.title = self._tooltip()
                # Update icon color based on running state
                running = is_proxy_running()
                img = make_icon(running)
                if img:
                    self.icon.icon = img

    def run(self):
        try:
            import pystray
        except ImportError:
            print("Tray mode requires: pip install pystray pillow")
            print("Falling back to headless mode...")
            os.execv(sys.executable, [sys.executable, PROXY_SCRIPT, "--start",
                                      "--upstream", self.upstream, "--port", str(PORT)])
            return

        menu = pystray.Menu(
            pystray.MenuItem("Start proxy", self.start_proxy, default=True),
            pystray.MenuItem("Stop proxy", self.stop_proxy),
            pystray.MenuItem("Show stats", self.show_stats),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self.quit),
        )

        img = make_icon(False)
        self.icon = pystray.Icon(
            "ClaudeShield",
            img or "S",
            "ClaudeShield - Starting...",
            menu,
        )

        # Start proxy on launch
        threading.Thread(target=self.start_proxy, kwargs={"notify": True}, daemon=True).start()

        # Start monitor thread
        t = threading.Thread(target=self._monitor_loop, daemon=True)
        t.start()

        self.icon.run()


def main():
    upstream = DEFAULT_UPSTREAM
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--upstream" and i + 1 < len(args):
            upstream = args[i + 1]
            i += 2
        else:
            i += 1

    if not os.path.exists(PROXY_SCRIPT):
        print(f"retry-proxy.py not found at {PROXY_SCRIPT}")
        print("Run the installer first:")
        print("  curl -sSL https://raw.githubusercontent.com/brainstormersia-cmd/agentrouter-autoretry-claudecode/main/install.py | python")
        sys.exit(1)

    controller = TrayController(upstream)
    controller.run()


if __name__ == "__main__":
    main()
