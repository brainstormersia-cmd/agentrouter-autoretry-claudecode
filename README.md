# 🛡️ ClaudeShield

### Never let Claude Code crash on gateway rate limits again

A lightweight proxy that makes Claude Code survive rate limits, outages, and provider switches - automatically. **Zero dependencies. 1-minute setup.**

---

## 🚀 Get Started (1 minute)

### Step 1: Download `retry-proxy.py`

Download `retry-proxy.py` from this repo.

### Step 2: Run it

```bash
python retry-proxy.py
```

It will ask you 3 simple questions:

```
  ClaudeShield Setup  v2.0.0

  This will configure Claude Code to survive rate limits.
  Answer 3 questions and you're done.

  Which gateway are you using?
    1. AgentRouter (https://agentrouter.org)
    2. Lumosel (https://api.lumosel.vip)
    3. Aerolink (https://capi.aerolink.lat)
    4. Custom URL

  > 1

  Enter your AgentRouter API key
  > sk-xxxx...

  Which model? (press Enter for claude-opus-4-8)
  > 

  [OK] Copied proxy to ~/.claude/retry-proxy.py
  [OK] Updated ~/.claude/settings.json

  Done! Setup complete.

  Now run these two commands:

    1. Start the proxy:
       python ~/.claude/retry-proxy.py --start

    2. In another terminal, launch Claude Code:
       claude --dangerously-skip-permissions

  That's it! Claude Code will now survive rate limits.
```

### Step 3: Launch Claude Code

```bash
claude --dangerously-skip-permissions
```

**Done.** Claude Code now survives rate limits, outages, and provider switches automatically.

---

## 🎯 What Problem Does This Solve?

When you run Claude Code against a third-party gateway, it crashes on rate limits:

| What happens | Gateway returns | Without ClaudeShield | With ClaudeShield |
|:--|:--|:--|:--|
| Rate limit hit | `403` or `400` | 💥 Crashes, restart manually | ✅ Auto-retries |
| Provider saturated | `429` | ⏳ Sometimes retries | ✅ Retries |
| Gateway timeout | `504` | 💥 Crashes | ✅ Auto-retries |
| Error in Chinese | `403 用户额度不足` | 💥 Doesn't recognize it | ✅ Auto-retries |
| Network drops | `ConnectionReset` | 💥 Crashes | ✅ Auto-retries |
| Wrong model | `403 "no access"` | 💥 Crashes | ⚠️ Fails fast (no loop) |

---

## ⚙️ How It Works

```
Claude Code → localhost:8787 (ClaudeShield) → Your gateway
                    ↓
          403 rate limit    → 429 + Retry-After  → Claude Code retries ✅
          400 "plan limit"  → 429 + Retry-After  → Claude Code retries ✅
          504 gateway out   → 429 + Retry-After  → Claude Code retries ✅
          429 / 5xx         → pass + Retry-After → Claude Code retries ✅
          network drop      → 503 + Retry-After  → Claude Code retries ✅
          wrong model       → pass through        → Fails fast (no loop) ⚠️
          200 + SSE stream  → direct passthrough  → Normal response ✅
```

### Circuit breaker (the important part)

Claude Code compacts context at 95% using a "small model" (e.g. `claude-sonnet-5`). If your gateway doesn't have that model, you get `403 "no access to model"`. Without ClaudeShield, this loops forever and freezes your session. ClaudeShield detects this and **fails fast** instead of looping.

---

## 📋 Requirements

- **Python 3.8+** (already installed on most systems)
- **Claude Code v2.1.186+**

No `pip install` needed. ClaudeShield uses only Python's standard library.

---

## 🔧 Commands

```bash
python retry-proxy.py                  # Interactive setup (recommended)
python retry-proxy.py --start          # Just start the proxy (skip setup)
python retry-proxy.py --start --upstream https://api.lumosel.vip  # Different gateway
python retry-proxy.py --start --port 9090  # Custom port
python retry-proxy.py --version       # Show version
```

---

## 🖥️ Auto-Start (optional)

Want the proxy to start automatically when you boot your computer?

<details>
<summary><b>Windows</b></summary>

1. Press `Win+R`, type `shell:startup`, press Enter
2. Create a file `claude-shield.bat`:
```batch
@echo off
start /min pythonw "%USERPROFILE%\.claude\retry-proxy.py" --start
```
</details>

<details>
<summary><b>macOS / Linux</b></summary>

```bash
mkdir -p ~/.config/systemd/user/
cp claude-proxy.service ~/.config/systemd/user/claude-shield.service
systemctl --user enable --now claude-shield
```
</details>

---

## 🐛 Troubleshooting

| Problem | Solution |
|:--|:--|
| `Connection refused` | The proxy isn't running. Start it first: `python retry-proxy.py --start` |
| Still crashing | Check if the proxy log shows `PASS permanent` - that means a real error (wrong model/auth), not a rate limit |
| Frozen at 95% context | Update to the latest version. The circuit breaker prevents this |
| Log shows `401` | Normal when testing with curl. Claude Code sends proper auth, the proxy forwards it |

### Reading the logs

```
[14:23:05] CONVERT 403 -> 429 | {"error":"用户额度不足..."}
[14:23:25] 200
```

- `CONVERT` = rate limit converted, Claude Code is retrying ✅
- `PASS permanent` = real error, not retried ⚠️
- `RETRY` = already retryable, forwarded ✅
- `NETERR` = network error, converted to retry ✅
- `200` = success ✅

---

## 🌐 Supported Gateways

Any Anthropic-compatible gateway:

- ✅ AgentRouter
- ✅ Lumosel
- ✅ Aerolink
- ✅ Kilo Gateway
- ✅ Any custom gateway

---

## 🔒 Privacy

- **No API keys stored** in the proxy - it forwards what Claude Code sends
- **No external dependencies** - pure Python, nothing to install
- **Local only** - listens on `127.0.0.1`, never exposed externally
- **No telemetry** - zero tracking

---

## 📄 License

MIT - use it, share it, modify it.

---

<p align="center">Made with ⚡ by developers tired of restarting Claude Code</p>
