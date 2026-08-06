# ClaudeShield

<p align="center">
  <img src="assets/logo.png" alt="ClaudeShield" width="480">
</p>

<p align="center">
  A local reliability proxy that keeps Claude Code alive through gateway outages.
</p>

<p align="center">
  <a href="https://github.com/brainstormersia-cmd/agentrouter-autoretry-claudecode/blob/main/LICENSE">MIT License</a>
  &middot;
  <a href="#quickstart">Quickstart</a>
  &middot;
  <a href="#how-it-works">How It Works</a>
  &middot;
  <a href="#configuration">Configuration</a>
</p>

---

## The Problem

Claude Code auto-retries `429` and `5xx` errors, but stops dead on `403` and `400`. Most third-party gateways (AgentRouter, Lumosel, Aerolink) return `403` for quota limits instead of the standard `429`. Some return errors in Chinese (`用户额度不足`). Some return `200` with an error hidden inside the SSE stream body.

Result: your coding session crashes. You restart manually. You lose context.

## The Solution

ClaudeShield is a local Python proxy that sits between Claude Code and your gateway. It:

- Converts non-retryable `403`/`400`/`504` errors into `429` + `Retry-After`
- Peeks at the first 8KB of SSE streams to detect errors hidden in `200` responses
- Filters `data: null` SSE events that break Anthropic parsers
- Injects the `claude-cli/1.0.0` User-Agent header that AgentRouter requires
- Detects Chinese error messages (`用户额度不足`, `无权访问模型`)
- Uses a circuit breaker to prevent infinite retry loops on permanent errors
- Forwards all auth headers from Claude Code without storing them

```
Claude Code  -->  ClaudeShield (localhost:8787)  -->  AgentRouter
                       |
                       +-- 403 quota?   --> 429 + Retry-After (client retries)
                       +-- 504 timeout?  --> 429 + Retry-After (client retries)
                       +-- 200 + error in SSE? --> 503 (client retries)
                       +-- 403 no model? --> pass through (circuit breaker, fail fast)
                       +-- 200 OK?       --> stream passthrough (zero overhead)
```

## Quickstart

### 1. Download

```bash
curl -O https://raw.githubusercontent.com/brainstormersia-cmd/agentrouter-autoretry-claudecode/main/retry-proxy.py
```

### 2. Run interactive setup

```bash
python retry-proxy.py
```

Answer 3 questions: gateway, API key, model. It auto-configures `~/.claude/settings.json`.

### 3. Launch Claude Code

```bash
claude --dangerously-skip-permissions
```

Done. Claude Code now survives gateway outages automatically.

## How It Works

### Error Classification

| Gateway returns | Body contains | ClaudeShield action | Claude Code sees |
|:--|:--|:--|:--|
| `403` | `用户额度不足` (quota) | Convert to `429` + Retry-After: 20s | Auto-retry |
| `403` | `无权访问模型` (no model access) | Pass through (circuit breaker) | Fail fast |
| `400` | `rate_limit`, `quota`, `exhausted` | Convert to `429` + Retry-After: 20s | Auto-retry |
| `504` | Gateway timeout | Convert to `429` + Retry-After: 20s | Auto-retry |
| `429` | Rate limit | Pass through + Retry-After: 15s | Auto-retry |
| `500`-`530` | Server error | Pass through + Retry-After: 15s | Auto-retry |
| `200` | Empty body | Convert to `503` + Retry-After: 10s | Auto-retry |
| `200` | Non-JSON (HTML error page) | Convert to `503` + Retry-After: 10s | Auto-retry |
| `200` | SSE with `type:error` inside | Convert to `503` + Retry-After: 10s | Auto-retry |
| `200` + valid SSE | `message_start` present | Stream passthrough | Normal response |
| `401` | Invalid key | Pass through | Fail fast |
| `404` | Model not found | Pass through | Fail fast |
| Network error | Connection reset | Convert to `503` + Retry-After: 10s | Auto-retry |

### Circuit Breaker

The proxy distinguishes **retryable** errors from **permanent** ones:

- **Retryable**: quota, rate limit, capacity, timeout, server error, network drop
- **Permanent**: model not accessible, invalid auth, non-existent endpoint

Permanent errors pass through without conversion. This prevents the infinite retry loop that freezes Claude Code at 95% context compaction when the "small fast model" is not available on the gateway.

### SSE Stream Inspection

Claude Code uses streaming (Server-Sent Events). AgentRouter sometimes returns `HTTP 200` with an error payload inside the SSE stream body instead of `message_start`. ClaudeShield reads the first 8KB of every stream and checks for error markers before forwarding. If an error is detected, it converts the response to `503` so Claude Code retries.

### Stats Endpoint

The proxy exposes telemetry for monitoring:

```bash
curl http://127.0.0.1:8787/stats
```

```json
{
  "version": "3.1.0",
  "uptime_seconds": 45.9,
  "requests": 127,
  "converted": 14,
  "passed": 110,
  "errors": 3,
  "retry_rate": 0.1102
}
```

## Configuration

### settings.json

The interactive setup writes this to `~/.claude/settings.json`:

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:8787",
    "ANTHROPIC_API_KEY": "your-gateway-key",
    "CLAUDE_CODE_RETRY_WATCHDOG": "1",
    "CLAUDE_CODE_MAX_RETRIES": "300"
  }
}
```

### CLI

```bash
python retry-proxy.py                              # Interactive setup
python retry-proxy.py --start                     # Start with defaults
python retry-proxy.py --start --upstream https://api.lumosel.vip  # Different gateway
python retry-proxy.py --start --port 9090          # Custom port
python retry-proxy.py --version                    # Show version
```

### Supported Gateways

| Gateway | URL | Notes |
|:--|:--|:--|
| AgentRouter | `https://agentrouter.org` | Primary target. Injects required User-Agent. |
| Lumosel | `https://api.lumosel.vip` | Works out of the box. |
| Aerolink | `https://capi.aerolink.lat` | Works out of the box. |
| Any | `https://your-gateway.com` | Any Anthropic-compatible gateway. |

## Requirements

- Python 3.8+ (standard library only, no `pip install`)
- Claude Code v2.1.186+ (for `CLAUDE_CODE_RETRY_WATCHDOG`)

## Files

| File | Description |
|:--|:--|
| `retry-proxy.py` | The proxy (single file, zero dependencies) |
| `start-proxy.bat` | Windows auto-start script |
| `start-proxy.sh` | macOS/Linux auto-start script |
| `claude-proxy.service` | systemd service file |
| `assets/logo.png` | Logo |
| `assets/feature-403-429.png` | 403-to-429 conversion diagram |

## Privacy

- No API keys stored. The proxy forwards what Claude Code sends.
- No external dependencies. Pure Python standard library.
- Local only. Listens on `127.0.0.1`, never exposes ports externally.
- No telemetry. Zero network calls except forwarding to your configured gateway.

## License

MIT
