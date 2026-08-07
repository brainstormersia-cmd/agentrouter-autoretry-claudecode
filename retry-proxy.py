#!/usr/bin/env python3
"""
ClaudeShield - Never let Claude Code crash on gateway rate limits again.

Just run it. It will guide you through setup.

  python retry-proxy.py            # Interactive setup (recommended)
  python retry-proxy.py --start    # Skip setup, just run the proxy

Works with any Anthropic-compatible gateway:
  AgentRouter, Lumosel, Aerolink, or your own.
"""

import http.server
import http.client
import json
import os
import sys
import socketserver
import ssl
import threading
import time
from urllib.parse import urlparse

# Force UTF-8 on Windows to support Unicode box-drawing + emojis
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

    # Enable Windows Virtual Terminal Processing so ANSI escape codes work
    # even in legacy Windows PowerShell 5.1 (conhost). This is what rich/colorama do.
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32

        # STD_OUTPUT_HANDLE = -11, STD_ERROR_HANDLE = -12
        for handle_id in (-11, -12):
            handle = kernel32.GetStdHandle(handle_id)
            mode = ctypes.c_uint32()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
                kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass

VERSION = "3.3.0"
DEFAULT_PORT = 8787
DEFAULT_UPSTREAM = "https://agentrouter.org"
_start_time = time.time()


def _format_uptime(seconds):
    """Format uptime as human-readable string."""
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds/60)}m {int(seconds%60)}s"
    hours = int(seconds / 3600)
    minutes = int((seconds % 3600) / 60)
    return f"{hours}h {minutes}m"

# Known gateways
GATEWAYS = [
    ("AgentRouter", "https://agentrouter.org", "claude-opus-4-8"),
    ("Lumosel",     "https://api.lumosel.vip", "claude-opus-5"),
    ("Aerolink",    "https://capi.aerolink.lat", "claude-opus-4-8"),
]


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  RAINBOW ANIMATION ENGINE
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def _supports_color():
    """Detect if the terminal supports ANSI 256-color output.

    On Windows we enable Virtual Terminal Processing via the kernel32
    API at startup, so ANSI codes work even in PowerShell 5.1.
    """
    if sys.platform == 'win32':
        return True  # VT processing enabled at module load
    # Unix: check TERM
    return bool(os.environ.get('TERM'))

_NO_COLOR = not _supports_color()

# Fallback basic colors for terminals without 256-color support
BASIC_COLORS = {
    'red': '\033[91m', 'green': '\033[92m', 'yellow': '\033[93m',
    'cyan': '\033[96m', 'magenta': '\033[95m', 'blue': '\033[94m',
}

# 256-color rainbow palette
RAINBOW = [196, 202, 208, 214, 220, 226, 190, 154, 118, 82, 46, 47, 48, 49, 50, 51, 45, 39, 33, 27, 21, 57, 93, 129, 165, 201]


# Animation state
class AnimState:
    active = False        # True when proxy is converting/retrying
    last_action = ""      # "CONVERT", "RETRY", "NETERR", "PASS", "OK"
    last_status = 0
    spinner_frame = 0
    rainbow_offset = 0
    stop_flag = False
    line_len = 0

SHIELD_ART = [
    "  ###### ##   ## #######  ####### ####### #######  ##      ## #######",
    " ##          ## ##  ##   # ##     ##  ##  ##   ##  ##    ##  ##     ",
    " ##  #### #####    #####   #####  #####   #######   ##  ##   #####  ",
    " ##   ## ##  ##   ##      ##     ## ##    ##   ##    ####    ##     ",
    "  ###### ##  ##   ##       #######  ## ### ##   ##     ##     ###### ",
]

# RGB truecolor rainbow (more vivid than 256-color)
RAINBOW_RGB = [
    (255,0,0), (255,127,0), (255,255,0), (127,255,0),
    (0,255,0), (0,255,127), (0,255,255), (0,127,255),
    (0,0,255), (127,0,255), (255,0,255), (255,0,127),
]

def rgb(text, r, g, b):
    """Truecolor RGB ANSI escape."""
    if _NO_COLOR:
        return text
    return f"\033[38;2;{r};{g};{b}m{text}\033[0m"

def rainbow_text(text, offset=0):
    """Color text with cycling rainbow RGB colors."""
    if _NO_COLOR:
        return text
    result = []
    for i, ch in enumerate(text):
        if ch == ' ':
            result.append(ch)
        else:
            r, g, b = RAINBOW_RGB[(i + offset) % len(RAINBOW_RGB)]
            result.append(rgb(ch, r, g, b))
    return ''.join(result)

def c(text, color):
    if _NO_COLOR:
        return text
    colors = {'green':'\033[92m','red':'\033[91m','yellow':'\033[93m',
              'cyan':'\033[96m','bold':'\033[1m','dim':'\033[2m','reset':'\033[0m',
              'orange':'\033[38;2;255;165;0m'}
    return f"{colors.get(color,'')}{text}{colors['reset']}"

# Animation state
class AnimState:
    active = False
    last_action = ""
    last_status = 0
    spinner_frame = 0
    rainbow_offset = 0
    stop_flag = False
    line_len = 0

SHIELD_FRAMES = ['*']
SPINNER = ['|','/','-','\\']
BAR_FRAMES = [
    '..........',
    ':.........',
    '::........',
    ':::.......',
    '::::......',
    ':::::.....',
    '::::::....',
    ':::::::...',
    '::::::::..',
    ':::::::::.',
    '::::::::::',
    ':::::::::.',
    '::::::::..',
    ':::::::...',
    '::::::....',
    ':::::.....',
    '::::......',
    ':::.......',
    '::........',
    ':.........',
]


def animation_thread():
    """Background animation - rainbow spinner when proxy is active."""
    while not AnimState.stop_flag:
        if AnimState.active:
            frame = SPINNER[AnimState.spinner_frame % len(SPINNER)]
            bar = BAR_FRAMES[AnimState.spinner_frame % len(BAR_FRAMES)]
            offset = AnimState.rainbow_offset

            # Spinner frame in cycling rainbow color
            sr, sg, sb = RAINBOW_RGB[offset % len(RAINBOW_RGB)]
            spinner_str = rgb(frame, sr, sg, sb)

            # CLAUDE (orange) + SHIELD (rainbow)
            claude_str = c("CLAUDE", "orange")
            shield_str = rainbow_text("SHIELD", offset)

            # Action label
            action_colors = {
                'CONVERT': (0, 255, 255),   # cyan
                'RETRY':   (255, 255, 0),   # yellow
                'NETERR':  (255, 0, 0),     # red
                'PASS':    (128, 128, 128), # gray
                'OK':      (0, 255, 0),     # green
            }
            ar, ag, ab = action_colors.get(AnimState.last_action, (0, 255, 0))
            action_str = rgb(f"[{AnimState.last_action}]", ar, ag, ab)
            status = AnimState.last_status

            # Bar in rainbow
            bar_str = rainbow_text(bar, offset + 3)

            status_text = f" {spinner_str} {claude_str}{shield_str} {action_str} "
            if status:
                status_text += rgb(str(status), 255, 255, 255)
                status_text += " "
            status_text += f"{bar_str} {c('protecting...', 'dim')}"

            # Overwrite line in place
            pad = max(0, AnimState.line_len - len(status_text))
            sys.stderr.write(f"\r\033[K{status_text}{' ' * pad}")
            sys.stderr.flush()
            AnimState.line_len = len(status_text)

            AnimState.spinner_frame += 1
            AnimState.rainbow_offset += 1
            time.sleep(0.1)
        else:
            time.sleep(0.3)

    if AnimState.line_len > 0:
        sys.stderr.write(f"\r\033[K")
        sys.stderr.flush()


def trigger_animation(action, status=0):
    AnimState.active = True
    AnimState.last_action = action
    AnimState.last_status = status

def stop_animation():
    AnimState.active = False
    if AnimState.line_len > 0:
        sys.stderr.write(f"\r\033[K")
        sys.stderr.flush()
        AnimState.line_len = 0


def orange_text(text):
    if _NO_COLOR:
        return text
    return f"\033[38;2;255;165;0m{text}\033[0m"

def dim_text(text):
    if _NO_COLOR:
        return text
    return f"\033[2m{text}\033[0m"

def show_banner(upstream, host, port):
    """Show CLAUDE (orange) + SHIELD (rainbow) ASCII art banner."""
    split_pos = 30
    for line in SHIELD_ART:
        if line.strip():
            claude_part = line[:split_pos]
            shield_part = line[split_pos:]
            offset = int(time.time() * 8) % len(RAINBOW_RGB)
            print(f"  {orange_text(claude_part)}{rainbow_text(shield_part, offset)}")

    print()
    print(f"  {dim_text('v' + VERSION)}  {c('|', 'dim')}  {c('listen', 'dim')} {c(f'http://{host}:{port}', 'bold')}"
          f"  {c('|', 'dim')}  {c('upstream', 'dim')} {c(upstream, 'cyan')}")
    print()
    print(f"  {c('[*]', 'green')}  {c('Shield active', 'green')}  {c('Claude Code will auto-retry through outages', 'dim')}")
    print(f"  {c('[?]', 'dim')}  {c('Stats: http://{host}:{port}/stats', 'dim')}")
    print(f"  {c('[!]', 'yellow')}  {c('Ctrl+C to stop', 'dim')}")
    print()
    print(f"  {rainbow_text('* * * * * * * * * *', offset=0)}")
    print()
    print(f"  {rainbow_text('* * * * * * * * * *', offset=0)}")
    print()



# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  PROXY
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

class Stats:
    requests = 0
    converted = 0
    passed = 0
    errors = 0

class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    UPSTREAM_HOST = "agentrouter.org"
    UPSTREAM_PORT = 443
    USE_TLS = True

    def do_GET(self):
        # Built-in stats endpoint for status page
        if self.path == '/stats' or self.path == '/stats/':
            self._send_stats()
            return
        if self.path == '/health' or self.path == '/health/':
            self._send_health()
            return
        self._handle("GET")

    def _send_stats(self):
        """Expose proxy stats as JSON for the status page."""
        uptime = time.time() - _start_time
        stats_json = json.dumps({
            "version": VERSION,
            "upstream": f"https://{Handler.UPSTREAM_HOST}",
            "uptime_seconds": round(uptime, 1),
            "uptime_human": _format_uptime(uptime),
            "requests": Stats.requests,
            "converted": Stats.converted,
            "passed": Stats.passed,
            "errors": Stats.errors,
            "retry_rate": round(Stats.converted / max(1, Stats.requests), 4),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', str(len(stats_json)))
        self.end_headers()
        self.wfile.write(stats_json)

    def _send_health(self):
        """Simple health check endpoint."""
        health = json.dumps({"status": "ok", "version": VERSION}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', str(len(health)))
        self.end_headers()
        self.wfile.write(health)

    def do_POST(self):  self._handle("POST")
    def do_HEAD(self):  self._handle("HEAD")
    def do_PUT(self):    self._handle("PUT")
    def do_DELETE(self): self._handle("DELETE")

    def _handle(self, method):
        Stats.requests += 1
        rid = Stats.requests
        length = int(self.headers.get('Content-Length', 0))
        raw_body = self.rfile.read(length) if length > 0 else None

        # Inject Claude Code CLI User-Agent - AgentRouter rejects other clients
        headers = {}
        for k, v in self.headers.items():
            if k.lower() not in ('host','content-length','connection',
                                 'transfer-encoding','accept-encoding',
                                 'user-agent'):
                headers[k] = v
        headers['Accept-Encoding'] = 'identity'

        # Pick the right User-Agent: real Claude Code UA if present, else inject
        orig_ua = (self.headers.get('User-Agent') or '').strip()
        if orig_ua.lower().startswith(('claude-cli/', 'claude-code/')):
            headers['User-Agent'] = orig_ua
        else:
            headers['User-Agent'] = 'claude-cli/1.0.0 (external, cli)'

        # Force stream=true and parse body for payload sanitization
        body = raw_body
        is_streamed = False
        wants_stream = False
        is_messages_route = self.path.startswith('/v1/messages')
        if raw_body and method == 'POST':
            try:
                text = raw_body.decode('utf-8', errors='replace')
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    is_streamed = parsed.get('stream', False) is True
                    wants_stream = is_streamed  # what the CLIENT asked for
                    parsed['stream'] = True  # always force stream upstream
                    # Remove params some gateways reject
                    parsed.pop('temperature', None)
                    parsed.pop('top_p', None)
                    body = json.dumps(parsed).encode()
                    headers['Content-Length'] = str(len(body))
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass

        if self.USE_TLS:
            ctx = ssl.create_default_context()
            conn = http.client.HTTPSConnection(self.UPSTREAM_HOST, self.UPSTREAM_PORT,
                                               timeout=60, context=ctx)
        else:
            conn = http.client.HTTPConnection(self.UPSTREAM_HOST, self.UPSTREAM_PORT,
                                              timeout=60)
        try:
            conn.request(method, self.path, body=body, headers=headers)
            resp = conn.getresponse()
            status = resp.status
            rheaders = resp.getheaders()

            errors = (400,401,403,404,408,409,413,429,500,501,502,503,504,505,
                      510,511,520,521,522,523,524,525,526,527,529,530)
            if status in errors:
                rbody = resp.read()
                self._on_error(status, rbody, rheaders, rid)
                return

            content_type = (resp.getheader('Content-Type') or '').lower()
            is_stream = 'text/event-stream' in content_type or is_streamed
            if is_stream:
                if wants_stream:
                    self._stream_with_peek(resp, status, rheaders, rid, is_streamed)
                else:
                    # Client asked for stream:false but upstream (forced) streams.
                    # Fold the SSE back into a single JSON response - otherwise
                    # the client sees SSE where it expects JSON and reports
                    # "empty or malformed response (HTTP 200)".
                    self._collect_json(resp, status, rheaders, rid, is_messages_route)
            else:
                rbody = resp.read()
                # Catch empty/malformed 200 responses
                if status == 200:
                    stripped = rbody.strip()
                    if len(stripped) == 0:
                        Stats.errors += 1
                        self.log(f"[{rid}] {c('EMPTY200','red')} 200 empty -> 503")
                        trigger_animation('NETERR', 503)
                        self._send_503("Gateway returned empty response. Retrying.")
                        return
                    if stripped[:1] not in (b'{', b'['):
                        Stats.errors += 1
                        self.log(f"[{rid}] {c('MALFORMED','red')} 200 non-JSON -> 503 | {stripped[:80]!r}")
                        trigger_animation('NETERR', 503)
                        self._send_503("Gateway returned non-JSON response. Retrying.")
                        return
                    # JSON body that starts with { but IS an error payload.
                    # Anthropic error format: {"type":"error","error":{...}}
                    # Claude Code expects a message response on 200 - an error
                    # body means the gateway lied about the status.
                    if stripped[:1] == b'{':
                        body_lower = stripped[:512].lower()
                        if b'"type":"error"' in body_lower or b'"type": "error"' in body_lower:
                            Stats.errors += 1
                            self.log(f"[{rid}] {c('JSON_ERR','red')} 200 JSON-error body -> 503 | {stripped[:100]!r}")
                            trigger_animation('NETERR', 503)
                            self._send_503("Gateway returned error body with 200 status. Retrying.")
                            return
                self._send(status, rbody, rheaders)
        except (http.client.HTTPException, ConnectionError, TimeoutError, OSError) as e:
            Stats.errors += 1
            self._net_error(str(e), rid)
        finally:
            try: conn.close()
            except: pass

    def _send_503(self, message):
        """Send a 503 retryable response for empty/malformed 200s."""
        err = json.dumps({"type":"error","error":{"type":"api_error",
            "message":message}}).encode()
        self.send_response(503)
        self.send_header('Content-Type','application/json')
        self.send_header('Retry-After','10')
        self.send_header('Content-Length',str(len(err)))
        self.send_header('Connection','close')
        self.end_headers()
        self.wfile.write(err)

    def _send(self, status, body, headers):
        self.send_response(status)
        for k, v in headers:
            if k.lower() not in ('transfer-encoding','connection',
                                 'content-length','content-encoding'):
                self.send_header(k, v)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(body)

    def _stream_with_peek(self, resp, status, headers, rid, was_streamed):
        """Stream SSE response, but peek at the first COMPLETE events to detect
        errors hidden inside a 200 status.

        Like arproxy: accumulate until a full SSE event boundary (\\n\\n)
        before deciding. A partial chunk must never trigger a false abort.

        Rules (mirroring arproxy):
        - error event (parsed JSON type:error or has error field) -> 503 retry
        - no message_start in first events -> 503 retry
        - otherwise stream through, filtering data: null
        """
        # Peek: accumulate bytes until at least one complete SSE event
        buffer = b''
        try:
            while len(buffer) < 65536:  # safety cap
                chunk = resp.read(4096)
                if not chunk:
                    break
                buffer += chunk
                # Stop when we have at least one full event (double newline)
                if b'\n\n' in buffer:
                    break
        except Exception:
            pass

        text = buffer.decode('utf-8', errors='replace')

        # Real error payload in the stream head?
        if self._sse_contains_error(text):
            Stats.errors += 1
            self.log(f"[{rid}] {c('SSE_ERR','red')} 200 stream has error -> 503")
            trigger_animation('NETERR', 503)
            self._send_503("Gateway returned error in stream. Retrying.")
            return

        # No message_start in the first complete events -> malformed stream
        if was_streamed and 'message_start' not in text and text.strip():
            Stats.errors += 1
            self.log(f"[{rid}] {c('SSE_NO_START','red')} 200 no message_start -> 503")
            trigger_animation('NETERR', 503)
            self._send_503("Stream ended before message_start. Retrying.")
            return

        # Stream is good - send headers then buffered data + rest
        self.send_response(status)
        for k, v in headers:
            if k.lower() not in ('transfer-encoding','connection',
                                 'content-length','content-encoding'):
                self.send_header(k, v)
        self.send_header('Connection', 'close')
        self.end_headers()

        try:
            # Send buffered data first (filtering data: null)
            if buffer:
                self.wfile.write(self._filter_null_events(buffer))
                self.wfile.flush()
            # Then stream the rest - NO mid-stream error killing.
            # A model may legitimately mention error words in its output;
            # only event-level filtering happens below.
            while True:
                chunk = resp.read(4096)
                if not chunk:
                    break
                self.wfile.write(self._filter_null_events(chunk))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def _collect_json(self, resp, status, headers, rid, is_messages_route):
        """Fold a forced-stream SSE response back into a single JSON body.

        Called when the CLIENT asked for stream:false but the upstream was
        forced to stream. Returning SSE to a JSON-expecting client causes
        'empty or malformed response (HTTP 200)'.

        For /v1/messages: rebuild the Anthropic message object.
        For /v1/chat/completions: rebuild the chat.completion object.
        """
        try:
            raw = resp.read()
        except Exception as e:
            Stats.errors += 1
            self.log(f"[{rid}] {c('COLLECT_ERR','red')} read failed: {e}")
            self._send_503("Gateway read error. Retrying.")
            return

        text = raw.decode('utf-8', errors='replace')
        if not text.strip():
            Stats.errors += 1
            self.log(f"[{rid}] {c('EMPTY200','red')} 200 empty body -> 503")
            trigger_animation('NETERR', 503)
            self._send_503("Gateway returned empty response. Retrying.")
            return

        if self._sse_contains_error(text):
            Stats.errors += 1
            self.log(f"[{rid}] {c('SSE_ERR','red')} stream has error -> 503")
            trigger_animation('NETERR', 503)
            self._send_503("Gateway returned error in stream. Retrying.")
            return

        # If it's not actually SSE (gateway ignored stream:true), pass raw JSON
        if text.strip()[:1] in ('{', '['):
            self._send(status, raw, headers)
            return

        events = self._parse_sse_events(text)
        if not events:
            Stats.errors += 1
            # Debug: show what the gateway actually returned
            self.log(f"[{rid}] {c('SSE_NO_START','red')} no parseable events -> 503 | body: {text[:200]!r}")
            trigger_animation('NETERR', 503)
            self._send_503("Stream had no parseable events. Retrying.")
            return

        if is_messages_route:
            out = self._fold_anthropic_message(events)
        else:
            out = self._fold_openai_completion(events)

        if out is None:
            Stats.errors += 1
            self.log(f"[{rid}] {c('FOLD_ERR','red')} could not fold stream -> 503")
            trigger_animation('NETERR', 503)
            self._send_503("Could not reconstruct response. Retrying.")
            return

        body = json.dumps(out).encode()
        # Send as JSON with the upstream status
        self.send_response(status)
        for k, v in headers:
            if k.lower() not in ('transfer-encoding','connection',
                                 'content-length','content-encoding'):
                self.send_header(k, v)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def _parse_sse_events(text: str) -> list[dict]:
        """Extract JSON objects from SSE data: lines."""
        events = []
        for line in text.split('\n'):
            line = line.strip()
            if not line.startswith('data:'):
                continue
            payload = line[5:].strip()
            if not payload or payload == '[DONE]':
                continue
            try:
                obj = json.loads(payload)
                if isinstance(obj, dict):
                    events.append(obj)
            except (json.JSONDecodeError, TypeError):
                continue
        return events

    def _fold_anthropic_message(self, events: list[dict]):
        """Rebuild a single Anthropic message object from SSE events."""
        message = None
        blocks: dict[int, dict] = {}
        stop_reason = None
        stop_sequence = None
        usage = None

        for ev in events:
            etype = ev.get('type')
            if etype == 'message_start':
                message = dict(ev.get('message') or {})
                usage = dict(ev.get('message', {}).get('usage') or {})
            elif etype == 'content_block_start':
                idx = ev.get('index', len(blocks))
                block = dict(ev.get('content_block') or {})
                if block.get('type') == 'tool_use' and 'input' not in block:
                    block['input'] = ''
                blocks[idx] = block
            elif etype == 'content_block_delta':
                idx = ev.get('index', 0)
                block = blocks.setdefault(idx, {'type': 'text', 'text': ''})
                delta = ev.get('delta') or {}
                if isinstance(delta.get('text'), str):
                    block['text'] = block.get('text', '') + delta['text']
                if isinstance(delta.get('partial_json'), str):
                    block['input'] = block.get('input', '') + delta['partial_json']
                if isinstance(delta.get('thinking'), str):
                    block['thinking'] = block.get('thinking', '') + delta['thinking']
                if isinstance(delta.get('signature'), str):
                    block['signature'] = delta['signature']
            elif etype == 'message_delta':
                if ev.get('delta'):
                    if ev['delta'].get('stop_reason') is not None:
                        stop_reason = ev['delta']['stop_reason']
                    if ev['delta'].get('stop_sequence') is not None:
                        stop_sequence = ev['delta']['stop_sequence']
                if ev.get('usage'):
                    usage = {**(usage or {}), **ev['usage']}
            elif etype == 'error':
                return None

        if not message:
            return None

        content = []
        for idx in sorted(blocks):
            block = blocks[idx]
            if block.get('type') == 'tool_use' and isinstance(block.get('input'), str):
                try:
                    block['input'] = json.loads(block['input']) if block['input'] else {}
                except json.JSONDecodeError:
                    block['input'] = {}
            content.append(block)

        message['type'] = 'message'
        message.setdefault('role', 'assistant')
        message['content'] = content
        message['stop_reason'] = stop_reason or message.get('stop_reason') or 'end_turn'
        message['stop_sequence'] = stop_sequence if stop_sequence is not None else message.get('stop_sequence')
        if usage:
            message['usage'] = usage
        return message

    def _fold_openai_completion(self, events: list[dict]):
        """Rebuild a single chat.completion object from SSE events."""
        accum_text = ''
        calls: dict[int, dict] = {}
        stop_reason = 'stop'
        seq_id = 'chatcmpl-proxy'
        model = ''

        for ev in events:
            if ev.get('id'):
                seq_id = ev['id']
            if ev.get('model'):
                model = ev['model']
            if ev.get('type') == 'content_block_delta' and ev.get('delta', {}).get('text'):
                accum_text += ev['delta']['text']
            for choice in ev.get('choices') or []:
                if choice.get('finish_reason'):
                    stop_reason = choice['finish_reason']
                delta = choice.get('delta') or {}
                if delta.get('content'):
                    accum_text += delta['content']
                for tc in delta.get('tool_calls') or []:
                    idx = tc.get('index', 0)
                    call = calls.setdefault(idx, {'id': '', 'type': 'function', 'function': {'name': '', 'arguments': ''}})
                    if tc.get('id'):
                        call['id'] = tc['id']
                    if tc.get('function', {}).get('name'):
                        call['function']['name'] += tc['function']['name']
                    if tc.get('function', {}).get('arguments'):
                        call['function']['arguments'] += tc['function']['arguments']

        msg = {'role': 'assistant'}
        if accum_text or not calls:
            msg['content'] = accum_text
        if calls:
            msg['tool_calls'] = list(calls.values())

        return {
            'id': seq_id,
            'object': 'chat.completion',
            'created': int(time.time()),
            'model': model,
            'choices': [{'index': 0, 'message': msg, 'finish_reason': stop_reason}],
        }

    @staticmethod
    def _sse_contains_error(text: str) -> bool:
        """Detect if an SSE stream chunk contains an error payload.

        Parses data: lines as JSON - only REAL error payloads match.
        Never matches model content that merely mentions words like
        'rate_limit' (the false-positive bug in earlier versions).
        """
        if not text:
            return False

        # Check every data: line by parsing it as JSON (like arproxy)
        for line in text.split('\n'):
            line = line.strip()
            if not line.startswith('data:'):
                continue
            payload = line[5:].strip()
            if not payload or payload == '[DONE]':
                continue
            try:
                obj = json.loads(payload)
                if not isinstance(obj, dict):
                    continue
                # Real error: type == "error", OR an error field that is
                # actually populated. Legitimate events carry "error": null,
                # so a bare 'error' key must NOT count as an error.
                if obj.get('type') == 'error':
                    return True
                err_val = obj.get('error')
                if err_val is not None and err_val != {} and err_val != []:
                    return True
            except (json.JSONDecodeError, TypeError):
                continue
        return False

    @staticmethod
    def _filter_null_events(data: bytes) -> bytes:
        """Remove 'data: null' SSE events that break Anthropic parsers."""
        text = data.decode('utf-8', errors='replace')
        lines = text.split('\n')
        filtered = []
        skip_next_blank = False
        for line in lines:
            stripped = line.strip()
            if stripped == 'data: null' or stripped == 'data:null':
                skip_next_blank = True
                continue
            if skip_next_blank and stripped == '':
                skip_next_blank = False
                continue
            skip_next_blank = False
            filtered.append(line)
        return '\n'.join(filtered).encode()

    def _stream(self, resp, status, headers):
        """Legacy stream passthrough (kept for non-Anthropic endpoints)."""
        self.send_response(status)
        for k, v in headers:
            if k.lower() not in ('transfer-encoding','connection',
                                 'content-length','content-encoding'):
                self.send_header(k, v)
        self.send_header('Connection', 'close')
        self.end_headers()
        try:
            while True:
                chunk = resp.read(4096)
                if not chunk: break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def _on_error(self, status, body, headers, rid):
        text = body.decode('utf-8', errors='replace').lower()

        # Permanent errors: don't retry (prevents infinite loops)
        permanent = ['æ— æƒè®¿é—®æ¨¡åž‹','not authorized to access','model not found',
                     'invalid model','unknown model','model does not exist',
                     'unauthorized','invalid_api_key','authentication']
        if any(kw in text for kw in permanent) and status in (400,401,403,404):
            Stats.passed += 1
            self.log(f"[{rid}] {c('PASS','yellow')} {status} permanent | {text[:100]}")
            self._send(status, body, headers)
            return

        # Retryable: rate limit, quota, capacity, server errors
        retry_kw = ['rate','limit','quota','throttle','too many','overloaded',
                    'capacity','timeout','timed out','unavailable','connection',
                    'busy','retry','temporarily','maintenance','degraded',
                    'restricted','insufficient','balance','exceeded','exhausted',
                    'saturated','gateway','downstream','upstream','origin',
                    'é¢åº¦','ä¸è¶³','ä½™é¢','é™åˆ¶','è¶…é¢','è€—å°½']
        is_retry = (any(kw in text for kw in retry_kw) or
                    status in (429,500,502,503,504,505,510,511,
                               520,521,522,523,524,525,526,527,529,530))
        if status == 504: is_retry = True

        # Convert 403/400/504 -> 429
        if is_retry and status in (400,403,408,409,413,504):
            Stats.converted += 1
            self.log(f"[{rid}] {c('CONVERT','cyan')} {status} -> 429 | {text[:100]}")
            trigger_animation('CONVERT', status)
            # Friendly random messages - shown to the user in their client
            shield_messages = [
                "ClaudeShield is holding the line. Retrying...",
                "Shield up! Absorbing the hit, retrying...",
                "Rate limit deflected. ClaudeShield has your back.",
                "Quota error neutralized. Standing by for retry.",
                "ClaudeShield caught that. Retrying automatically.",
                "No worries - ClaudeShield is handling it. Retrying...",
                "Shield active. Bouncing back from rate limit.",
                "ClaudeShield: rate limit intercepted, retrying now.",
            ]
            msg = shield_messages[Stats.converted % len(shield_messages)]
            err = json.dumps({"type":"error","error":{"type":"rate_limit_error",
                "message":msg}}).encode()
            self.send_response(429)
            self.send_header('Content-Type','application/json')
            self.send_header('Retry-After','20')
            self.send_header('Content-Length',str(len(err)))
            self.send_header('Connection','close')
            self.end_headers()
            self.wfile.write(err)
            return

        # Pass 429/5xx with Retry-After
        if is_retry and status in (429,500,501,502,503,505,510,511,
                                   520,521,522,523,524,525,526,527,529,530):
            Stats.passed += 1
            self.log(f"[{rid}] {c('RETRY','yellow')} {status} | {text[:60]}")
            trigger_animation('RETRY', status)
            self.send_response(status)
            for k, v in headers:
                if k.lower() not in ('transfer-encoding','connection',
                                     'content-length','content-encoding'):
                    self.send_header(k, v)
            self.send_header('Retry-After','15')
            self.send_header('Content-Length',str(len(body)))
            self.send_header('Connection','close')
            self.end_headers()
            self.wfile.write(body)
            return

        # Non-retryable
        Stats.passed += 1
        self.log(f"[{rid}] {c('PASS','dim')} {status} | {text[:60]}")
        self._send(status, body, headers)

    def _net_error(self, msg, rid):
        Stats.errors += 1
        self.log(f"[{rid}] {c('NETERR','red')} -> 503 | {msg[:60]}")
        trigger_animation('NETERR', 503)
        err = json.dumps({"type":"error","error":{"type":"api_error",
            "message":f"Proxy error: {msg}. Retrying."}}).encode()
        try:
            self.send_response(503)
            self.send_header('Content-Type','application/json')
            self.send_header('Retry-After','10')
            self.send_header('Content-Length',str(len(err)))
            self.send_header('Connection','close')
            self.end_headers()
            self.wfile.write(err)
        except: pass

    def log(self, msg):
        sys.stderr.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        sys.stderr.flush()

    def log_message(self, *a): pass

class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  INTERACTIVE SETUP
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def read_settings():
    """Read current settings.json and return (settings_dict, upstream, has_key, model)."""
    home = os.path.expanduser("~")
    settings_path = os.path.join(home, ".claude", "settings.json")
    settings = {}
    upstream = DEFAULT_UPSTREAM
    has_key = False
    model = "claude-opus-5"

    if os.path.exists(settings_path):
        try:
            with open(settings_path) as f:
                settings = json.load(f)
            env = settings.get("env", {})
            base_url = env.get("ANTHROPIC_BASE_URL", "")
            # Detect upstream from base_url if it's not localhost
            if base_url and "127.0.0.1" not in base_url and "localhost" not in base_url:
                upstream = base_url.rstrip("/")
            key = env.get("ANTHROPIC_API_KEY") or env.get("ANTHROPIC_AUTH_TOKEN") or ""
            has_key = bool(key)
            model = env.get("ANTHROPIC_MODEL", model)
        except Exception:
            pass
    return settings, upstream, has_key, model, settings_path


def interactive_setup():
    """Main menu - reads existing config and offers clear options."""
    settings, upstream, has_key, model, settings_path = read_settings()

    # Detect gateway name from upstream
    gw_name = "Custom"
    for name, url, _ in GATEWAYS:
        if url in upstream:
            gw_name = name
            break

    print(f"\n  {c('ClaudeShield', 'bold')} v{VERSION}\n")
    print(f"  {c('Current configuration:', 'dim')}")
    print(f"    Gateway:  {c(upstream, 'cyan')}")
    print(f"    Model:    {c(model, 'cyan')}")
    print(f"    API key:  {c('configured' if has_key else c('NOT SET', 'red'), 'green' if has_key else 'dim')}")
    print(f"    Port:     {c(str(DEFAULT_PORT), 'cyan')}")
    print()

    print(f"  {c('What do you want to do?', 'bold')}\n")
    print(f"    {c('1', 'cyan')}. Start proxy now")
    print(f"    {c('2', 'cyan')}. Change gateway / API key / model")
    print(f"    {c('3', 'cyan')}. Update ClaudeShield to latest version")
    print(f"    {c('4', 'cyan')}. Show current settings.json")
    print(f"    {c('5', 'cyan')}. Quit")
    print()

    choice = input(f"  {c('Choice [1-5]', 'bold')} (default 1): ").strip() or "1"

    if choice == "1":
        return upstream
    elif choice == "2":
        return configure_gateway(settings_path)
    elif choice == "3":
        sys.exit(self_update())
    elif choice == "4":
        show_settings(settings_path)
        return None  # don't start
    else:
        sys.exit(0)


def configure_gateway(settings_path):
    """Change gateway, API key, and model."""
    print(f"\n  {c('Configuration', 'bold')}\n")
    print("  Which gateway are you using?")
    for i, (name, url, _) in enumerate(GATEWAYS, 1):
        print(f"    {c(str(i), 'cyan')}. {name} ({url})")
    print(f"    {c(str(len(GATEWAYS)+1), 'cyan')}. Custom URL\n")

    while True:
        choice = input(f"  {c('>', 'bold')} ").strip()
        try:
            n = int(choice)
            if 1 <= n <= len(GATEWAYS):
                gw_name, upstream, default_model = GATEWAYS[n-1]
                break
            elif n == len(GATEWAYS)+1:
                upstream = input("  Enter gateway URL (https://...): ").strip()
                if not upstream.startswith("http"):
                    upstream = "https://" + upstream
                gw_name = "Custom"
                default_model = "claude-opus-5"
                break
        except ValueError:
            pass
        print(f"  {c('Please enter a number.', 'red')}")

    # API key
    _, _, existing_key, _, _ = read_settings()
    print(f"\n  Enter your {gw_name} API key")
    if existing_key:
        masked = existing_key[:8] + "..." + existing_key[-4:]
        print(f"  {c('(found existing: ' + masked + ' - press Enter to keep)', 'dim')}")
    api_key = input(f"  {c('>', 'bold')} ").strip()
    if not api_key and existing_key:
        api_key = existing_key
        print(f"  {c('Using existing key.', 'dim')}")
    elif not api_key:
        print(f"  {c('No key entered. You can add it later in settings.json', 'yellow')}")

    # Model
    print(f"\n  Which model? {c('(press Enter for ' + default_model + ')', 'dim')}")
    model = input(f"  {c('>', 'bold')} ").strip() or default_model

    # Save
    print(f"\n  {c('Saving...', 'cyan')}")
    settings = {}
    if os.path.exists(settings_path):
        try:
            with open(settings_path) as f:
                settings = json.load(f)
        except:
            pass

    env = settings.get("env", {})
    env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{DEFAULT_PORT}"
    if api_key:
        env["ANTHROPIC_API_KEY"] = api_key
    env["ANTHROPIC_MODEL"] = model
    env["CLAUDE_CODE_RETRY_WATCHDOG"] = "1"
    env["CLAUDE_CODE_MAX_RETRIES"] = "300"
    settings["env"] = env

    if "permissions" not in settings:
        settings["permissions"] = {"allow": [], "deny": []}

    with open(settings_path, "w") as f:
        json.dump(settings, f, indent=2)
    print(f"  {c('[OK]', 'green')} Saved to {settings_path}")
    print(f"\n  {c('Starting proxy...', 'green')}\n")
    return upstream


def show_settings(settings_path):
    """Display current settings.json."""
    print(f"\n  {c('Current settings.json:', 'bold')}")
    print(f"  {c('-' * 50, 'dim')}")
    if os.path.exists(settings_path):
        try:
            with open(settings_path) as f:
                content = f.read()
            # Mask API key for display
            import re
            masked = re.sub(
                r'("(?:ANTHROPIC_API_KEY|ANTHROPIC_AUTH_TOKEN)"\s*:\s*")([^"]{8})[^"]*([^"]{4})(")',
                r'\1\2...\4', content
            )
            print(masked)
        except Exception as e:
            print(f"  Error reading: {e}")
    else:
        print(f"  {c('No settings.json found.', 'yellow')}")
    print(f"  {c('-' * 50, 'dim')}")
    input(f"\n  {c('Press Enter to continue...', 'dim')}")


def run_proxy(upstream, port=DEFAULT_PORT, host="127.0.0.1"):
    """Start the proxy server."""
    parsed = urlparse(upstream)
    uhost = parsed.hostname or upstream
    uport = parsed.port or (443 if parsed.scheme == 'https' else 80)
    tls = parsed.scheme != 'http'

    Handler.UPSTREAM_HOST = uhost
    Handler.UPSTREAM_PORT = uport
    Handler.USE_TLS = tls

    show_banner(upstream, host, port)

    # Start animation thread
    AnimState.stop_flag = False
    anim = threading.Thread(target=animation_thread, daemon=True)
    anim.start()

    print(f"  {c('ðŸ›¡ï¸  Shield active', 'green')}  {c('- Claude Code will auto-retry through outages', 'dim')}")
    print(f"  {c('Press Ctrl+C to stop.', 'dim')}\n")

    server = Server((host, port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        AnimState.stop_flag = True
        stop_animation()
        print(f"\n{c('ClaudeShield stopped.', 'yellow')}")
        print(f"  Requests handled: {Stats.requests}")
        print(f"  Errors converted: {c(str(Stats.converted), 'cyan')}")
        print(f"  Passed through:   {Stats.passed}")
        print(f"  Network errors:   {Stats.errors}")
        server.shutdown()


def self_update():
    """Download the latest retry-proxy.py from GitHub and replace this file."""
    import urllib.request
    import shutil

    REPO = "brainstormersia-cmd/agentrouter-autoretry-claudecode"
    # Cache-busting query param - GitHub raw caches ~5 min, this forces fresh
    URL = f"https://raw.githubusercontent.com/{REPO}/main/retry-proxy.py?ts={int(time.time())}"
    CURRENT = os.path.abspath(__file__)
    TMP = CURRENT + ".new"

    print(f"\n  {c('ClaudeShield Update', 'bold')}")
    print(f"  Current version: {c('v' + VERSION, 'cyan')}")
    print(f"  Downloading latest from GitHub...")

    try:
        urllib.request.urlretrieve(URL, TMP)
        with open(TMP, 'r', encoding='utf-8') as f:
            new_content = f.read()
        if "retry-proxy.py" not in new_content and "ClaudeShield" not in new_content:
            print(f"  {c('ERROR', 'red')}: downloaded file does not look like ClaudeShield. Aborting.")
            os.remove(TMP)
            return 1

        # Extract remote version for comparison
        import re as _re
        m = _re.search(r'VERSION\s*=\s*"([\d.]+)"', new_content)
        remote_version = m.group(1) if m else "unknown"

        # If same version, still replace (file may have fixes without version bump)
        if remote_version == VERSION:
            print(f"  Already on v{VERSION} - re-applying anyway (may include fixes).")
        else:
            print(f"  Remote version: {c('v' + remote_version, 'cyan')}")

        # Backup current version
        backup = CURRENT + ".bak"
        shutil.copy2(CURRENT, backup)

        # Replace
        os.replace(TMP, CURRENT)
        print(f"  {c('OK', 'green')}: updated to v{remote_version}.")
        print(f"  Backup saved: {backup}")
        print(f"\n  Restart the proxy to use the new version:")
        print(f"    python \"{CURRENT}\" --start")
        return 0
    except Exception as e:
        print(f"  {c('ERROR', 'red')}: {e}")
        if os.path.exists(TMP):
            os.remove(TMP)
        return 1


def main():
    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help"):
        print(f"\n  {c('ClaudeShield', 'bold')} v{VERSION}\n")
        print(f"  {c('Quick start:', 'bold')}  python retry-proxy.py")
        print(f"  {c('Just run:  ', 'bold')}  python retry-proxy.py --start\n")
        print(f"  Commands:")
        print(f"    {c('(none)', 'cyan')}      Interactive setup (recommended for new users)")
        print(f"    {c('--start', 'cyan')}     Start proxy with existing config")
        print(f"    {c('--update', 'cyan')}    Update to latest version from GitHub")
        print(f"    {c('--upstream URL', 'cyan')}  Use a specific gateway")
        print(f"    {c('--port N', 'cyan')}    Use a specific port (default: {DEFAULT_PORT})")
        print(f"    {c('--version', 'cyan')}   Show version\n")
        print(f"  {c('Supported gateways:', 'bold')} AgentRouter, Lumosel, Aerolink, or any")
        print(f"  Anthropic-compatible gateway.\n")
        return

    if args[0] == "--version":
        print(f"ClaudeShield v{VERSION}")
        return

    if args[0] == "--update":
        sys.exit(self_update())

    if args[0] == "--start":
        # Start with default or configured upstream
        upstream = DEFAULT_UPSTREAM
        port = DEFAULT_PORT
        i = 1
        while i < len(args):
            if args[i] == "--upstream" and i+1 < len(args):
                upstream = args[i+1]; i += 2
            elif args[i] == "--port" and i+1 < len(args):
                port = int(args[i+1]); i += 2
            else: i += 1
        run_proxy(upstream, port)
        return

    # Parse --upstream / --port for direct start
    upstream = DEFAULT_UPSTREAM
    port = DEFAULT_PORT
    i = 0
    while i < len(args):
        if args[i] == "--upstream" and i+1 < len(args):
            upstream = args[i+1]; i += 2
        elif args[i] == "--port" and i+1 < len(args):
            port = int(args[i+1]); i += 2
        else: i += 1

    # If --upstream was passed, just start (no interactive)
    has_upstream = "--upstream" in args
    if has_upstream:
        run_proxy(upstream, port)
    else:
        # Interactive setup
        configured_upstream = interactive_setup()
        if configured_upstream:
            run_proxy(configured_upstream, port)


if __name__ == '__main__':
    main()
