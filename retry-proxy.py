#!/usr/bin/env python3
"""
Claude Code Retry Proxy v1.0
=============================
Converts non-retryable gateway errors (403, 400, 504 rate-limits) into
retryable ones (429) so Claude Code's built-in watchdog auto-retries.

Works with ANY Anthropic-compatible gateway:
  - AgentRouter (https://agentrouter.org)
  - Lumosel     (https://api.lumosel.vip)
  - Aerolink    (https://capi.aerolink.lat)
  - Any custom gateway

NO API KEYS in this file. The proxy just forwards whatever Claude Code sends.
NO external dependencies. Python 3.8+ standard library only.

Usage:
  python retry-proxy.py --upstream https://agentrouter.org
  python retry-proxy.py --upstream https://api.lumosel.vip --port 8787

Then in ~/.claude/settings.json set:
  "ANTHROPIC_BASE_URL": "http://127.0.0.1:8787"
"""

import http.server
import http.client
import json
import sys
import socketserver
import ssl
import time
from urllib.parse import urlparse


class RetryProxyHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    UPSTREAM_HOST = "agentrouter.org"
    UPSTREAM_PORT = 443
    USE_TLS = True
    REQUEST_COUNT = 0
    CONVERT_COUNT = 0

    def do_GET(self):
        self.handle_proxy("GET")

    def do_POST(self):
        self.handle_proxy("POST")

    def do_HEAD(self):
        self.handle_proxy("HEAD")

    def do_PUT(self):
        self.handle_proxy("PUT")

    def do_DELETE(self):
        self.handle_proxy("DELETE")

    def handle_proxy(self, method):
        RetryProxyHandler.REQUEST_COUNT += 1
        req_id = RetryProxyHandler.REQUEST_COUNT

        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length) if content_length > 0 else None

        # Forward ALL headers from Claude Code (auth, session-id, beta, etc.)
        # Only strip hop-by-hop headers
        headers = {}
        for key, value in self.headers.items():
            if key.lower() not in ('host', 'content-length', 'connection',
                                   'transfer-encoding', 'accept-encoding'):
                headers[key] = value
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
            response = conn.getresponse()
            status = response.status
            resp_headers = response.getheaders()

            # Check for error status FIRST (before streaming)
            # Errors must be intercepted even if Content-Type is text/event-stream
            error_statuses = (400, 401, 403, 404, 408, 409, 413, 429,
                               500, 501, 502, 503, 504, 505, 510, 511,
                               520, 521, 522, 523, 524, 525, 526, 527, 529, 530)
            if status in error_statuses:
                resp_body = response.read()
                self._handle_error(status, resp_body, resp_headers, req_id)
                return

            # Successful response — stream or buffer
            is_stream = 'text/event-stream' in (response.getheader('Content-Type') or '')

            if is_stream:
                self._stream_response(response, status, resp_headers, req_id)
            else:
                resp_body = response.read()
                self._send_response(status, resp_body, resp_headers)

        except (http.client.HTTPException, ConnectionError, TimeoutError, OSError) as e:
            self._send_proxy_error(f"{type(e).__name__}: {e}", req_id)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _send_response(self, status, body, headers):
        """Send a normal response back to Claude Code."""
        self.send_response(status)
        for key, value in headers:
            if key.lower() not in ('transfer-encoding', 'connection',
                                   'content-length', 'content-encoding'):
                self.send_header(key, value)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(body)

    def _stream_response(self, response, status, resp_headers, req_id):
        """Stream SSE response directly without buffering."""
        self.send_response(status)
        for key, value in resp_headers:
            if key.lower() not in ('transfer-encoding', 'connection',
                                   'content-length', 'content-encoding'):
                self.send_header(key, value)
        self.send_header('Connection', 'close')
        self.end_headers()

        try:
            while True:
                chunk = response.read(4096)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as e:
            self.log_message(
                f"[{req_id}] STREAM ERROR: {type(e).__name__}: {e}")

    def _handle_error(self, status, resp_body, resp_headers, req_id):
        """Decide whether to convert an error to 429 (retry) or pass through."""
        body_text = resp_body.decode('utf-8', errors='replace').lower()

        # ── Circuit breaker ──
        # Permanent errors: retrying won't help (wrong model, bad auth)
        # Must NOT convert these to 429, or Claude Code loops forever
        permanent_keywords = [
            '无权访问模型',           # CN: no access to model
            'not authorized to access',
            'model not found',
            'invalid model',
            'unknown model',
            'model does not exist',
            'unauthorized',
            'invalid_api_key',
            'authentication',
        ]
        is_permanent = any(kw in body_text for kw in permanent_keywords)

        if is_permanent and status in (400, 401, 403, 404):
            self.log_message(
                f"[{req_id}] PASS-THROUGH {status} (permanent, no retry) | {body_text[:150]}")
            self._send_response(status, resp_body, resp_headers)
            return

        # ── Retryable errors ──
        # Rate limits, quota, capacity, server errors
        retry_keywords = [
            'rate', 'limit', 'plan', 'quota',
            'throttle', 'too many', 'overloaded', 'capacity',
            'timeout', 'timed out', 'unavailable', 'connection',
            'busy', 'retry', 'temporarily', 'maintenance',
            'degraded', 'restricted', 'insufficient', 'balance',
            'exceeded', 'exhausted', 'saturated',
            'gateway', 'downstream', 'upstream', 'origin',
            # Chinese (AgentRouter)
            '额度', '不足', '余额', '限制', '超额', '耗尽',
        ]
        is_retryable = (
            any(kw in body_text for kw in retry_keywords)
            or status in (429, 500, 502, 503, 504, 505, 510, 511,
                          520, 521, 522, 523, 524, 525, 526, 527, 529, 530)
        )
        if status == 504:
            is_retryable = True  # gateway timeout = always retry

        # Convert 403/400/504 rate-limits -> 429 so Claude Code retries
        if is_retryable and status in (400, 403, 408, 409, 413, 504):
            RetryProxyHandler.CONVERT_COUNT += 1
            self.log_message(
                f"[{req_id}] CONVERT {status} -> 429 | {body_text[:150]}")
            error_json = json.dumps({
                "type": "error",
                "error": {
                    "type": "rate_limit_error",
                    "message": "Request rate limited (converted by proxy). "
                               "Retrying automatically."
                }
            }).encode()
            self.send_response(429)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Retry-After', '20')
            self.send_header('Content-Length', str(len(error_json)))
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(error_json)
            return

        # Pass through 429/5xx with Retry-After added
        if is_retryable and status in (429, 500, 501, 502, 503, 505,
                                       510, 511, 520, 521, 522, 523,
                                       524, 525, 526, 527, 529, 530):
            self.log_message(
                f"[{req_id}] PASS-THROUGH {status} + Retry-After | {body_text[:100]}")
            self.send_response(status)
            for key, value in resp_headers:
                if key.lower() not in ('transfer-encoding', 'connection',
                                       'content-length', 'content-encoding'):
                    self.send_header(key, value)
            self.send_header('Retry-After', '15')
            self.send_header('Content-Length', str(len(resp_body)))
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(resp_body)
            return

        # Non-retryable: pass through as-is
        self.log_message(
            f"[{req_id}] PASS-THROUGH {status} (non-retryable) | {body_text[:100]}")
        self._send_response(status, resp_body, resp_headers)

    def _send_proxy_error(self, message, req_id):
        """Convert network errors to 503 so Claude Code retries."""
        self.log_message(
            f"[{req_id}] PROXY ERROR -> 503 | {message}")
        error_json = json.dumps({
            "type": "error",
            "error": {
                "type": "api_error",
                "message": f"Proxy connection error: {message}. Retrying."
            }
        }).encode()
        try:
            self.send_response(503)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Retry-After', '10')
            self.send_header('Content-Length', str(len(error_json)))
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(error_json)
        except Exception:
            pass

    def log_message(self, format, *args):
        msg = format % args
        sys.stderr.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        sys.stderr.flush()


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    host = "127.0.0.1"
    port = 8787
    upstream = "https://agentrouter.org"

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--port" and i + 1 < len(args):
            port = int(args[i + 1])
            i += 2
        elif args[i] == "--upstream" and i + 1 < len(args):
            upstream = args[i + 1]
            i += 2
        elif args[i] in ("-h", "--help"):
            print(__doc__)
            return
        else:
            i += 1

    parsed = urlparse(upstream)
    upstream_host = parsed.hostname or upstream
    upstream_port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    use_tls = parsed.scheme != 'http'

    RetryProxyHandler.UPSTREAM_HOST = upstream_host
    RetryProxyHandler.UPSTREAM_PORT = upstream_port
    RetryProxyHandler.USE_TLS = use_tls

    server = ThreadingHTTPServer((host, port), RetryProxyHandler)

    print(f"""
+--------------------------------------------------+
|  Claude Code Retry Proxy v1.0                    |
|  Listen:   http://{host}:{port}                    |
|  Upstream: {upstream:<40}|
|                                                  |
|  Conversions:                                    |
|    403/400/504 rate-limit -> 429 (retryable)     |
|    429/5xx server errors   -> + Retry-After       |
|    network errors          -> 503 (retryable)     |
|    SSE streaming           -> passthrough          |
|                                                  |
|  Circuit breaker:                                 |
|    permanent errors (model/auth) -> pass-through  |
|    (prevents infinite retry loops)               |
|                                                  |
|  Stats: req=0 converted=0                         |
+--------------------------------------------------+

Set in settings.json:
  "ANTHROPIC_BASE_URL": "http://{host}:{port}"

Press Ctrl+C to stop.
""".replace("|" + " " * 40 + "|", "| %-40s |" % upstream), flush=True)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(f"\nProxy stopped. "
              f"Total requests: {RetryProxyHandler.REQUEST_COUNT}, "
              f"Converted: {RetryProxyHandler.CONVERT_COUNT}")
        server.shutdown()


if __name__ == '__main__':
    main()
