#!/usr/bin/env python3
"""Rane ONE -> Claude/Codex tmux rig (djclaude).
  right pitch fader (ch1 cc9, up=low)  -> FABLE  /effort low..max
  left  volume      (ch0 cc28)         -> SONNET /effort low..max
  right volume      (ch1 cc28)         -> GPT5.5 codex /model picker digit 1..4
  left pitch, crossfader               -> display only (phase 2)
State -> /tmp/dragons-state.json for statusbar.py.
"""
import json, time, subprocess, pathlib, threading, collections
from http.server import HTTPServer, BaseHTTPRequestHandler

TUNE_FILE = pathlib.Path('/tmp/dragons-tuning.json')
def tune():
    try: return json.loads(TUNE_FILE.read_text())
    except Exception: return {"notch": 1, "play_ignore": 0, "span_opus": 200, "span_fable": 800}

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
S = 'djclaude'
try:
    PANES = json.loads(pathlib.Path('/tmp/dragons-layout.json').read_text())
except Exception:
    PANES = {'opus': f'{S}:0.0', 'fable': f'{S}:0.1'}
EFFORTS, GPT_EFFORTS = CFG['efforts'], CFG['gpt_efforts']
OPUS_EFFORTS = ['nothink'] + EFFORTS   # 6 detents: thinking off, then low..max
SETTLE = 1.5  # relaxation: fader must rest this long before send

state = {n: 0 for n in CFG['controls']}
state.update({'sent': {}})

def write_state(): STATE_FILE.write_text(json.dumps(state))

def detent(v, levels): return min(len(levels)-1, v*len(levels)//128)

_tmux_pipe = None
def _pipe():
    global _tmux_pipe
    if _tmux_pipe is None or _tmux_pipe.poll() is not None:
        _tmux_pipe = subprocess.Popen(['tmux', '-C', 'attach-session', '-t', S],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
    return _tmux_pipe

def _q(a):
    return "'" + str(a).replace("'", "'\\''") + "'"

WHEEL_UP  = ['1b','5b','3c','36','34','3b','31','30','3b','31','30','4d']   # ESC[<64;10;10M
WHEEL_DN  = ['1b','5b','3c','36','35','3b','31','30','3b','31','30','4d']   # ESC[<65;10;10M
def wheel(pane, n):
    seq = WHEEL_DN if n > 0 else WHEEL_UP
    for _ in range(min(48, abs(n))):     # pipe sends are 0.5µs; let big swipes through
        tmux('send-keys', '-t', pane, '-H', *seq)

def tmux(*args):
    try:
        p = _pipe()
        p.stdin.write(' '.join(_q(a) for a in args) + '\n'); p.stdin.flush()
    except Exception:
        subprocess.run(['tmux', *args], check=False)   # fallback

def send(head, level_idx):
    if head not in PANES: return
    if head == 'gpt':
        eff = GPT_EFFORTS[level_idx]
        if state['sent'].get('gpt') == eff: return
        p = PANES['gpt']
        if eff == 'none':
            # picker can't reach none: respawn pane, resume same thread at effort none
            tmux('respawn-pane', '-k', '-t', p,
                 'codex resume --last -a never -s workspace-write -c model_reasoning_effort=none')
        else:
            tmux('send-keys', '-t', p, 'Escape')            # close any open dialog
            time.sleep(0.3); tmux('send-keys', '-t', p, 'Escape')
            time.sleep(0.3); tmux('send-keys', '-t', p, '/model', 'Enter')
            time.sleep(1.0); tmux('send-keys', '-t', p, 'Enter')  # submit slash command
            time.sleep(1.2); tmux('send-keys', '-t', p, '1')  # explicit: 1 = gpt-5.5
            time.sleep(0.8); tmux('send-keys', '-t', p, str(level_idx))  # digit: low=1..xhigh=4
        state['sent']['gpt'] = eff
    elif head == 'opus':
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
        if state['sent'].get(head) == eff: return
        tmux('send-keys', '-t', PANES[head], f'/effort {eff}', 'Enter')
        state['sent'][head] = eff
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
    last_raw, jump_cand = {}, {}
    jog_last, jog_acc, strip_last = {}, {}, {}
    jog_tick_t, jog_run_start = {}, {}
    jog_gest = {}
    play_dir, play_until, play_beat = {}, {}, {}
    auto_scrub = {}; scrub_t = 0; strip_touch_t = {}; strip_pending = {}; strip_acc = {}; strip_seen = {}; last_input_t = {}; pos_pages = {}; seek_target = {}; seek_t = 0
    q = collections.deque()
    LAT = collections.deque(maxlen=100000)   # (proc latency seconds)
    def _stamped(msg): q.append((time.perf_counter(), msg))
    port = mido.open_input(CFG['port'], callback=_stamped)
    print(f"listening on {CFG['port']} (event-driven, 240Hz loop)", flush=True)
    write_state()
    last_evt = time.time(); reopen_t = 0
    if True:
        while True:
            # self-heal: if silent >20s, try reopening the port (device re-enumeration)
            if q: last_evt = time.time()
            if time.time() - last_evt > 20 and time.time() - reopen_t > 20:
                reopen_t = time.time()
                try:
                    port.close()
                    port = mido.open_input(CFG['port'], callback=_stamped)
                    print(f"[{time.strftime('%H:%M:%S')}] reopened MIDI port", flush=True)
                except Exception as e:
                    print(f"[{time.strftime('%H:%M:%S')}] reopen failed: {e}", flush=True)
            moved = False
            while q:
                _ts, m = q.popleft()
                if m.type != 'control_change': continue
                LAT.append(time.perf_counter() - _ts)
                nc = ctl.get((m.channel, m.control))
                if not nc: continue
                name, inv = nc
                mode = CFG['controls'][name].get('mode')
                if mode == 'jog':
                    head = name.split('_')[1]
                    if head in PANES:
                        last = jog_last.get(name)
                        jog_last[name] = m.value
                        if last is None: continue
                        d = (m.value - last + 64) % 128 - 64   # wrapped delta
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
                        auto_scrub[name.split('_')[1]] = 0      # platter takes over
                        last_input_t[name.split('_')[1]] = time.time()
                        jog_acc[name] = jog_acc.get(name, 0.0) + d
                        now_t = time.time()
                        if now_t - jog_tick_t.get(name, 0) > 0.3:
                            jog_run_start[name] = now_t          # gap: new gesture
                        jog_tick_t[name] = now_t
                        playing = now_t - jog_run_start.get(name, now_t) > 1.5
                        t_ = tune()
                        if playing and t_.get('play_ignore', 1):
                            jog_acc[name] = 0.0                 # motor/play: ignore
                            continue
                        NOTCH = t_.get('notch', 1)
                        # anti-windup: never owe more than one step of history
                        jog_acc[name] = max(-NOTCH*1.5, min(NOTCH*1.5, jog_acc[name]))
                        n = int(jog_acc[name] / NOTCH)
                        if n:
                            jog_acc[name] -= n * NOTCH
                            k = 'needle_' + head
                            pos = state.get(k, 127)
                            if (n < 0 and pos <= 0) or (n > 0 and pos >= 127):
                                jog_acc[name] = 0.0             # at the wall: locked, no debt
                            else:
                                state[k] = max(0, min(127, pos + n * 6))
                                write_state()
                            wheel(PANES[head], n)   # 3-line wheel notches: smooth
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
                    state[name] = v; moved = True
            for name, val in list(strip_pending.items()):
                del strip_pending[name]
                head = name.split('_')[1]
                if head not in PANES: continue
                # touch/release transient guard: isolated extreme samples are noise;
                # snap zones need two consecutive in-zone readings
                prev_v, prev_t = strip_seen.get(name, (None, 0))
                strip_seen[name] = (val, time.time())
                zone = 'hi' if val >= 120 else ('lo' if val <= 6 else 'mid')
                if zone != 'mid':
                    pz = 'hi' if (prev_v or 64) >= 108 else ('lo' if (prev_v or 64) <= 19 else 'mid')
                    if pz != zone or time.time() - prev_t > 0.25:
                        continue                       # unconfirmed extreme: drop
                elif prev_v is not None and abs(val - prev_v) > 60 and time.time() - prev_t > 0.2:
                    strip_last[name] = val             # finger re-landed elsewhere: re-anchor,
                    state['needle_' + head] = val      # no scroll burst
                    write_state(); continue
                auto_scrub[head] = 0
                strip_touch_t[head] = time.time()
                last_input_t[head] = time.time()
                if val >= 120:
                    state['needle_' + head] = 127; state['tape_off'] = 0
                    pos_pages[head] = tune().get('span_' + head, 400)
                    tmux('send-keys', '-t', PANES[head], 'C-End')
                elif val <= 6:
                    state['needle_' + head] = 0
                    pos_pages[head] = 0
                    tmux('send-keys', '-t', PANES[head], 'C-Home')
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
                        tmux('send-keys', '-t', PANES[head], 'C-End'); pos_pages[head] = span
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
                    if h in PANES: wheel(PANES[h], play_dir.get(nm, 1))
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
                    if cur is None or h not in PANES: seek_target.pop(h, None); continue
                    d_ = tgt - cur
                    if abs(d_) < 1: seek_target.pop(h, None); continue
                    step = max(-3, min(3, d_))          # ≤3 pages per 25ms = 120 pages/s, steady
                    n_ = int(step) if abs(step) >= 1 else (1 if step > 0 else -1)
                    for _ in range(abs(n_)):
                        tmux('send-keys', '-t', PANES[h], 'NPage' if n_ > 0 else 'PPage')
                    pos_pages[h] = cur + n_
            _fnow = time.time()
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
                    if d and h in PANES:
                        wheel(PANES[h], d * 2)   # readable auto-crawl: 6 lines per beat
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
                send('opus', detent(state['opus'], OPUS_EFFORTS))
                send('fable', detent(state['fable'], EFFORTS))
                send('sonnet', detent(state['sonnet'], EFFORTS))
                send('gpt', detent(state['gpt'], GPT_EFFORTS))
                write_state()
            time.sleep(1/240)

if __name__ == '__main__':
    main()
