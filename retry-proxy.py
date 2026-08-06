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

VERSION = "2.1.0"
DEFAULT_PORT = 8787
DEFAULT_UPSTREAM = "https://agentrouter.org"

# Known gateways
GATEWAYS = [
    ("AgentRouter", "https://agentrouter.org", "claude-opus-4-8"),
    ("Lumosel",     "https://api.lumosel.vip", "claude-opus-5"),
    ("Aerolink",    "https://capi.aerolink.lat", "claude-opus-4-8"),
]


# ═══════════════════════════════════════════════════════════════
#  RAINBOW ANIMATION ENGINE
# ═══════════════════════════════════════════════════════════════

_NO_COLOR = sys.platform == 'win32' and not os.environ.get('WT_SESSION') and not os.environ.get('TERM')

# 256-color rainbow palette
RAINBOW = [196, 202, 208, 214, 220, 226, 190, 154, 118, 82, 46, 47, 48, 49, 50, 51, 45, 39, 33, 27, 21, 57, 93, 129, 165, 201]

def rainbow_text(text, offset=0):
    """Color text with cycling rainbow colors."""
    if _NO_COLOR:
        return text
    result = []
    for i, ch in enumerate(text):
        if ch == ' ':
            result.append(ch)
        else:
            color = RAINBOW[(i + offset) % len(RAINBOW)]
            result.append(f"\033[38;5;{color}m{ch}\033[0m")
    return ''.join(result)

def c(text, color):
    if _NO_COLOR:
        return text
    colors = {'green':'\033[92m','red':'\033[91m','yellow':'\033[93m',
              'cyan':'\033[96m','bold':'\033[1m','dim':'\033[2m','reset':'\033[0m'}
    return f"{colors.get(color,'')}{text}{colors['reset']}"

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
    "  ██████╗██╗  ██╗██████╗  ██████╗ ███████╗██████╗  ██╗   ██╗███████╗",
    " ██╔════╝██║  ██║██╔══██╗██╔═══██╗██╔════╝██╔══██╗██║   ██║██╔════╝",
    " ██║     ███████║██████╔╝██║   ██║█████╗  ██████╔╝██║   ██║███████╗",
    " ██║     ██╔══██║██╔══██╗██║   ██║██╔══╝  ██╔══██╗██║   ██║╚════██║",
    " ╚██████╗██║  ██║██║  ██║╚██████╔╝███████╗██║  ██║╚██████╔╝███████║",
    "  ╚═════╝╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═╝ ╚═════╝ ╚══════╝",
]

SPINNER = ['⠋','⠙','⠹','⠸','⠼','⠴','⠦','⠧','⠇','⠏']
SHIELD_FRAMES = ['🛡️','🛡️','🛡️','🛡️']
BAR_FRAMES = [
    '▁▁▁▁▁▁▁▁▁▁▁',
    '▁▁▂▂▃▃▄▄▅▅▆',
    '▂▂▃▃▄▄▅▅▆▆▇',
    '▃▃▄▄▅▅▆▆▇▇█',
    '▄▅▆▇█▇▆▅▄▃▂',
    '▅▆▇█▇▆▅▄▃▂▁',
    '▆▇█▇▆▅▄▃▂▁▁',
    '▇█▇▆▅▄▃▂▁▁▁',
]


def animation_thread():
    """Background animation that shows status when proxy is active."""
    while not AnimState.stop_flag:
        if AnimState.active:
            frame = SPINNER[AnimState.spinner_frame % len(SPINNER)]
            bar = BAR_FRAMES[AnimState.spinner_frame % len(BAR_FRAMES)]
            shield = SHIELD_FRAMES[AnimState.spinner_frame % len(SHIELD_FRAMES)]
            offset = AnimState.rainbow_offset

            action_colors = {
                'CONVERT': 'cyan',
                'RETRY': 'yellow',
                'NETERR': 'red',
                'PASS': 'dim',
                'OK': 'green',
            }
            ac = action_colors.get(AnimState.last_action, 'green')
            status = AnimState.last_status

            # Build animated status line - rainbow spinner + rainbow text + rainbow bar
            # Spinner in rainbow (cycles color per frame)
            spinner_color = RAINBOW[AnimState.rainbow_offset % len(RAINBOW)]
            spinner_str = f"\033[38;5;{spinner_color}m{frame}\033[0m"
            
            # CLAUDE orange + SHIELD rainbow
            claude_str = f"\033[38;5;208mCLAUDE\033[0m"
            shield_str = rainbow_text("SHIELD", offset)
            
            # Action label in its color
            action_str = c(f"[{AnimState.last_action}]", ac)
            
            # Bar in rainbow
            bar_str = rainbow_text(bar, offset + 5)
            
            status_text = f" {shield} {spinner_str} {claude_str}{shield_str} {action_str} "
            if status:
                status_text += f"{c(str(status), 'bold')} "
            status_text += f"{bar_str} "
            status_text += f"{c('protecting...', 'dim')}"

            # Pad and overwrite
            pad = max(0, AnimState.line_len - len(status_text))
            sys.stderr.write(f"\r\033[K{status_text}{' ' * pad}")
            sys.stderr.flush()
            AnimState.line_len = len(status_text)

            AnimState.spinner_frame += 1
            AnimState.rainbow_offset += 1
            time.sleep(0.12)
        else:
            time.sleep(0.3)

    # Clear animation line on stop
    if AnimState.line_len > 0:
        sys.stderr.write(f"\r\033[K")
        sys.stderr.flush()


def trigger_animation(action, status=0):
    """Activate the animation with a specific action."""
    AnimState.active = True
    AnimState.last_action = action
    AnimState.last_status = status

def stop_animation():
    """Deactivate the animation."""
    AnimState.active = False
    if AnimState.line_len > 0:
        sys.stderr.write(f"\r\033[K")
        sys.stderr.flush()
        AnimState.line_len = 0


def orange_text(text):
    """Color text in orange (256-color)."""
    if _NO_COLOR:
        return text
    return f"\033[38;5;208m{text}\033[0m"

def show_banner(upstream, host, port):
    """Show CLAUDE (orange) + SHIELD (rainbow) ASCII art banner."""
    # Split each line: first ~28 chars = CLAUDE (orange), rest = SHIELD (rainbow)
    split_pos = 28
    for line in SHIELD_ART:
        if line.strip():
            claude_part = line[:split_pos]
            shield_part = line[split_pos:]
            print(f"  {orange_text(claude_part)}{rainbow_text(shield_part, offset=int(time.time()*8) % len(RAINBOW))}")
    
    print()
    print(f"  {c('v' + VERSION, 'dim')}  ·  {c('Listening on', 'dim')} {c(f'http://{host}:{port}', 'bold')}")
    print(f"  {c('Proxying to', 'dim')} {c(upstream, 'cyan')}")
    print()
    print(f"  {c('🛡️  Shield active', 'green')}  {c('- Claude Code will auto-retry through outages', 'dim')}")
    print(f"  {c('Press Ctrl+C to stop.', 'dim')}")
    print()
    print(f"  {rainbow_text('● ● ● ● ● ● ● ● ● ●', offset=0)}")
    print()



# ═══════════════════════════════════════════════════════════════
#  PROXY
# ═══════════════════════════════════════════════════════════════

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

    def do_GET(self):    self._handle("GET")
    def do_POST(self):  self._handle("POST")
    def do_HEAD(self):  self._handle("HEAD")
    def do_PUT(self):    self._handle("PUT")
    def do_DELETE(self): self._handle("DELETE")

    def _handle(self, method):
        Stats.requests += 1
        rid = Stats.requests
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length) if length > 0 else None

        headers = {}
        for k, v in self.headers.items():
            if k.lower() not in ('host','content-length','connection',
                                 'transfer-encoding','accept-encoding'):
                headers[k] = v
        headers['Accept-Encoding'] = 'identity'

        if self.USE_TLS:
            ctx = ssl.create_default_context()
            conn = http.client.HTTPSConnection(self.UPSTREAM_HOST, self.UPSTREAM_PORT,
                                               timeout=300, context=ctx)
        else:
            conn = http.client.HTTPConnection(self.UPSTREAM_HOST, self.UPSTREAM_PORT,
                                              timeout=300)
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

            is_stream = 'text/event-stream' in (resp.getheader('Content-Type') or '')
            if is_stream:
                self._stream(resp, status, rheaders)
            else:
                rbody = resp.read()
                self._send(status, rbody, rheaders)
        except (http.client.HTTPException, ConnectionError, TimeoutError, OSError) as e:
            Stats.errors += 1
            self._net_error(str(e), rid)
        finally:
            try: conn.close()
            except: pass

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

    def _stream(self, resp, status, headers):
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
        permanent = ['无权访问模型','not authorized to access','model not found',
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
                    '额度','不足','余额','限制','超额','耗尽']
        is_retry = (any(kw in text for kw in retry_kw) or
                    status in (429,500,502,503,504,505,510,511,
                               520,521,522,523,524,525,526,527,529,530))
        if status == 504: is_retry = True

        # Convert 403/400/504 -> 429
        if is_retry and status in (400,403,408,409,413,504):
            Stats.converted += 1
            self.log(f"[{rid}] {c('CONVERT','cyan')} {status} -> 429 | {text[:100]}")
            trigger_animation('CONVERT', status)
            err = json.dumps({"type":"error","error":{"type":"rate_limit_error",
                "message":"Rate limited (converted by ClaudeShield). Retrying."}}).encode()
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


# ═══════════════════════════════════════════════════════════════
#  INTERACTIVE SETUP
# ═══════════════════════════════════════════════════════════════

def interactive_setup():
    """Guide the user through setup with simple questions."""
    home = os.path.expanduser("~")
    claude_dir = os.path.join(home, ".claude")
    settings_path = os.path.join(claude_dir, "settings.json")

    print(f"\n  {c('ClaudeShield Setup', 'bold')}  v{VERSION}\n")
    print("  This will configure Claude Code to survive rate limits.\n")
    print("  Answer 3 questions and you're done.\n")

    # 1. Which gateway?
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

    # 2. API key
    # Try to read existing key from settings
    existing_key = ""
    if os.path.exists(settings_path):
        try:
            with open(settings_path) as f:
                old = json.load(f)
            env = old.get("env", {})
            existing_key = (env.get("ANTHROPIC_API_KEY") or
                           env.get("ANTHROPIC_AUTH_TOKEN") or "")
        except:
            pass

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

    # 3. Model
    print(f"\n  Which model? {c('(press Enter for ' + default_model + ')', 'dim')}")
    model = input(f"  {c('>', 'bold')} ").strip() or default_model

    # 4. Configure
    print(f"\n  {c('Configuring...', 'cyan')}")

    os.makedirs(claude_dir, exist_ok=True)

    # Copy this script
    current = os.path.abspath(__file__)
    script_dest = os.path.join(claude_dir, "retry-proxy.py")
    if current != script_dest:
        import shutil
        shutil.copy2(current, script_dest)
        print(f"  {c('[OK]', 'green')} Copied proxy to {script_dest}")

    # Read/create settings
    settings = {}
    if os.path.exists(settings_path):
        try:
            with open(settings_path) as f:
                settings = json.load(f)
        except:
            settings = {}

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
    print(f"  {c('[OK]', 'green')} Updated {settings_path}")

    print(f"\n  {c('Done! Setup complete.', 'bold')}\n")
    print(f"  Now run these two commands:\n")
    print(f"    {c('1. Start the proxy:', 'bold')}")
    print(f"       python \"{script_dest}\" --start\n")
    print(f"    {c('2. In another terminal, launch Claude Code:', 'bold')}")
    print(f"       claude --dangerously-skip-permissions\n")
    print(f"  {c('That is it! Claude Code will now survive rate limits.', 'green')}\n")
    return upstream


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

    print(f"  {c('🛡️  Shield active', 'green')}  {c('- Claude Code will auto-retry through outages', 'dim')}")
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


def main():
    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help"):
        print(f"\n  {c('ClaudeShield', 'bold')} v{VERSION}\n")
        print(f"  {c('Quick start:', 'bold')}  python retry-proxy.py")
        print(f"  {c('Just run:  ', 'bold')}  python retry-proxy.py --start\n")
        print(f"  Commands:")
        print(f"    {c('(none)', 'cyan')}      Interactive setup (recommended for new users)")
        print(f"    {c('--start', 'cyan')}     Start proxy with existing config")
        print(f"    {c('--upstream URL', 'cyan')}  Use a specific gateway")
        print(f"    {c('--port N', 'cyan')}    Use a specific port (default: {DEFAULT_PORT})")
        print(f"    {c('--version', 'cyan')}   Show version\n")
        print(f"  {c('Supported gateways:', 'bold')} AgentRouter, Lumosel, Aerolink, or any")
        print(f"  Anthropic-compatible gateway.\n")
        return

    if args[0] == "--version":
        print(f"ClaudeShield v{VERSION}")
        return

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
