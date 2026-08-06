#!/usr/bin/env python3
"""
ClaudeShield - Never let Claude Code crash on gateway rate limits again.

A lightweight local proxy that converts non-retryable gateway errors
(403, 400, 504 rate-limits) into retryable ones (429) so Claude Code's
built-in watchdog auto-retries through outages.

Usage:
  python retry-proxy.py                          # Start proxy (default: AgentRouter)
  python retry-proxy.py --upstream URL           # Use a different gateway
  python retry-proxy.py --install KEY            # Auto-setup everything
  python retry-proxy.py --port 9090              # Custom port
  python retry-proxy.py --help                   # Show help

Works with ANY Anthropic-compatible gateway:
  AgentRouter, Lumosel, Aerolink, Kilo Gateway, or your own.
"""

import http.server
import http.client
import json
import os
import sys
import socketserver
import ssl
import time
from urllib.parse import urlparse

VERSION = "1.1.0"
DEFAULT_PORT = 8787
DEFAULT_UPSTREAM = "https://agentrouter.org"

# ANSI colors (disabled on Windows if needed)
def c(text, color):
    if sys.platform == 'win32' and not os.environ.get('TERM'):
        return text
    colors = {
        'green': '\033[92m', 'red': '\033[91m', 'yellow': '\033[93m',
        'cyan': '\033[96m', 'bold': '\033[1m', 'dim': '\033[2m', 'reset': '\033[0m'
    }
    return f"{colors.get(color, '')}{text}{colors['reset']}"


class ClaudeShield:
    """Stats tracker."""
    requests = 0
    converted = 0
    passed = 0
    errors = 0


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    UPSTREAM_HOST = "agentrouter.org"
    UPSTREAM_PORT = 443
    USE_TLS = True

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_HEAD(self):
        self._handle("HEAD")

    def do_PUT(self):
        self._handle("PUT")

    def do_DELETE(self):
        self._handle("DELETE")

    def _handle(self, method):
        ClaudeShield.requests += 1
        rid = ClaudeShield.requests

        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length) if length > 0 else None

        headers = {}
        for k, v in self.headers.items():
            if k.lower() not in ('host', 'content-length', 'connection',
                                 'transfer-encoding', 'accept-encoding'):
                headers[k] = v
        headers['Accept-Encoding'] = 'identity'

        if self.USE_TLS:
            ctx = ssl.create_default_context()
            conn = http.client.HTTPSConnection(
                self.UPSTREAM_HOST, self.UPSTREAM_PORT, timeout=300, context=ctx)
        else:
            conn = http.client.HTTPConnection(
                self.UPSTREAM_HOST, self.UPSTREAM_PORT, timeout=300)

        try:
            conn.request(method, self.path, body=body, headers=headers)
            resp = conn.getresponse()
            status = resp.status
            rheaders = resp.getheaders()

            errors = (400, 401, 403, 404, 408, 409, 413, 429,
                      500, 501, 502, 503, 504, 505, 510, 511,
                      520, 521, 522, 523, 524, 525, 526, 527, 529, 530)
            if status in errors:
                rbody = resp.read()
                self._on_error(status, rbody, rheaders, rid)
                return

            is_stream = 'text/event-stream' in (resp.getheader('Content-Type') or '')
            if is_stream:
                self._stream(resp, status, rheaders)
            else:
                rbody = resp.read()
                self._send(status, rbody, rheaders)

        except (http.client.HTTPException, ConnectionError, TimeoutError, OSError) as e:
            ClaudeShield.errors += 1
            self._net_error(str(e), rid)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _send(self, status, body, headers):
        self.send_response(status)
        for k, v in headers:
            if k.lower() not in ('transfer-encoding', 'connection',
                                 'content-length', 'content-encoding'):
                self.send_header(k, v)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(body)

    def _stream(self, resp, status, headers):
        self.send_response(status)
        for k, v in headers:
            if k.lower() not in ('transfer-encoding', 'connection',
                                 'content-length', 'content-encoding'):
                self.send_header(k, v)
        self.send_header('Connection', 'close')
        self.end_headers()
        try:
            while True:
                chunk = resp.read(4096)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def _on_error(self, status, body, headers, rid):
        text = body.decode('utf-8', errors='replace').lower()

        # Permanent errors: retrying won't help (wrong model, bad auth)
        permanent = [
            '无权访问模型', 'not authorized to access', 'model not found',
            'invalid model', 'unknown model', 'model does not exist',
            'unauthorized', 'invalid_api_key', 'authentication',
        ]
        if any(kw in text for kw in permanent) and status in (400, 401, 403, 404):
            ClaudeShield.passed += 1
            self.log(f"[{rid}] {c('PASS', 'yellow')} {status} permanent | {text[:120]}")
            self._send(status, body, headers)
            return

        # Retryable: rate limit, quota, capacity, server errors
        retry_kw = [
            'rate', 'limit', 'quota', 'throttle', 'too many', 'overloaded',
            'capacity', 'timeout', 'timed out', 'unavailable', 'connection',
            'busy', 'retry', 'temporarily', 'maintenance', 'degraded',
            'restricted', 'insufficient', 'balance', 'exceeded', 'exhausted',
            'saturated', 'gateway', 'downstream', 'upstream', 'origin',
            '额度', '不足', '余额', '限制', '超额', '耗尽',
        ]
        is_retry = (
            any(kw in text for kw in retry_kw)
            or status in (429, 500, 502, 503, 504, 505, 510, 511,
                          520, 521, 522, 523, 524, 525, 526, 527, 529, 530)
        )
        if status == 504:
            is_retry = True

        # Convert 403/400/504 -> 429 so Claude Code retries
        if is_retry and status in (400, 403, 408, 409, 413, 504):
            ClaudeShield.converted += 1
            self.log(f"[{rid}] {c('CONVERT', 'cyan')} {status} -> 429 | {text[:120]}")
            err = json.dumps({"type": "error", "error": {
                "type": "rate_limit_error",
                "message": "Rate limited (converted by ClaudeShield). Retrying."
            }}).encode()
            self.send_response(429)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Retry-After', '20')
            self.send_header('Content-Length', str(len(err)))
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(err)
            return

        # Pass through 429/5xx with Retry-After
        if is_retry and status in (429, 500, 501, 502, 503, 505, 510, 511,
                                   520, 521, 522, 523, 524, 525, 526, 527, 529, 530):
            ClaudeShield.passed += 1
            self.log(f"[{rid}] {c('RETRY', 'yellow')} {status} + Retry-After | {text[:80]}")
            self.send_response(status)
            for k, v in headers:
                if k.lower() not in ('transfer-encoding', 'connection',
                                     'content-length', 'content-encoding'):
                    self.send_header(k, v)
            self.send_header('Retry-After', '15')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(body)
            return

        # Non-retryable: pass through
        ClaudeShield.passed += 1
        self.log(f"[{rid}] {c('PASS', 'dim')} {status} | {text[:80]}")
        self._send(status, body, headers)

    def _net_error(self, msg, rid):
        ClaudeShield.errors += 1
        self.log(f"[{rid}] {c('NETERR', 'red')} -> 503 | {msg[:80]}")
        err = json.dumps({"type": "error", "error": {
            "type": "api_error",
            "message": f"Proxy error: {msg}. Retrying."
        }}).encode()
        try:
            self.send_response(503)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Retry-After', '10')
            self.send_header('Content-Length', str(len(err)))
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(err)
        except Exception:
            pass

    def _send(self, status, body, headers):
        self.send_response(status)
        for k, v in headers:
            if k.lower() not in ('transfer-encoding', 'connection',
                                 'content-length', 'content-encoding'):
                self.send_header(k, v)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(body)

    def log(self, msg):
        sys.stderr.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        sys.stderr.flush()

    def log_message(self, *args):
        pass  # Suppress default access log (we use our own)


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def install(api_key, upstream, port):
    """Auto-configure everything: copy script + update settings.json."""
    home = os.path.expanduser("~")
    claude_dir = os.path.join(home, ".claude")
    settings_path = os.path.join(claude_dir, "settings.json")
    script_path = os.path.join(claude_dir, "retry-proxy.py")

    os.makedirs(claude_dir, exist_ok=True)

    # Copy this script to ~/.claude/
    current = os.path.abspath(__file__)
    if current != script_path:
        import shutil
        shutil.copy2(current, script_path)
        print(c(f"  [OK] Copied to {script_path}", 'green'))

    # Read or create settings.json
    settings = {}
    if os.path.exists(settings_path):
        try:
            with open(settings_path, 'r') as f:
                settings = json.load(f)
        except Exception:
            settings = {}

    # Update env
    env = settings.get('env', {})
    env['ANTHROPIC_BASE_URL'] = f'http://127.0.0.1:{port}'
    if api_key:
        env['ANTHROPIC_API_KEY'] = api_key
    env['CLAUDE_CODE_RETRY_WATCHDOG'] = '1'
    env['CLAUDE_CODE_MAX_RETRIES'] = '300'
    settings['env'] = env

    # Ensure permissions allow everything
    if 'permissions' not in settings:
        settings['permissions'] = {"allow": [], "deny": []}

    with open(settings_path, 'w') as f:
        json.dump(settings, f, indent=2)
    print(c(f"  [OK] Updated {settings_path}", 'green'))

    print(c(f"\n  [DONE] Setup complete!", 'green'))
    print(f"\n  Now start the proxy:")
    print(c(f"    python ~/.claude/retry-proxy.py --upstream {upstream} --port {port}", 'cyan'))
    print(f"\n  Then launch Claude Code:")
    print(c(f"    claude --dangerously-skip-permissions", 'cyan'))


def banner(upstream, host, port):
    print(f"""
{c('╔══════════════════════════════════════════════╗', 'cyan')}
{c('║', 'cyan')}  {c('ClaudeShield', 'bold')} v{VERSION}                    {c('║', 'cyan')}
{c('║', 'cyan')}  Never crash on rate limits again          {c('║', 'cyan')}
{c('╠══════════════════════════════════════════════╣', 'cyan')}
{c('║', 'cyan')}  Listen:   http://{host}:{port:<24}{c('║', 'cyan')}
{c('║', 'cyan')}  Upstream: {upstream:<34}{c('║', 'cyan')}
{c('╠══════════════════════════════════════════════╣', 'cyan')}
{c('║', 'cyan')}  403/400/504 rate-limit  -> 429 {c('(retry)', 'green')}  {c('║', 'cyan')}
{c('║', 'cyan')}  429/5xx server error    -> +Retry   {c('(retry)', 'green')}  {c('║', 'cyan')}
{c('║', 'cyan')}  network drop           -> 503      {c('(retry)', 'green')}  {c('║', 'cyan')}
{c('║', 'cyan')}  model/auth error       -> pass     {c('(fast)', 'yellow')}   {c('║', 'cyan')}
{c('║', 'cyan')}  SSE streaming          -> direct   {c('(live)', 'green')}   {c('║', 'cyan')}
{c('╚══════════════════════════════════════════════╝', 'cyan')}

  {c('Tip:', 'dim')} Set ANTHROPIC_BASE_URL to http://{host}:{port}
  {c('Ctrl+C to stop.', 'dim')}
""", flush=True)


def main():
    host = "127.0.0.1"
    port = DEFAULT_PORT
    upstream = DEFAULT_UPSTREAM
    do_install = False
    install_key = None

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-h", "--help"):
            print(__doc__)
            print(f"\nOptions:")
            print(f"  --upstream URL   Gateway to proxy to (default: {DEFAULT_UPSTREAM})")
            print(f"  --port PORT      Local port (default: {DEFAULT_PORT})")
            print(f"  --install KEY    Auto-setup: configure settings.json + copy script")
            print(f"  --version        Show version")
            return
        elif a == "--version":
            print(f"ClaudeShield v{VERSION}")
            return
        elif a == "--install":
            do_install = True
        elif a == "--upstream" and i + 1 < len(args):
            upstream = args[i + 1]
            i += 1
        elif a == "--port" and i + 1 < len(args):
            port = int(args[i + 1])
            i += 1
        elif a.startswith("--") and a != "--install":
            # --install can optionally take a key as next arg
            if do_install and not install_key and not a.startswith("--"):
                install_key = a
        elif do_install and not install_key:
            install_key = a
        i += 1

    if do_install:
        print(c("\n  ClaudeShield Auto-Setup\n", 'bold'))
        install(install_key, upstream, port)
        return

    parsed = urlparse(upstream)
    upstream_host = parsed.hostname or upstream
    upstream_port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    use_tls = parsed.scheme != 'http'

    ProxyHandler.UPSTREAM_HOST = upstream_host
    ProxyHandler.UPSTREAM_PORT = upstream_port
    ProxyHandler.USE_TLS = use_tls

    banner(upstream, host, port)

    server = Server((host, port), ProxyHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(f"\n{c('ClaudeShield stopped.', 'yellow')}")
        print(f"  Requests: {ClaudeShield.requests}")
        print(f"  Converted: {c(str(ClaudeShield.converted), 'cyan')}")
        print(f"  Passed: {ClaudeShield.passed}")
        print(f"  Errors: {ClaudeShield.errors}")
        server.shutdown()


if __name__ == '__main__':
    main()
