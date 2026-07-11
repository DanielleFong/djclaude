#!/usr/bin/env python3
"""Rane ONE -> Claude/Codex tmux rig (djclaude).
  right pitch fader (ch1 cc9, up=low)  -> FABLE  /effort low..max
  left  volume      (ch0 cc28)         -> SONNET /effort low..max
  right volume      (ch1 cc28)         -> GPT5.5 codex /model picker digit 1..4
  left pitch, crossfader               -> display only (phase 2)
State -> /tmp/dragons-state.json for statusbar.py.
"""
import json, os, time, subprocess, pathlib, threading, collections, queue, socket
from http.server import HTTPServer, BaseHTTPRequestHandler

TUNE_FILE = pathlib.Path('/tmp/dragons-tuning.json')
def tune():
    try: return json.loads(TUNE_FILE.read_text())
    except Exception: return {"notch": 1, "play_ignore": 1, "span_opus": 200, "span_fable": 800}

TUNE_HTML = b'''<!doctype html><meta name=viewport content="width=device-width,initial-scale=1">
<body style="background:#0d0d10;color:#ddd;font-family:ui-monospace,monospace;max-width:480px;margin:40px auto;padding:0 20px">
<h2 style="color:#ffd75f">djclaude platter tuning</h2>
<label>NOTCH (platter ticks per 3-line step): <b id=nv></b></label><br>
<input id=n type=range min=1 max=400 style="width:100%%"><br><br>
<label><input id=p type=checkbox> ignore motor/play rotation (scratch-only)</label><br><br>
<label><span style="color:#ff5f5f">OPUS span</span> (pages): <b id=sov></b></label><br>
<input id=so type=range min=20 max=3000 step=20 style="width:100%%"><br>
<label><span style="color:#ffd75f">FABLE span</span> (pages): <b id=sfv></b></label><br>
<input id=sf type=range min=20 max=3000 step=20 style="width:100%%"><br>
<p id=st style="color:#666"></p>
<script>
const n=document.getElementById('n'),p=document.getElementById('p'),nv=document.getElementById('nv'),st=document.getElementById('st'),so=document.getElementById('so'),sf=document.getElementById('sf'),sov=document.getElementById('sov'),sfv=document.getElementById('sfv');
fetch('/tune.json').then(r=>r.json()).then(t=>{n.value=t.notch;nv.textContent=t.notch;p.checked=!!t.play_ignore;so.value=t.span_opus||200;sov.textContent=so.value;sf.value=t.span_fable||800;sfv.textContent=sf.value});
function push(){nv.textContent=n.value;sov.textContent=so.value;sfv.textContent=sf.value;fetch('/set?notch='+n.value+'&play_ignore='+(p.checked?1:0)+'&span_opus='+so.value+'&span_fable='+sf.value).then(()=>st.textContent='applied '+new Date().toLocaleTimeString())}
n.oninput=push; p.onchange=push; so.oninput=push; sf.oninput=push;
</script>'''

class _EffortHTTP(BaseHTTPRequestHandler):
    def do_GET(self):
        from urllib.parse import urlparse, parse_qs
        u = urlparse(self.path)
        if u.path == '/tune':
            self.send_response(200); self.send_header('content-type','text/html; charset=utf-8'); self.end_headers()
            self.wfile.write(TUNE_HTML); return
        if u.path == '/tune.json':
            self.send_response(200); self.send_header('content-type','application/json'); self.end_headers()
            self.wfile.write(json.dumps(tune()).encode()); return
        if u.path == '/set':
            q = parse_qs(u.query)
            t = tune()
            if 'notch' in q: t['notch'] = max(1, min(400, int(q['notch'][0])))
            if 'play_ignore' in q: t['play_ignore'] = int(q['play_ignore'][0])
            for k in ('span_opus','span_fable'):
                if k in q: t[k] = max(20, min(3000, int(q[k][0])))
            TUNE_FILE.write_text(json.dumps(t))
            self.send_response(200); self.end_headers(); return
        return self._effort()
    def _effort(self):
        try: st = json.loads(pathlib.Path('/tmp/dragons-state.json').read_text())
        except Exception: st = {}
        # claude.ai effort follows the fable fader (right pitch)
        lv = ['low','medium','high','xhigh','max']
        v = st.get('fable', 0)
        eff = lv[min(4, v*5//128)]
        body = json.dumps({'effort': eff}).encode()
        self.send_response(200)
        self.send_header('content-type', 'application/json')
        self.send_header('access-control-allow-origin', 'https://claude.ai')
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a): pass

def _serve():
    HTTPServer(('127.0.0.1', 7683), _EffortHTTP).serve_forever()
threading.Thread(target=_serve, daemon=True).start()

import mido
mido.set_backend('mido.backends.rtmidi')

HERE = pathlib.Path(__file__).parent
CFG = json.loads((HERE / 'mapping.json').read_text())
STATE_FILE = pathlib.Path('/tmp/dragons-state.json')
SCRUB_PROBE_MARKER = pathlib.Path('/tmp/dj-scrub-probe.enabled')
SCRUB_PROBE_ADDRESS = ('127.0.0.1', 17683)
LOCAL_SOL_DECK_MARKER = pathlib.Path('/tmp/dj-sol-local-tape.enabled')
LOCAL_SOL_DECK_ADDRESS = ('127.0.0.1', 17684)
STOCK_CODEX_CDP_MARKER = pathlib.Path('/tmp/dj-sol-cdp.enabled')
STOCK_CODEX_CDP_ADDRESS = ('127.0.0.1', 17685)
STOCK_CODEX_CDP_METRICS = pathlib.Path('/tmp/dj-sol-cdp-metrics.json')
S = 'djclaude'
def _load_layout():
    try: raw = json.loads(pathlib.Path('/tmp/dragons-layout.json').read_text())
    except Exception: raw = {'opus': f'{S}:0.0', 'fable': f'{S}:0.1'}
    panes, types, scroll_backends, sessions, thread_titles = {}, {}, {}, {}, {}
    for k, v in raw.items():
        if isinstance(v, dict):
            pane = v.get('pane')
            head_type = v.get('type', 'claude')
            if pane:
                panes[k] = pane
            types[k] = head_type
            sessions[k] = v.get('session', '')
            thread_titles[k] = v.get('thread_title', '')
            backend = v.get(
                'scroll',
                'codex_ax' if head_type == 'codex_app' else 'tmux',
            )
            # `codex_app` was the pre-semantic name for this transport.
            # Normalize it so restored layouts cannot revive pointer-routed HID.
            scroll_backends[k] = 'codex_ax' if backend == 'codex_app' else backend
            continue

        panes[k] = v
        types[k] = 'codex' if k == 'gpt' else 'claude'
        scroll_backends[k] = 'tmux'
        sessions[k] = ''
        thread_titles[k] = ''
    return panes, types, scroll_backends, sessions, thread_titles

PANES, HEAD_TYPE, SCROLL_BACKEND, HEAD_SESSION, HEAD_THREAD_TITLE = _load_layout()
def _session_for_pane(pane):
    # bare pane ids (%10) carry no session name — ask tmux who owns them
    try:
        r = subprocess.run(['tmux', 'display', '-p', '-t', pane, '#{session_name}'],
                           capture_output=True, text=True, timeout=3)
        return r.stdout.strip() or None
    except Exception:
        return None

TMUX_SESSION = (next((pane.split(':', 1)[0] for pane in PANES.values() if ':' in pane), None)
                or next((s for pn in PANES.values() for s in [_session_for_pane(pn)] if s), S))
EFFORTS, GPT_EFFORTS = CFG['efforts'], CFG['gpt_efforts']
OPUS_EFFORTS = ['nothink'] + EFFORTS   # 6 detents: thinking off, then low..max
SOL_EFFORTS = ['low', 'medium', 'high', 'xhigh', 'ultra']
PINNED_EFFORTS = {}
SETTLE = 1.5  # relaxation: fader must rest this long before send
MIDI_RECONNECT_INTERVAL = 1.0
MIDI_POLL_HZ = 2000
MIDI_POLL_INTERVAL = 1 / MIDI_POLL_HZ

state = {n: 0 for n in CFG['controls']}
state.update({
    'sent': {},
    'desired': dict(PINNED_EFFORTS),
    'effort_policy': {},
    'midi_connected': False,
})
state_lock = threading.RLock()
STATE_WRITE_INTERVAL = 1 / 60
_state_written_at = 0.0
_scrub_probe_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
_local_sol_deck_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
_stock_codex_cdp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
_scrub_probe_sequence = 0

def write_state(force=False):
    global _state_written_at
    now = time.monotonic()
    with state_lock:
        if not force and now - _state_written_at < STATE_WRITE_INTERVAL:
            return
        payload = json.dumps(state)
        pending = STATE_FILE.with_suffix('.tmp')
        pending.write_text(payload)
        pending.replace(STATE_FILE)
        _state_written_at = now

def detent(v, levels): return min(len(levels)-1, v*len(levels)//128)


def emit_scrub_probe(head, steps):
    global _scrub_probe_sequence
    if not SCRUB_PROBE_MARKER.exists():
        return
    _scrub_probe_sequence += 1
    payload = json.dumps({
        'seq': _scrub_probe_sequence,
        'head': head,
        'steps': steps,
        'monotonic_ns': time.monotonic_ns(),
    }).encode()
    try:
        _scrub_probe_socket.sendto(payload, SCRUB_PROBE_ADDRESS)
    except OSError:
        pass


def local_sol_deck_is_live():
    try:
        return time.time() - LOCAL_SOL_DECK_MARKER.stat().st_mtime < 3
    except OSError:
        return False


def emit_local_sol_deck(event_type, **fields):
    if not local_sol_deck_is_live():
        return False
    payload = json.dumps({
        'type': event_type,
        'head': 'opus',
        'monotonic_ns': time.monotonic_ns(),
        **fields,
    }).encode()
    try:
        _local_sol_deck_socket.sendto(payload, LOCAL_SOL_DECK_ADDRESS)
        return True
    except OSError:
        return False


def stock_codex_cdp_is_live():
    try:
        return time.time() - STOCK_CODEX_CDP_MARKER.stat().st_mtime < 3
    except OSError:
        return False


def emit_stock_codex_cdp(event_type, **fields):
    if not stock_codex_cdp_is_live():
        return False
    payload = json.dumps({
        'type': event_type,
        'head': 'opus',
        'monotonic_ns': time.monotonic_ns(),
        # A decimal string preserves nanosecond precision through JSON/JS.
        'unix_ns': str(time.time_ns()),
        **fields,
    }).encode()
    try:
        _stock_codex_cdp_socket.sendto(payload, STOCK_CODEX_CDP_ADDRESS)
        return True
    except OSError:
        return False


_stock_cdp_metrics_mtime = 0
def sync_stock_codex_position():
    """Mirror the off-path renderer position into the shared dock state."""
    global _stock_cdp_metrics_mtime
    try:
        stat = STOCK_CODEX_CDP_METRICS.stat()
        if stat.st_mtime_ns == _stock_cdp_metrics_mtime:
            return
        document = json.loads(STOCK_CODEX_CDP_METRICS.read_text())
        fraction = document.get('position', {}).get('fraction')
        if not isinstance(fraction, (int, float)):
            return
        _stock_cdp_metrics_mtime = stat.st_mtime_ns
        state['needle_opus'] = max(0, min(127, round(fraction * 127)))
        state['codex_renderer_position'] = document['position']
        write_state(force=True)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return


def close_midi_port(port):
    if port is None:
        return
    try:
        port.close()
    except Exception:
        pass


def open_midi_port():
    if CFG['port'] not in mido.get_input_names():
        return None
    return mido.open_input(CFG['port'])

_tmux_pipe = None
def _pipe():
    global _tmux_pipe
    if _tmux_pipe is None or _tmux_pipe.poll() is not None:
        _tmux_pipe = subprocess.Popen(['tmux', '-C', 'attach-session', '-t', TMUX_SESSION],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
    return _tmux_pipe

def _q(a):
    return "'" + str(a).replace("'", "'\\''") + "'"

WHEEL_UP  = ['1b','5b','3c','36','34','3b','31','30','3b','31','30','4d']   # ESC[<64;10;10M
WHEEL_DN  = ['1b','5b','3c','36','35','3b','31','30','3b','31','30','4d']   # ESC[<65;10;10M
def tmux_wheel(pane, n):
    seq = WHEEL_DN if n > 0 else WHEEL_UP
    for _ in range(min(48, abs(n))):     # pipe sends are 0.5µs; let big swipes through
        tmux('send-keys', '-t', pane, '-H', *seq)

def tmux_copy_scroll(pane, n):
    # codex CLI never enables mouse reporting, so wheel bytes are a no-op there.
    # Scrub the tmux scrollback instead: copy-mode is idempotent, -e exits at bottom.
    lines = min(48, abs(n)) * 3
    tmux('copy-mode', '-e', '-t', pane)
    tmux('send-keys', '-X', '-t', pane, '-N', str(lines),
         'scroll-down' if n > 0 else 'scroll-up')

CODEX_SCROLL_BINARY = HERE / 'codex-scroll'
CODEX_ANCHORS_PER_STEP = 4
CODEX_EFFORT_RETRY_SECONDS = 2.0
CODEX_SEMANTIC_TIMEOUT_SECONDS = 2.0
_codex_scroll_pipe = None
_codex_scroll_retry_after = 0.0
_codex_effort_retry_at = 0.0
_codex_receipts = queue.SimpleQueue()
_codex_semantic_inflight = False
_codex_semantic_sent_at = 0.0
_codex_pending_steps = 0
_codex_pending_fraction = None


def _parse_receipt(line):
    fields = {}
    for token in line.strip().split()[1:]:
        if '=' not in token:
            continue
        key, value = token.split('=', 1)
        fields[key] = value
    return fields


def _read_codex_receipts(pipe):
    if pipe.stdout is None:
        return
    for line in pipe.stdout:
        _codex_receipts.put(line.strip())


def drain_codex_receipts():
    global _codex_effort_retry_at, _codex_semantic_inflight
    changed = False
    semantic_finished = False
    while True:
        try:
            line = _codex_receipts.get_nowait()
        except queue.Empty:
            break
        receipt = {
            'line': line,
            'at': time.time(),
        }
        fields = _parse_receipt(line)
        receipt.update(fields)
        state['codex_receipt'] = receipt
        if line.startswith('effort_control '):
            state['codex_effort_receipt'] = receipt
            current = fields.get('current')
            if fields.get('status') == 'observed' and current:
                state['sent']['opus'] = current
            elif fields.get('status') == 'deferred':
                delay = 5.0 if fields.get('reason') == 'task_running' else 2.0
                _codex_effort_retry_at = max(
                    _codex_effort_retry_at,
                    time.time() + delay,
                )
        elif line.startswith('semantic_scroll '):
            if _codex_semantic_sent_at > 0:
                receipt['bridge_roundtrip_ms'] = round(
                    (time.time() - _codex_semantic_sent_at) * 1000,
                    3,
                )
            state['codex_scroll_receipt'] = receipt
            status = fields.get('status')
            if status != 'prewarmed':
                try:
                    requested = int(fields.get('requested', '0'))
                except ValueError:
                    requested = 0
                try:
                    actual_delta = int(fields.get('delta', '0'))
                except ValueError:
                    actual_delta = 0
                stalled = status == 'stalled' or (
                    requested != 0 and actual_delta == 0
                )
                direction_ok = requested == 0 or (
                    actual_delta != 0 and
                    (requested > 0) == (actual_delta > 0)
                )
                previous_stalls = state.get(
                    'sol_scroll_verifier', {}
                ).get('consecutive_stalls', 0)
                state['sol_scroll_verifier'] = {
                    'status': (
                        'stalled' if stalled else
                        'direction_mismatch' if not direction_ok else
                        'progressing'
                    ),
                    'requested_anchor_delta': requested,
                    'observed_anchor_delta': actual_delta,
                    'direction_ok': direction_ok,
                    'consecutive_stalls': (
                        previous_stalls + 1 if stalled else 0
                    ),
                    'at': time.time(),
                }
                _codex_semantic_inflight = False
                semantic_finished = True
        elif line.startswith('codex-scroll:'):
            state['codex_scroll_error'] = receipt
            _codex_semantic_inflight = False
            semantic_finished = True
        changed = True
    if changed:
        write_state(force=True)
    if semantic_finished:
        flush_codex_scroll()


def _open_codex_scroll_pipe():
    global _codex_scroll_pipe, _codex_scroll_retry_after
    global _codex_semantic_inflight
    if _codex_scroll_pipe is not None and _codex_scroll_pipe.poll() is None:
        return _codex_scroll_pipe
    if _codex_scroll_pipe is not None:
        _codex_scroll_pipe = None
        _codex_semantic_inflight = False
        _codex_scroll_retry_after = time.time() + 2
    if time.time() < _codex_scroll_retry_after:
        return None
    if not CODEX_SCROLL_BINARY.exists():
        print(f'codex scroll helper missing: {CODEX_SCROLL_BINARY}', flush=True)
        return None

    try:
        _codex_scroll_pipe = subprocess.Popen(
            [str(CODEX_SCROLL_BINARY)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env={
                **os.environ,
                'CODEX_THREAD_ID': HEAD_SESSION.get('opus', ''),
                'CODEX_THREAD_TITLE': (
                    HEAD_THREAD_TITLE.get('opus') or 'Better Cal Sol'
                ),
            },
        )
        threading.Thread(
            target=_read_codex_receipts,
            args=(_codex_scroll_pipe,),
            daemon=True,
        ).start()
    except OSError as error:
        print(f'codex scroll helper failed to start: {error}', flush=True)
        _codex_scroll_retry_after = time.time() + 2
        return None
    return _codex_scroll_pipe


def _send_codex_semantic(command):
    global _codex_scroll_pipe, _codex_scroll_retry_after
    global _codex_semantic_inflight, _codex_semantic_sent_at
    pipe = _open_codex_scroll_pipe()
    if pipe is None or pipe.stdin is None:
        return False

    try:
        pipe.stdin.write(command + '\n')
        pipe.stdin.flush()
        _codex_semantic_inflight = True
        _codex_semantic_sent_at = time.time()
        return True
    except (BrokenPipeError, ValueError):
        _codex_scroll_pipe = None
        _codex_semantic_inflight = False
        _codex_scroll_retry_after = time.time() + 2
        return False


def flush_codex_scroll(now=None):
    global _codex_scroll_pipe, _codex_scroll_retry_after
    global _codex_semantic_inflight, _codex_semantic_sent_at
    global _codex_pending_steps, _codex_pending_fraction
    now = now or time.time()
    if _codex_semantic_inflight:
        if now - _codex_semantic_sent_at <= CODEX_SEMANTIC_TIMEOUT_SECONDS:
            return
        if _codex_scroll_pipe is not None:
            _codex_scroll_pipe.terminate()
        _codex_scroll_pipe = None
        _codex_semantic_inflight = False
        _codex_scroll_retry_after = now + 0.25

    if _codex_pending_fraction is not None:
        fraction = _codex_pending_fraction
        if _send_codex_semantic(f'fraction {fraction:.4f}'):
            _codex_pending_fraction = None
            _codex_pending_steps = 0
        return

    if not _codex_pending_steps:
        return
    steps = _codex_pending_steps
    if _send_codex_semantic(f'step {steps * CODEX_ANCHORS_PER_STEP}'):
        _codex_pending_steps = 0


def codex_app_scroll(steps):
    global _codex_pending_steps, _codex_pending_fraction
    _codex_pending_fraction = None
    _codex_pending_steps = max(-64, min(64, _codex_pending_steps + steps))
    flush_codex_scroll()


def codex_app_seek_fraction(fraction):
    global _codex_pending_steps, _codex_pending_fraction
    _codex_pending_steps = 0
    _codex_pending_fraction = max(0.0, min(1.0, fraction))
    flush_codex_scroll()


def codex_app_set_effort(effort):
    global _codex_scroll_pipe, _codex_scroll_retry_after
    global _codex_effort_retry_at, _codex_semantic_inflight
    _codex_effort_retry_at = time.time() + CODEX_EFFORT_RETRY_SECONDS
    pipe = _open_codex_scroll_pipe()
    if pipe is None or pipe.stdin is None:
        return False
    try:
        pipe.stdin.write(f'effort {effort}\n')
        pipe.stdin.flush()
        return True
    except (BrokenPipeError, ValueError):
        _codex_scroll_pipe = None
        _codex_semantic_inflight = False
        _codex_scroll_retry_after = time.time() + 2
        return False


def retry_pending_codex_effort(now):
    if 'opus' in PINNED_EFFORTS:
        return
    if HEAD_TYPE.get('opus') != 'codex_app':
        return
    if now < _codex_effort_retry_at:
        return
    with state_lock:
        desired = state['desired'].get('opus')
        sent = state['sent'].get('opus')
    if not desired or desired == sent:
        return
    codex_app_set_effort(desired)


def can_scroll(head):
    backend = SCROLL_BACKEND.get(head, 'tmux')
    if backend == 'codex_cdp':
        return head == 'opus' and stock_codex_cdp_is_live()
    if backend == 'local_full_tape':
        return head == 'opus' and local_sol_deck_is_live()
    return backend != 'disabled' and (backend == 'codex_ax' or head in PANES)


def supports_absolute_scroll(head):
    return SCROLL_BACKEND.get(head, 'tmux') == 'tmux' and head in PANES


def scroll(head, steps):
    if not steps:
        return
    state['scroll_event_count'] = state.get('scroll_event_count', 0) + 1
    state['last_scroll'] = {
        'head': head,
        'steps': steps,
        'backend': SCROLL_BACKEND.get(head, 'tmux'),
        'at': time.time(),
    }
    write_state()
    emit_scrub_probe(head, steps)
    pane = PANES.get(head)
    if SCROLL_BACKEND.get(head) == 'codex_ax':
        codex_app_scroll(steps)
        return
    if pane:
        if HEAD_TYPE.get(head) == 'codex':
            tmux_copy_scroll(pane, steps)
        else:
            tmux_wheel(pane, steps)

def tmux(*args):
    try:
        p = _pipe()
        p.stdin.write(' '.join(_q(a) for a in args) + '\n'); p.stdin.flush()
        if p.poll() is not None:                       # pipe died mid-write: command lost
            raise BrokenPipeError('control pipe dead')
    except Exception:
        subprocess.run(['tmux', *args], check=False)   # fallback


def claude_prompt_is_safe(pane):
    result = subprocess.run(
        ['tmux', 'capture-pane', '-p', '-t', pane, '-S', '-12'],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return False
    tail = result.stdout
    if 'esc to interrupt' in tail or 'Running ' in tail or 'Roosting…' in tail:
        return False
    prompt_lines = [
        line.strip()
        for line in tail.splitlines()
        if line.strip().startswith('❯')
    ]
    if not prompt_lines:
        return False
    return not prompt_lines[-1][1:].strip()


def dispatch_claude_effort(head, effort):
    pane = PANES.get(head)
    if not pane or not claude_prompt_is_safe(pane):
        state['claude_effort_receipt'] = {
            'status': 'deferred',
            'head': head,
            'requested': effort,
            'reason': 'prompt_busy_or_nonempty',
            'at': time.time(),
        }
        return False
    tmux('send-keys', '-t', pane, '-l', f'/effort {effort}')
    tmux('send-keys', '-t', pane, 'Enter')
    state['sent'][head] = effort
    state['claude_effort_receipt'] = {
        'status': 'dispatched',
        'head': head,
        'requested': effort,
        'at': time.time(),
    }
    return True


_claude_effort_retry_at = 0.0
def retry_pending_claude_effort(now):
    global _claude_effort_retry_at
    if now < _claude_effort_retry_at:
        return
    _claude_effort_retry_at = now + 1.0
    desired = state['desired'].get('fable')
    if desired and desired != state['sent'].get('fable'):
        dispatch_claude_effort('fable', desired)

def send(head, level_idx):
    if head in PINNED_EFFORTS:
        state['desired'][head] = PINNED_EFFORTS[head]
        return
    head_type = HEAD_TYPE.get(head)
    if head_type == 'codex_app':
        eff = SOL_EFFORTS[level_idx]
        if state['desired'].get(head) == eff:
            return
        state['desired'][head] = eff
        codex_app_set_effort(eff)
        print(f"[{time.strftime('%H:%M:%S')}] {head} effort requested -> {eff}", flush=True)
        return
    if head not in PANES: return
    if head_type not in ('codex', 'claude'):
        return
    if head_type == 'codex':
        eff = GPT_EFFORTS[level_idx]
        if state['sent'].get(head) == eff: return
        p = PANES[head]
        if eff == 'none':
            # picker can't reach none: respawn pane, resume same thread at effort none
            resume_target = HEAD_SESSION.get(head) or '--last'
            tmux('respawn-pane', '-k', '-t', p,
                 f'codex resume {resume_target} -a never -s danger-full-access '
                 '-c model_reasoning_effort=none')
        else:
            tmux('send-keys', '-t', p, 'Escape')            # close any open dialog
            time.sleep(0.3); tmux('send-keys', '-t', p, 'Escape')
            time.sleep(0.3); tmux('send-keys', '-t', p, '/model', 'Enter')
            time.sleep(1.0); tmux('send-keys', '-t', p, 'Enter')  # submit slash command
            time.sleep(1.2); tmux('send-keys', '-t', p, '1')  # explicit: 1 = gpt-5.5
            time.sleep(0.8); tmux('send-keys', '-t', p, str(level_idx))  # digit: low=1..xhigh=4
        state['sent'][head] = eff
    elif head == 'opus' and HEAD_TYPE.get(head) == 'claude':
        eff = OPUS_EFFORTS[level_idx]
        prev = state['sent'].get('opus')
        if prev == eff: return
        p = PANES['opus']
        if eff == 'nothink':
            tmux('send-keys', '-t', p, '/config thinking=false', 'Enter')
            time.sleep(0.8); tmux('send-keys', '-t', p, '/effort low', 'Enter')
        else:
            if prev in (None, 'nothink'):
                tmux('send-keys', '-t', p, '/config thinking=true', 'Enter')
                time.sleep(0.8)
            tmux('send-keys', '-t', p, f'/effort {eff}', 'Enter')
        state['sent']['opus'] = eff
    else:
        eff = EFFORTS[level_idx]
        state['desired'][head] = eff
        if state['sent'].get(head) == eff: return
        if not dispatch_claude_effort(head, eff):
            return
    print(f"[{time.strftime('%H:%M:%S')}] {head} -> {eff}", flush=True)
    with open(HERE / 'slack.md', 'a') as f:
        f.write(f"[{time.strftime('%H:%M:%S')}] rig: {head} effort -> {eff}\n")

def _reap_orphan_pipes():
    # control-mode clients from dead daemons distort sizing; reap any tmux -C with ppid 1
    try:
        out = subprocess.run(['pgrep', '-f', 'tmux -C attach'], capture_output=True, text=True).stdout
        for pid in out.split():
            pp = subprocess.run(['ps', '-o', 'ppid=', '-p', pid], capture_output=True, text=True).stdout.strip()
            if pp == '1': subprocess.run(['kill', pid])
    except Exception: pass

def main():
    _reap_orphan_pipes()
    ctl = {(c['channel'], c['cc']): (n, c.get('invert', False))
           for n, c in CFG['controls'].items()}
    pending = None
    pending_heads = set()
    last_raw, jump_cand = {}, {}
    jog_last, jog_acc, strip_last = {}, {}, {}
    jog_tick_t, jog_run_start, jog_run_ticks = {}, {}, {}
    jog_vel, jog_vel_t = {}, {}
    jog_gest = {}
    jog_motor = {}
    play_dir, play_until, play_beat = {}, {}, {}
    auto_scrub = {}; scrub_t = 0; strip_touch_t = {}; strip_pending = {}; strip_acc = {}; strip_seen = {}; last_input_t = {}; pos_pages = {}; seek_target = {}; seek_t = 0
    LAT = collections.deque(maxlen=100000)   # (proc latency seconds)
    port = None
    connected_once = False
    port_present = None
    next_port_probe = 0.0
    next_cdp_position_sync = 0.0
    write_state()
    if HEAD_TYPE.get('opus') == 'codex_app':
        _open_codex_scroll_pipe()
    if True:
        while True:
            now = time.time()
            if now >= next_cdp_position_sync:
                next_cdp_position_sync = now + 0.1
                sync_stock_codex_position()
            drain_codex_receipts()
            retry_pending_codex_effort(now)
            retry_pending_claude_effort(now)
            flush_codex_scroll(now)
            if now >= next_port_probe:
                next_port_probe = now + MIDI_RECONNECT_INTERVAL
                try:
                    is_present = CFG['port'] in mido.get_input_names()
                except Exception as error:
                    is_present = None
                    print(f"[{time.strftime('%H:%M:%S')}] MIDI endpoint probe failed: {error}", flush=True)

                if is_present is not None:
                    if port_present is not None and port_present != is_present:
                        status = 'available' if is_present else 'disconnected'
                        print(f"[{time.strftime('%H:%M:%S')}] {CFG['port']} {status}", flush=True)
                    port_present = is_present

                    if not is_present and port is not None:
                        close_midi_port(port)
                        port = None
                        state['midi_connected'] = False
                        write_state(force=True)

                    if is_present and port is None:
                        try:
                            port = open_midi_port()
                        except Exception as error:
                            print(f"[{time.strftime('%H:%M:%S')}] MIDI connect failed: {error}", flush=True)
                            port = None
                        if port is not None:
                            verb = 'reconnected to' if connected_once else 'listening on'
                            print(f"[{time.strftime('%H:%M:%S')}] {verb} {CFG['port']} (polling at {MIDI_POLL_HZ}Hz)", flush=True)
                            connected_once = True
                            state['midi_connected'] = True
                            state['midi_connected_at'] = time.time()
                            write_state(force=True)

            if port is None:
                time.sleep(MIDI_POLL_INTERVAL)
                continue

            try:
                messages = list(port.iter_pending())
            except Exception as error:
                print(f"[{time.strftime('%H:%M:%S')}] MIDI read failed: {error}", flush=True)
                close_midi_port(port)
                port = None
                state['midi_connected'] = False
                write_state(force=True)
                continue

            moved = False
            for m in messages:
                _ts = time.perf_counter()
                if m.type != 'control_change': continue
                LAT.append(time.perf_counter() - _ts)
                nc = ctl.get((m.channel, m.control))
                if not nc: continue
                name, inv = nc
                mode = CFG['controls'][name].get('mode')
                state['midi_event_count'] = state.get('midi_event_count', 0) + 1
                state['last_midi'] = {
                    'control': name,
                    'channel': m.channel,
                    'cc': m.control,
                    'value': m.value,
                    'at': time.time(),
                }
                write_state()
                if mode == 'jog':
                    head = name.split('_')[1]
                    if can_scroll(head):
                        last = jog_last.get(name)
                        jog_last[name] = m.value
                        if last is None: continue
                        d = (m.value - last + 64) % 128 - 64   # wrapped delta
                        tick_now = time.monotonic()
                        tick_interval = tick_now - jog_tick_t.get(
                            name,
                            tick_now - 1,
                        )
                        jog_tick_t[name] = tick_now
                        motor = jog_motor.setdefault(
                            name,
                            {'direction': 0, 'run': 0, 'active': False},
                        )
                        direction = 1 if d > 0 else -1 if d < 0 else 0
                        steady_motor_tick = (
                            abs(d) == 1 and
                            direction == motor['direction'] and
                            tick_interval < 0.003
                        )
                        if steady_motor_tick:
                            motor['run'] += 1
                        else:
                            motor['direction'] = direction
                            motor['run'] = 1 if abs(d) == 1 else 0
                            motor['active'] = False
                        if tune().get('play_ignore', 1) and abs(d) == 1:
                            if motor['run'] >= 8:
                                motor['active'] = True
                            # Buffer the first few exact motor-shaped ticks so
                            # an unattended platter cannot drift the transcript.
                            if motor['active'] or tick_interval < 0.003:
                                continue
                        # rebound deadband: platters physically settle back a few ticks
                        # after a spin — swallow small reversals right after motion stops
                        now_r = time.time()
                        g = jog_gest.setdefault(name, {'dir': 0, 'run': 0, 't': 0, 'debt': 0})
                        if d != 0:
                            dr = 1 if d > 0 else -1
                            if dr == g['dir']:
                                g['run'] += abs(d); g['debt'] = 0
                            else:
                                budget = 4 if g['run'] < 300 else 60   # hand wobble vs motor braking
                                window = 0.35 if g['run'] < 300 else 0.8
                                if g['run'] >= 8 and now_r - g['t'] < window:
                                    g['debt'] += abs(d)
                                    if g['debt'] <= budget:
                                        g['t'] = now_r
                                        continue          # swallowed rebound/braking
                                g['dir'], g['run'], g['debt'] = dr, abs(d), 0
                            g['t'] = now_r
                        if head == 'opus' and d != 0 and stock_codex_cdp_is_live():
                            needle = state.get('needle_opus', 127)
                            tape_offset = state.get('tape_off') or 0
                            off_tape_delta = max(1, abs(d) * 2)
                            if d < 0 and needle <= 2:
                                state['tape_off'] = min(
                                    3600,
                                    tape_offset + off_tape_delta,
                                )
                                write_state()
                                continue
                            if d > 0 and tape_offset > 0:
                                state['tape_off'] = max(
                                    0,
                                    tape_offset - off_tape_delta,
                                )
                                write_state()
                                continue
                            emitted = emit_stock_codex_cdp('jog', delta=d)
                            if not emitted:
                                continue
                            emit_scrub_probe(head, -1 if d < 0 else 1)
                            state['last_jog_step'] = {
                                'head': head,
                                'encoder_delta': d,
                                'transport': 'stock_cdp',
                                'at': time.time(),
                            }
                            write_state()
                            continue
                        if head == 'opus' and emit_local_sol_deck(
                            'jog',
                            delta=d,
                        ):
                            state['last_jog_step'] = {
                                'head': head,
                                'encoder_delta': d,
                                'transport': 'local_full_tape',
                                'at': time.time(),
                            }
                            write_state()
                            continue
                        auto_scrub[name.split('_')[1]] = 0      # platter takes over
                        seek_target.pop(name.split('_')[1], None)  # kill in-flight seek: scratch is tactile, not paced
                        last_input_t[name.split('_')[1]] = time.time()
                        # ONE GEAR, sized to reality: this platter emits ~920 ticks/s
                        # at play speed (measured), not 70. no slip-curve, no modes.
                        now_v = time.time()
                        dt = max(0.002, now_v - jog_vel_t.get(name, now_v - 0.05))
                        jog_vel_t[name] = now_v
                        NOTCH = tune().get('notch', 52)
                        # fractional-step accumulator: gear-invariant, backlash-free
                        frac = jog_acc.get(name, 0.0)
                        if dt > 0.25: frac = 0.0               # dwell: hard-cancel residue
                        if frac * d < 0: frac = 0.0            # reversal: instant, no debt
                        frac += d / NOTCH
                        frac = max(-1.2, min(1.2, frac))       # anti-windup in STEP units
                        n = int(frac)
                        jog_acc[name] = frac - n
                        if n:
                            # Both Rane platters use the same encoder polarity.
                            # Keep Sol aligned with the known-good Fable deck:
                            # negative encoder travel means older history.
                            scroll_steps = n
                            state['last_jog_step'] = {
                                'head': head,
                                'encoder_delta': d,
                                'scroll_steps': scroll_steps,
                                'chronology': (
                                    'older' if scroll_steps < 0 else 'newer'
                                ),
                                'at': time.time(),
                            }
                            k = 'needle_' + head
                            pos = state.get(k, 127)
                            anchored = time.time() - strip_touch_t.get(head, 0) < 5
                            if anchored and (
                                (scroll_steps < 0 and pos <= 0) or
                                (scroll_steps > 0 and pos >= 127)
                            ):
                                jog_acc[name] = 0.0             # trusted wall: locked, no debt
                            else:
                                state[k] = max(
                                    0,
                                    min(127, pos + scroll_steps * 6),
                                )
                                write_state()
                                scroll(head, scroll_steps)      # scroll only when not walled
                    continue
                if mode == 'strip':
                    strip_pending[name] = m.value   # coalesce: only newest position acts
                    continue
                raw = m.value
                lr = last_raw.get(name)
                if lr is not None and abs(raw - lr) > 40:
                    # spurious-jump guard (Serato sync snaps): need 2nd nearby event
                    jc = jump_cand.get(name)
                    if jc is None or abs(raw - jc) > 25:
                        jump_cand[name] = raw
                        continue
                jump_cand.pop(name, None)
                last_raw[name] = raw
                v = 127 - raw if inv else raw
                if state.get(name) != v:
                    state[name] = v
                    moved = True
                    if name in ('opus', 'fable', 'sonnet', 'gpt'):
                        pending_heads.add(name)
            for name, val in list(strip_pending.items()):
                del strip_pending[name]
                head = name.split('_')[1]
                if not can_scroll(head): continue
                if head == 'opus' and emit_stock_codex_cdp(
                    'strip',
                    value=val,
                ):
                    state['needle_' + head] = val
                    write_state()
                    continue
                if head == 'opus' and emit_local_sol_deck(
                    'strip',
                    value=val,
                ):
                    state['needle_' + head] = val
                    write_state()
                    continue
                # touch/release transient guard: isolated extreme samples are noise;
                # snap zones need two consecutive in-zone readings
                prev_v, prev_t = strip_seen.get(name, (None, 0))
                strip_seen[name] = (val, time.time())
                zone = 'hi' if val >= 120 else ('lo' if val <= 6 else 'mid')
                if zone != 'mid':
                    # transient = extreme arriving mid-flight (fresh mid-band motion + big jump)
                    fresh_mid = prev_v is not None and 19 < prev_v < 108 and time.time() - prev_t < 0.15
                    if fresh_mid and abs(val - prev_v) > 40:
                        continue                       # release-noise: drop
                    # deliberate tip touch: snap immediately
                elif prev_v is not None and abs(val - prev_v) > 60 and time.time() - prev_t > 0.2:
                    strip_last[name] = val             # finger re-landed elsewhere: re-anchor,
                    state['needle_' + head] = val      # no scroll burst
                    write_state(); continue
                auto_scrub[head] = 0
                strip_touch_t[head] = time.time()
                last_input_t[head] = time.time()
                if SCROLL_BACKEND.get(head) == 'codex_ax':
                    codex_app_seek_fraction(val / 127.0)
                    state['needle_' + head] = val
                    if val >= 108:
                        auto_scrub[head] = 1
                    elif val <= 19:
                        auto_scrub[head] = -1
                    write_state()
                    continue
                if val >= 120:
                    state['needle_' + head] = 127; state['tape_off'] = 0
                    span = tune().get('span_' + head, 400)
                    pos_pages[head] = span
                    if supports_absolute_scroll(head):
                        tmux('send-keys', '-t', PANES[head], 'C-End')
                    else:
                        scroll(head, span * 2)
                elif val <= 6:
                    state['needle_' + head] = 0
                    pos_pages[head] = 0
                    span = tune().get('span_' + head, 400)
                    if supports_absolute_scroll(head):
                        tmux('send-keys', '-t', PANES[head], 'C-Home')
                    else:
                        scroll(head, -span * 2)
                elif val >= 108:
                    state['needle_' + head] = val; auto_scrub[head] = 1
                elif val <= 19:
                    state['needle_' + head] = val; auto_scrub[head] = -1
                else:
                    # ABSOLUTE seek: strip maps the whole transcript, 7..119 -> 0..100%
                    span = tune().get('span_' + head, 400)
                    frac = (val - 7) / 112.0
                    target = int(frac * span)
                    if pos_pages.get(head) is None:     # unknown: anchor via C-End first
                        if supports_absolute_scroll(head):
                            tmux('send-keys', '-t', PANES[head], 'C-End')
                        else:
                            scroll(head, span * 2)
                        pos_pages[head] = span
                    seek_target[head] = target          # pacer slews toward this
                    state['needle_' + head] = val
                write_state()
            if moved:
                pending = time.time(); write_state()
            _now = time.time()
            PLAY_BEAT = 2.0                                     # seconds per 3-line step
            for nm, until in list(play_until.items()):
                if _now < until and _now - play_beat.get(nm, 0) >= PLAY_BEAT:
                    play_beat[nm] = _now
                    h = nm.split('_')[1]
                    if can_scroll(h): scroll(h, play_dir.get(nm, 1))
            if int(time.time()) % 5 == 0 and LAT and getattr(main, '_lat_t', 0) != int(time.time()):
                main._lat_t = int(time.time())
                sl = sorted(LAT)
                def pct(p): return sl[min(len(sl)-1, int(len(sl)*p))] * 1000
                pathlib.Path('/tmp/dragons-latency.json').write_text(json.dumps({
                    'n': len(sl), 'p50_ms': round(pct(0.50), 3), 'p99_ms': round(pct(0.99), 3),
                    'p9999_ms': round(pct(0.9999), 3), 'max_ms': round(sl[-1]*1000, 3)}))
            if time.time() - seek_t > 0.025:
                seek_t = time.time()
                for h, tgt in list(seek_target.items()):
                    cur = pos_pages.get(h)
                    if cur is None or not can_scroll(h): seek_target.pop(h, None); continue
                    d_ = tgt - cur
                    if abs(d_) < 1: seek_target.pop(h, None); continue
                    # adaptive rate: whole journey lands in ~1s (40 ticks), min 3 pg/tick
                    cap = max(3, abs(d_) // 40 + 1)
                    step = max(-cap, min(cap, d_))
                    n_ = int(step) if abs(step) >= 1 else (1 if step > 0 else -1)
                    if supports_absolute_scroll(h):
                        for _ in range(abs(n_)):
                            tmux('send-keys', '-t', PANES[h], 'NPage' if n_ > 0 else 'PPage')
                    else:
                        scroll(h, n_)
                    pos_pages[h] = cur + n_
            _fnow = time.time()
            if _fnow - globals().get('_fc_t', 0) < 0.25:
                pass
            else:
              globals()['_fc_t'] = _fnow
              for h in list(last_input_t):
                if _fnow - last_input_t[h] < 1.0:
                    ndl = state.get('needle_' + h, 127)
                    if ndl <= 2:                                    # pinned left: tape follows
                        state['tape_off'] = min(3600, state.get('tape_off', 0) + 10)
                        write_state()
                    elif ndl >= 125 and state.get('tape_off', 0) > 0:  # pinned right: reel to live
                        state['tape_off'] = max(0, state['tape_off'] - 30)
                        write_state()
            if time.time() - scrub_t > 0.7:
                scrub_t = time.time()
                for h, d in list(auto_scrub.items()):
                    if d and time.time() - strip_touch_t.get(h, 0) > 2.5:
                        auto_scrub[h] = 0; continue             # finger gone: stop
                    if d and can_scroll(h):
                        scroll(h, d * 2)         # readable auto-crawl: 6 lines per beat
                        k = 'needle_' + h
                        pos = state.get(k, 64)
                        if d < 0 and pos <= 0:
                            state['tape_off'] = min(3600, state.get('tape_off', 0) + 45)  # capped tape scrub
                        elif d > 0 and state.get('tape_off', 0) > 0:
                            state['tape_off'] = max(0, state['tape_off'] - 45)
                        else:
                            state[k] = max(0, min(127, pos + d * 3))
                            if state[k] == 127: auto_scrub[h] = 0
                        write_state()
            if pending and time.time() - pending > SETTLE:
                pending = None
                for head in tuple(pending_heads):
                    if head == 'opus':
                        levels = (
                            GPT_EFFORTS
                            if HEAD_TYPE.get('opus') == 'codex'
                            else (
                                SOL_EFFORTS
                                if HEAD_TYPE.get('opus') == 'codex_app'
                                else OPUS_EFFORTS
                            )
                        )
                    elif head == 'gpt':
                        levels = GPT_EFFORTS
                    else:
                        levels = EFFORTS
                    send(head, detent(state[head], levels))
                pending_heads.clear()
                write_state()
            time.sleep(MIDI_POLL_INTERVAL)

if __name__ == '__main__':
    main()
