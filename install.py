#!/usr/bin/env python3
"""
ClaudeShield one-liner installer.

  curl -sSL https://raw.githubusercontent.com/brainstormersia-cmd/agentrouter-autoretry-claudecode/main/install.py | python

Downloads all ClaudeShield files to ~/.claude/ and launches interactive setup.
Works on Windows, macOS, and Linux. Zero dependencies.
"""

import os
import sys
import stat
import urllib.request

REPO = "brainstormersia-cmd/agentrouter-autoretry-claudecode"
BASE = f"https://raw.githubusercontent.com/{REPO}/main"

FILES = [
    "retry-proxy.py",
    "shield-tray.ps1",
    "claude-shield.bat",
    "claude-shield.sh",
]

DEST_DIR = os.path.join(os.path.expanduser("~"), ".claude")


def main():
    print()
    print("  ClaudeShield Installer")
    print("  --------------------")
    print()

    os.makedirs(DEST_DIR, exist_ok=True)

    for filename in FILES:
        url = f"{BASE}/{filename}"
        dest = os.path.join(DEST_DIR, filename)
        print(f"  Downloading {filename}...", end=" ", flush=True)
        try:
            urllib.request.urlretrieve(url, dest)
            # Make shell scripts executable on Unix
            if filename.endswith(".sh") and sys.platform != "win32":
                os.chmod(dest, os.stat(dest).st_mode | stat.S_IEXEC)
            print("OK")
        except Exception as e:
            print(f"FAILED: {e}")
            print(f"  Manual: curl -O {url}")
            return 1

    print()
    print(f"  All files saved to {DEST_DIR}")
    print()

    # Launch interactive setup on the proxy
    proxy_path = os.path.join(DEST_DIR, "retry-proxy.py")
    os.execv(sys.executable, [sys.executable, proxy_path])


if __name__ == "__main__":
    sys.exit(main() or 0)
