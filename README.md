# 🛡️ ClaudeShield

### Never let Claude Code crash on gateway rate limits again

A lightweight local proxy that makes Claude Code **immune** to rate-limit errors from third-party gateways (AgentRouter, Lumosel, Aerolink, and any Anthropic-compatible gateway).

---

## 🎯 The Problem

When you run Claude Code against a third-party gateway, it crashes — a lot:

| What happens | Gateway returns | Claude Code does |
|:--|:--|:--|
| Rate limit hit | `403` or `400` | 💥 **Stops. Dead. You restart manually.** |
| Provider saturated | `429` (sometimes) | ⏳ Retries (if you're lucky) |
| Gateway timeout | `504` (nginx) | 💥 **Stops. Dead.** |
| Quota in Chinese | `403 用户额度不足` | 💥 **Doesn't even recognize it as a rate limit** |

Claude Code only auto-retries `429` and `5xx`. But gateways love returning `403` and `400` for rate limits — which Claude Code treats as **permanent client errors** and gives up instantly.

## ✅ The Solution

ClaudeShield sits between Claude Code and your gateway, **converting non-retryable errors into retryable ones**:

```
Claude Code → localhost:8787 (ClaudeShield) → gateway
                    ↓
          403 rate limit    → 429 + Retry-After  → ✅ Claude Code retries
          400 "plan limit"  → 429 + Retry-After  → ✅ Claude Code retries
          504 gateway out   → 429 + Retry-After  → ✅ Claude Code retries
          429               → pass + Retry-After → ✅ Claude Code retries
          5xx server error  → pass + Retry-After → ✅ Claude Code retries
          network drop      → 503 + Retry-After  → ✅ Claude Code retries
          403 "no model"    → pass through       → ❌ Fail fast (no infinite loop)
          401 / 404         → pass through       → ❌ Fail fast
          200 + SSE stream  → direct passthrough  → ✅ Normal response
```

With Claude Code's built-in [`CLAUDE_CODE_RETRY_WATCHDOG`](https://code.claude.com/docs/en/env-vars) enabled, it now retries **indefinitely** through outages — your session stays alive instead of dying.

## 🚀 Quick Start (2 minutes)

### 1. Download

```bash
curl -O https://raw.githubusercontent.com/brainstormersia-cmd/agentrouter-autoretry-claudecode/main/retry-proxy.py
```

Or just download `retry-proxy.py` from this repo and place it anywhere (e.g. `~/.claude/retry-proxy.py`).

### 2. Start the proxy

```bash
# AgentRouter
python retry-proxy.py --upstream https://agentrouter.org

# Lumosel
python retry-proxy.py --upstream https://api.lumosel.vip

# Aerolink
python retry-proxy.py --upstream https://capi.aerolink.lat

# Any Anthropic-compatible gateway
python retry-proxy.py --upstream https://your-gateway.com
```

You'll see:
```
+--------------------------------------------------+
|  Claude Code Retry Proxy v1.0                    |
|  Listen:   http://127.0.0.1:8787                 |
|  Upstream: https://agentrouter.org               |
|  ...                                             |
+--------------------------------------------------+
```

### 3. Point Claude Code at the proxy

Edit `~/.claude/settings.json` (create it if it doesn't exist):

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:8787",
    "ANTHROPIC_API_KEY": "your-gateway-api-key",
    "ANTHROPIC_MODEL": "claude-opus-5",
    "CLAUDE_CODE_RETRY_WATCHDOG": "1",
    "CLAUDE_CODE_MAX_RETRIES": "300"
  }
}
```

> **Important:** Set `ANTHROPIC_BASE_URL` to `http://127.0.0.1:8787` (the proxy), **not** your gateway URL. The proxy forwards everything to the gateway automatically.

### 4. Launch Claude Code

```bash
claude --dangerously-skip-permissions
```

That's it. Claude Code now survives gateway outages, rate limits, and provider switches automatically.

## ⚙️ How It Works

### Error detection

ClaudeShield reads the **response body** of every error to decide whether it's a rate limit (retryable) or a permanent failure (don't retry):

**Retryable keywords** (English + Chinese):
> `rate`, `limit`, `quota`, `throttle`, `overloaded`, `capacity`, `saturated`, `timeout`, `unavailable`, `retry`, `insufficient`, `balance`, `exceeded`, `exhausted`, `temporarily`, `maintenance`, `额度` (quota), `不足` (insufficient), `余额` (balance), `限制` (limit), `耗尽` (exhausted)...

**Permanent keywords** (fail fast, don't retry):
> `not authorized to access`, `model not found`, `invalid model`, `unauthorized`, `authentication`, `无权访问模型` (no access to model)...

### Circuit breaker

This is the key feature that prevents **infinite retry loops**:

> When Claude Code tries to compact context at 95%, it uses the "small fast model" (e.g. `claude-sonnet-5`). If your gateway doesn't serve that model, it returns `403 "no access to model"`. Without a circuit breaker, the proxy would convert this to `429`, Claude Code would retry the same model, get the same `403`, convert to `429` again → **infinite loop → frozen session at 95%**.

ClaudeShield detects the `"no access to model"` keyword and **passes the error through without conversion**. Claude Code fails immediately on that specific request instead of looping forever.

### Streaming support

Claude Code uses Server-Sent Events (SSE) for streaming responses. ClaudeShield detects `text/event-stream` responses and **streams them through in real-time** without buffering — so you see tokens as they arrive, no latency added.

### Network resilience

If the gateway drops the connection mid-request (common with Cloudflare-fronted gateways), ClaudeShield catches the `ConnectionResetError` and returns a `503 + Retry-After` — so Claude Code retries instead of crashing.

## 📋 Requirements

| Requirement | Version |
|:--|:--|
| Python | 3.8+ (standard library only — no pip install needed) |
| Claude Code | v2.1.186+ (for `CLAUDE_CODE_RETRY_WATCHDOG`) |

## 🔧 Configuration Reference

| Environment variable | Purpose | Default |
|:--|:--|:--|
| `ANTHROPIC_BASE_URL` | Point at the proxy | `http://127.0.0.1:8787` |
| `CLAUDE_CODE_RETRY_WATCHDOG` | Retry 429/529 indefinitely | `1` |
| `CLAUDE_CODE_MAX_RETRIES` | Max retry attempts | `300` (~3 hours of backoff) |

## 🖥️ Auto-Start (optional)

<details>
<summary><b>Windows</b> — run on boot</summary>

1. Press `Win+R`, type `shell:startup`, press Enter
2. Create a file `claude-shield.bat` in that folder:

```batch
@echo off
REM Configure your gateway here:
set UPSTREAM=https://agentrouter.org
set PORT=8787
set PROXY_SCRIPT=%USERPROFILE%\.claude\retry-proxy.py

start /min pythonw "%PROXY_SCRIPT%" --upstream %UPSTREAM% --port %PORT%
```

Or use the included `start-proxy.bat`.
</details>

<details>
<summary><b>macOS / Linux</b> — systemd service</summary>

```bash
# Copy the service file
cp claude-proxy.service ~/.config/systemd/user/claude-shield.service

# Edit the upstream URL inside if needed
nano ~/.config/systemd/user/claude-shield.service

# Enable and start
systemctl --user enable --now claude-shield
```

Service file content:
```ini
[Unit]
Description=ClaudeShield - Claude Code Retry Proxy
After=network.target

[Service]
ExecStart=/usr/bin/python3 %h/.claude/retry-proxy.py --upstream https://agentrouter.org --port 8787
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```
</details>

## 🐛 Troubleshooting

| Problem | Solution |
|:--|:--|
| `Connection refused` | Proxy isn't running. Start it before launching Claude Code. |
| Still getting `403` errors | Check the proxy logs — if it says `PASS-THROUGH (permanent)`, the error is a real model/auth issue, not a rate limit. Fix your model config. |
| Session frozen at 95% context | Make sure you have the latest `retry-proxy.py` with the circuit breaker. Old versions loop forever on "no access to model" errors. |
| Proxy logs show `401` | Normal when testing with curl. Claude Code sends proper auth headers and the proxy forwards them. |
| Proxy logs show `404` on `count_tokens` | Normal — some gateways don't implement this endpoint. The proxy passes it as non-retryable, Claude Code falls back to local token estimation. |
| Proxy logs show `504` | Gateway timeout from nginx. The proxy converts it to `429` and Claude Code retries automatically. |

### Reading the logs

ClaudeShield logs to stderr in real-time:

```
[14:23:05] [42] CONVERT 403 -> 429 | {"error":"用户额度不足, 剩余额度: $-71.35..."}
[14:23:06] [42] "POST /v1/messages?beta=true HTTP/1.1" 429 -
[14:23:25] [43] "POST /v1/messages?beta=true HTTP/1.1" 200 -
```

- `CONVERT 403 -> 429` — rate limit detected, converted for retry
- `PASS-THROUGH 403 (permanent)` — real error, not retried (circuit breaker)
- `PASS-THROUGH 429 + Retry-After` — already retryable, forwarded as-is
- `PROXY ERROR -> 503` — network error, converted for retry
- `200` — success, response streamed through

## 🏗️ Why not patch Claude Code directly?

| Approach | Problem |
|:--|:--|
| Patching the Claude Code binary | Breaks on every update. Risk of corrupting the executable. |
| Waiting for Anthropic to fix it | Gateways return non-standard error codes — Anthropic can't anticipate every gateway's quirks. |
| **ClaudeShield proxy** | **Transparent. Works with any Claude Code version. Zero modifications. Gateway-agnostic.** |

## 🌐 Supported Gateways

Any Anthropic-compatible gateway that speaks the `/v1/messages` API:

- ✅ AgentRouter (`https://agentrouter.org`)
- ✅ Lumosel (`https://api.lumosel.vip`)
- ✅ Aerolink (`https://capi.aerolink.lat`)
- ✅ Kilo Gateway
- ✅ Any custom Anthropic-compatible proxy

## 📁 Files

| File | Description |
|:--|:--|
| `retry-proxy.py` | The proxy script (Python 3.8+, zero dependencies) |
| `start-proxy.bat` | Windows auto-start script |
| `start-proxy.sh` | macOS/Linux auto-start script |
| `claude-proxy.service` | systemd service file for Linux |
| `CLAUDE-RETRY-PROXY-README.md` | Detailed technical documentation |

## 🔒 Privacy & Security

- **No API keys stored** — ClaudeShield forwards whatever Claude Code sends, it never stores credentials
- **No external dependencies** — pure Python standard library, nothing to install
- **Local only** — listens on `127.0.0.1`, never exposes ports externally
- **No telemetry** — zero network calls except forwarding to your configured gateway

## 📄 License

MIT — use it, share it, modify it.

---

<p align="center">
  Made with ⚡ by developers tired of restarting Claude Code
</p>
