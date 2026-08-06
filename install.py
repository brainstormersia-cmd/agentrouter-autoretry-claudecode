#!/usr/bin/env python3
"""
ClaudeShield one-liner installer.

  curl -sSL https://raw.githubusercontent.com/brainstormersia-cmd/agentrouter-autoretry-claudecode/main/install.py | python

Downloads retry-proxy.py to ~/.claude/ and launches interactive setup.
"""

import os
import sys
import urllib.request

REPO = "brainstormersia-cmd/agentrouter-autoretry-claudecode"
RAW = f"https://raw.githubusercontent.com/{REPO}/main/retry-proxy.py"
DEST = os.path.join(os.path.expanduser("~"), ".claude", "retry-proxy.py")


def main():
    print()
    print("  ClaudeShield Installer")
    print("  --------------------")
    print()

    os.makedirs(os.path.dirname(DEST), exist_ok=True)

    print(f"  Downloading retry-proxy.py...")
    try:
        urllib.request.urlretrieve(RAW, DEST)
    except Exception as e:
        print(f"  Download failed: {e}")
        print(f"  Manual: curl -O {RAW}")
        return 1

    print(f"  Saved to {DEST}")
    print()

    # Launch interactive setup
    os.execv(sys.executable, [sys.executable, DEST])


if __name__ == "__main__":
    sys.exit(main() or 0)
