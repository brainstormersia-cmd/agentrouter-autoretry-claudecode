# Claude Code Retry Proxy

**Rendi Claude Code immune ai blocchi dei gateway di terze parti (AgentRouter, Lumosel, Aerolink).**

[English below](#english)

---

## Il problema

Claude Code ritenta automaticamente gli errori **429** (rate limit standard) e **5xx** (server error). Però molti gateway restituiscono **403** o **400** quando il piano è limitato, e Claude Code **non ritenta** questi codici → si blocca → devi riavviare a mano.

Inoltre, alcuni gateway restituiscono errori in **cinese** (es. `用户额度不足` = "quota insufficiente") o **504 gateway timeout** di nginx, che Claude Code non riconosce come ritentabili.

## La soluzione

Un proxy HTTP locale (Python, zero dipendenze) che:

1. **Forwarda** tutte le richieste al gateway (preserva auth + header Claude Code)
2. **Converte** 403/400/504 rate-limit → 429 + `Retry-After` → Claude Code ritenta
3. **Circuit breaker**: errori permanenti (modello non accessibile, auth invalida) → passano senza conversione → **niente loop infiniti**
4. **Streaming SSE** passthrough (non bufferizza)
5. **Errori di rete** → 503 ritentabile

```
Claude Code → localhost:8787 (proxy) → gateway upstream
                 ↓
         403/400/504 rate-limit  → 429 + Retry-After  ✅ ritenta
         429/5xx server error     → + Retry-After       ✅ ritenta
         network error            → 503 + Retry-After   ✅ ritenta
         403 "no access to model" → pass-through        ❌ fail fast (no loop)
         401/404                  → pass-through        ❌ fail fast
         200 + SSE                → stream diretto      ✅ normale
```

## Setup (2 minuti)

### 1. Salva il file
Copia `retry-proxy.py` in `~/.claude/retry-proxy.py` (qualsiasi cartella va bene).

### 2. Avvia il proxy
```powershell
# AgentRouter
python ~/.claude/retry-proxy.py --upstream https://agentrouter.org

# Lumosel
python ~/.claude/retry-proxy.py --upstream https://api.lumosel.vip

# Aerolink
python ~/.claude/retry-proxy.py --upstream https://capi.aerolink.lat
```

### 3. Configura Claude Code
In `~/.claude/settings.json`:
```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:8787",
    "ANTHROPIC_API_KEY": "your-key",
    "ANTHROPIC_MODEL": "claude-opus-5",
    "CLAUDE_CODE_RETRY_WATCHDOG": "1",
    "CLAUDE_CODE_MAX_RETRIES": "300"
  }
}
```

### 4. Avvia Claude Code
```powershell
claude --dangerously-skip-permissions
```

## Requisiti
- Python 3.8+ (solo standard library, niente pip install)
- Claude Code v2.1.186+ (per `CLAUDE_CODE_RETRY_WATCHDOG`)

## Troubleshooting

| Problema | Soluzione |
|----------|-----------|
| "Connection refused" | Il proxy non gira. Avvialo prima di Claude Code |
| Loop infinito al 95% contesto | Il circuit breaker previene questo. Assicurati di avere l'ultima versione del proxy |
| Log mostra 404 su count_tokens | Normale, alcuni gateway non supportano l'endpoint. Il proxy lo passa come non-ritentabile |
| Proxy mostra 401 | Normale se testi con curl. Claude Code invia gli header corretti e il proxy li forwarda |

## Avvio automatico (opzionale)

### Windows
Crea un `.bat` in `shell:startup`:
```batch
@echo off
start /min pythonw "%USERPROFILE%\.claude\retry-proxy.py" --upstream https://agentrouter.org --port 8787
```

### macOS/Linux (systemd)
```ini
# ~/.config/systemd/user/claude-proxy.service
[Unit]
Description=Claude Code Retry Proxy
After=network.target

[Service]
ExecStart=/usr/bin/python3 %h/.claude/retry-proxy.py --upstream https://agentrouter.org --port 8787
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```
```bash
systemctl --user enable --now claude-proxy
```

---

# English

## The Problem

Claude Code auto-retries **429** and **5xx** errors. But many gateways return **403** or **400** for rate limits, which Claude Code treats as permanent client errors → it stops → you must restart manually.

Some gateways return errors in **Chinese** (e.g. `用户额度不足` = "quota insufficient") or **504 gateway timeout** from nginx, which Claude Code doesn't recognize as retryable.

## The Solution

A local HTTP proxy (Python, zero dependencies) that:

1. **Forwards** all requests to the gateway (preserves auth + Claude Code headers)
2. **Converts** 403/400/504 rate-limits → 429 + `Retry-After` → Claude Code retries
3. **Circuit breaker**: permanent errors (model not accessible, invalid auth) → pass through without conversion → **no infinite loops**
4. **SSE streaming** passthrough (no buffering)
5. **Network errors** → 503 retryable

## Setup (2 minutes)

### 1. Save the file
Copy `retry-proxy.py` to `~/.claude/retry-proxy.py` (any folder works).

### 2. Start the proxy
```bash
# AgentRouter
python ~/.claude/retry-proxy.py --upstream https://agentrouter.org

# Lumosel
python ~/.claude/retry-proxy.py --upstream https://api.lumosel.vip

# Aerolink
python ~/.claude/retry-proxy.py --upstream https://capi.aerolink.lat
```

### 3. Configure Claude Code
In `~/.claude/settings.json`:
```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:8787",
    "ANTHROPIC_API_KEY": "your-key",
    "ANTHROPIC_MODEL": "claude-opus-5",
    "CLAUDE_CODE_RETRY_WATCHDOG": "1",
    "CLAUDE_CODE_MAX_RETRIES": "300"
  }
}
```

### 4. Launch Claude Code
```bash
claude --dangerously-skip-permissions
```

## Requirements
- Python 3.8+ (standard library only, no pip install)
- Claude Code v2.1.186+ (for `CLAUDE_CODE_RETRY_WATCHDOG`)

## How it works

| Upstream code | Proxy action | Claude Code result |
|:--------------|:------------|:-------------------|
| 403 rate-limit | → 429 + `Retry-After: 20` | ✅ Auto-retry |
| 400 "plan limited" | → 429 + `Retry-After: 20` | ✅ Auto-retry |
| 504 gateway timeout | → 429 + `Retry-After: 20` | ✅ Auto-retry |
| 429 | → pass + `Retry-After: 15` | ✅ Auto-retry |
| 500/502/503/5xx | → pass + `Retry-After: 15` | ✅ Auto-retry |
| 520-527 (Cloudflare) | → pass + `Retry-After: 15` | ✅ Auto-retry |
| Network error | → 503 + `Retry-After: 10` | ✅ Auto-retry |
| 403 "no access to model" | → pass through | ❌ Fail fast (no loop) |
| 401 (auth) | → pass through | ❌ Fail fast |
| 404 (model) | → pass through | ❌ Fail fast |
| 200 + SSE | → stream direct | ✅ Normal response |

The proxy detects whether a 403/400 is a rate-limit by checking keywords in the body (English + Chinese). If the body doesn't contain rate-limit keywords, it passes through as-is (real error, not retryable).

## Circuit Breaker

The proxy distinguishes:
- **Rate-limit errors** (quota, capacity, saturated) → convert to 429 → Claude Code retries
- **Permanent errors** (model not accessible, invalid auth) → pass through → Claude Code fails immediately

This prevents the infinite retry loop that happens when Claude Code tries to compact context using a model the gateway doesn't serve (e.g. `claude-sonnet-5` when only `claude-opus-5` is available).

## License

MIT — use it, share it, modify it.
