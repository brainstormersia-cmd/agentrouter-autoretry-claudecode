The Problem
Claude Code auto-retries 429 and 5xx errors. But many gateways return 403 or 400 for rate limits, which Claude Code treats as permanent client errors → it stops → you must restart manually.

Some gateways return errors in Chinese (e.g. 用户额度不足 = "quota insufficient") or 504 gateway timeout from nginx, which Claude Code doesn't recognize as retryable.

The Solution
A local HTTP proxy (Python, zero dependencies) that:

Forwards all requests to the gateway (preserves auth + Claude Code headers)
Converts 403/400/504 rate-limits → 429 + Retry-After → Claude Code retries
Circuit breaker: permanent errors (model not accessible, invalid auth) → pass through without conversion → no infinite loops
SSE streaming passthrough (no buffering)
Network errors → 503 retryable
Setup (2 minutes)
1. Save the file
Copy retry-proxy.py to ~/.claude/retry-proxy.py (any folder works).

2. Start the proxy
# AgentRouter
python ~/.claude/retry-proxy.py --upstream https://agentrouter.org

# Lumosel
python ~/.claude/retry-proxy.py --upstream https://api.lumosel.vip

# Aerolink
python ~/.claude/retry-proxy.py --upstream https://capi.aerolink.lat
3. Configure Claude Code
In ~/.claude/settings.json:

{
  "env": {
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:8787",
    "ANTHROPIC_API_KEY": "your-key",
    "ANTHROPIC_MODEL": "claude-opus-5",
    "CLAUDE_CODE_RETRY_WATCHDOG": "1",
    "CLAUDE_CODE_MAX_RETRIES": "300"
  }
}
4. Launch Claude Code
claude --dangerously-skip-permissions
Requirements
Python 3.8+ (standard library only, no pip install)
Claude Code v2.1.186+ (for CLAUDE_CODE_RETRY_WATCHDOG)
How it works
Upstream code	Proxy action	Claude Code result
403 rate-limit	→ 429 + Retry-After: 20	✅ Auto-retry
400 "plan limited"	→ 429 + Retry-After: 20	✅ Auto-retry
504 gateway timeout	→ 429 + Retry-After: 20	✅ Auto-retry
429	→ pass + Retry-After: 15	✅ Auto-retry
500/502/503/5xx	→ pass + Retry-After: 15	✅ Auto-retry
520-527 (Cloudflare)	→ pass + Retry-After: 15	✅ Auto-retry
Network error	→ 503 + Retry-After: 10	✅ Auto-retry
403 "no access to model"	→ pass through	❌ Fail fast (no loop)
401 (auth)	→ pass through	❌ Fail fast
404 (model)	→ pass through	❌ Fail fast
200 + SSE	→ stream direct	✅ Normal response
The proxy detects whether a 403/400 is a rate-limit by checking keywords in the body (English + Chinese). If the body doesn't contain rate-limit keywords, it passes through as-is (real error, not retryable).

Circuit Breaker
The proxy distinguishes:

Rate-limit errors (quota, capacity, saturated) → convert to 429 → Claude Code retries
Permanent errors (model not accessible, invalid auth) → pass through → Claude Code fails immediately
This prevents the infinite retry loop that happens when Claude Code tries to compact context using a model the gateway doesn't serve (e.g. claude-sonnet-5 when only claude-opus-5 is available).

License
MIT — use it, share it, modify it.
