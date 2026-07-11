#!/usr/bin/env python3
"""djclaude dock: per-head spend/activity timeline. Zoom: ctrl-scroll. Pan/scrub: scroll.
End/e = live-follow. Braille columns, 1Hz sampler, 30fps renderer."""
import datetime, json, os, re, select, subprocess, sys, termios, time, tty, pathlib, threading, unicodedata

ANSI_RE = re.compile(r'\x1b\[[0-9;?]*[ -/]*[@-~]')
NAME_CELLS = 7
EFFORT_CELLS = 8
LEDGER_CELLS = 22
TAPE_COLUMN = NAME_CELLS + EFFORT_CELLS


def visible_width(text):
    """Return terminal cells, excluding ANSI control sequences."""
    return sum(character_cells(character) for character in ANSI_RE.sub('', text))


def character_cells(character):
    if unicodedata.combining(character) or character in ('\ufe0e', '\ufe0f'):
        return 0
    return 2 if unicodedata.east_asian_width(character) in ('W', 'F') else 1


def fit_cells(text, width, align='left'):
    """ANSI-aware fixed-cell field for the dock's shared row schema."""
    visible = 0
    output = []
    for token in re.split(f'({ANSI_RE.pattern})', text):
        if not token:
            continue
        if ANSI_RE.fullmatch(token):
            output.append(token)
            continue
        for character in token:
            cells = character_cells(character)
            if visible + cells > width:
                break
            output.append(character)
            visible += cells
    padding = ' ' * max(0, width - visible)
    return padding + ''.join(output) if align == 'right' else ''.join(output) + padding

S = 'djclaude'
STATE = pathlib.Path('/tmp/dragons-state.json')
LOG = pathlib.Path('/tmp/dragons-timeline.jsonl')
try:
    _raw = json.loads(pathlib.Path('/tmp/dragons-layout.json').read_text())
    _LAY = {}
    for k, v in _raw.items():
        if not isinstance(v, dict):
            _LAY[k] = v
            continue
        if v.get('type') == 'codex_app' and v.get('session'):
            pane_target = f"codex-thread:{v['session']}"
        else:
            pane_target = v.get('pane')
        _LAY[k] = pane_target
    _TITLE = {k: (v.get('title','').upper() if isinstance(v, dict) else '') for k, v in _raw.items()}
    _TYPE = {k: (v.get('type','claude') if isinstance(v, dict) else 'claude') for k, v in _raw.items()}
    _SESSION = {k: (v.get('session','') if isinstance(v, dict) else '') for k, v in _raw.items()}
    _TRANSCRIPT = {k: (v.get('transcript','') if isinstance(v, dict) else '') for k, v in _raw.items()}
except Exception:
    _LAY = {'opus': f'{S}:0.0', 'fable': f'{S}:0.1'}; _TITLE = {}; _TYPE = {}; _SESSION = {}; _TRANSCRIPT = {}
_ALL = [('OPUS','opus','\033[38;5;203m'), ('SONNET','sonnet','\033[38;5;44m'),
        ('GPT','gpt','\033[38;5;114m'), ('FABLE','fable','\033[38;5;220m')]
HEADS = [((_TITLE.get(k) or n), _LAY[k], c, k) for n, k, c in _ALL if k in _LAY]
PINK = '\033[38;5;205m'
HEADS = HEADS + [((_TITLE.get('human') or 'HUMAN'), 'human:', PINK, 'human')]   # human row anchors the bottom
# row palette by resolved title: SOL = sun gold, FABLE = orange, HUMAN = pink
_ROWCOLOR = {'SOL': '\033[38;5;220m', 'FABLE': '\033[38;5;208m', 'HUMAN': PINK}
HEADS = [(n, pn, _ROWCOLOR.get(n, c), k) for n, pn, c, k in HEADS]
DIM, RST, B = '\033[2m', '\033[0m', '\033[1m'
series = {}   # t -> [act0..act3, spend0..spend3]
events = []   # {'t','head','cost','total'} emitted at burst end
totals = [0.0]*len(HEADS)
lock = threading.Lock()
WLOCK = threading.Lock()   # tty write lock: full-frame renderer vs 120fps EQ painter
ACT_T = 40    # activity threshold for burst detection
UNIT = ['$', 'tok', 'ctx', 'tok']

def kfmt(v):
    if v >= 1e6: return f"{v/1e6:.1f}M"
    return f"{v/1000:.1f}k" if v >= 1000 else f"{v:.0f}"


# --- HUMAN row: live mic tape + real prompt events. multiplayer: more human rows via layout ---
# pylon sync-tier EQ, ported from tools/pylon/src/sync/spectrum.rs (glyphs + bands + peak-hold)
EQ_W = 16
EQ_VERT   = ['⠀','⣀','⣤','⣶','⣷','⣿','⣿','⣿','⣿']   # pylon BRAILLE_VERT
EQ_SHADOW = ['⠀','⠤','⠶','⠿','⡿','⣟','⣿','⣿','⣿']   # pylon peak-hold shadows
EQ_HEAT = [52, 88, 124, 160, 196, 202, 208, 214, 226, 231]  # blackbody: deep red -> white @ 20 kHz
class _Mic:
    """Default input → level + 16 log-band Goertzel EQ at 8ms hops (125 fps bands).
    sox raw stream, pure stdlib. Degrades to ok=False silently."""
    DEVICE = 'Logitech BRIO'           # preferred input; falls back to system default
    def __init__(self):
        self.level, self.ok = 0.0, False
        self.bands = [0.0]*EQ_W
        self.peaks = [0.0]*EQ_W
        self.dev = self.DEVICE
        threading.Thread(target=self._run, daemon=True).start()
    def _run(self):
        import array, math
        RATE, CHUNK = 48000, 384           # native rate, 8ms hops — pylon sync-tier budget
        FREQS = [100 * (200 ** (i / (EQ_W - 1))) for i in range(EQ_W)]   # log 100 Hz .. 20 kHz
        coef = [2*math.cos(2*math.pi*f/RATE) for f in FREQS]
        while True:
            try:
                src_args = ['-t', 'coreaudio', self.dev] if self.dev else ['-d']
                proc = subprocess.Popen(
                    ['sox', '--buffer', str(CHUNK * 2), '-q', *src_args, '-t', 'raw',
                     '-b', '16', '-e', 'signed-integer', '-c', '1', '-r', str(RATE), '-'],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
                got_data = False
                while True:
                    data = proc.stdout.read(CHUNK * 2)
                    if not data or len(data) < CHUNK * 2: break
                    got_data = True
                    s = array.array('h', data)
                    acc = 0
                    for x in s: acc += x*x
                    self.level = 0.8*self.level + 0.2*min(1.0, (acc/len(s))**0.5/32768*6)
                    for i, c in enumerate(coef):
                        s1 = s2 = 0.0
                        for x in s:
                            s0 = x + c*s1 - s2; s2 = s1; s1 = s0
                        mag = (max(0.0, s1*s1 + s2*s2 - c*s1*s2))**0.5 / (len(s)*8192)
                        db = 20 * math.log10(mag + 1e-7)               # 0 dB = full scale
                        b = min(1.0, max(0.0, (db + 60 + 12) / 60))    # -60..0 dB -> 0..1, +12 dB gain
                        if b > self.bands[i]:
                            self.bands[i] = b                          # instant attack
                        else:
                            self.bands[i] *= 0.984                     # exp release, tau ~= 0.5 s @125 Hz
                        self.peaks[i] = max(self.bands[i], self.peaks[i]*0.99)
                    self.ok = True
                proc.kill()
                if not got_data and self.dev:
                    self.dev = None            # named device unavailable: fall back to default
                    self.ok = False
                    time.sleep(2); continue
                elif got_data and self.dev is None:
                    self.dev = self.DEVICE     # stream ended: try the preferred device again
            except Exception:
                pass
            self.ok = False
            time.sleep(30)     # mic denied/busy/unplugged: retry without TCC spam
MIC = _Mic()

# --- live meter slot: HUMAN = red REC + mic EQ · models = voice channel (transcript byte-rate) ---
METER_W = 12

def meter_for(color):
    if not MIC.ok:
        return DIM + '○mic off    '[:METER_W] + RST + color
    t120 = int(time.time() * 120)
    cells = []
    for j, v in enumerate(_resample(MIC.bands, METER_W-2)):
        x = v * 8.0
        k = int(x)
        if ((t120*31 + j*17) % 16) / 16.0 < (x - k):   # temporal dither: sub-glyph levels
            k += 1
        k = max(1, min(8, k))                           # ⣀ floor: live channel never reads blank
        cells.append(f'\033[38;5;{EQ_HEAT[j]}m' + EQ_VERT[k])
    return '\033[38;5;196m●' + ''.join(cells) + color + ' '        # red REC + blackbody EQ

def _resample(vals, n):
    L = len(vals)
    return [vals[min(L-1, int(j*L/n))] for j in range(n)]

HUMAN_PROMPTS = [0]
HUMAN_RATE_USD = float(os.environ.get('DJCLAUDE_HUMAN_RATE_USD', '500'))
HUMAN_ACTIVE_GRACE_SECONDS = 90
HUMAN_LEDGER = {'active_seconds': 0, 'last_signal_at': 0}
_hsrc, _hoff = [], {}
for _k, _p in _TRANSCRIPT.items():
    if _p and _TYPE.get(_k) == 'claude':
        _hsrc.append((pathlib.Path(_p), '"userType":"external"'))
for _k, _s in _SESSION.items():
    if _TYPE.get(_k) == 'codex_app' and _s:
        _m = sorted((pathlib.Path.home() / '.codex/sessions').glob(f'**/*{_s}*.jsonl'))
        if _m: _hsrc.append((_m[0], 'user_message'))


def _event_timestamp(record):
    raw = record.get('timestamp') or record.get('created_at')
    if isinstance(raw, (int, float)):
        return float(raw)
    if not isinstance(raw, str):
        return None
    try:
        return datetime.datetime.fromisoformat(
            raw.replace('Z', '+00:00')
        ).timestamp()
    except ValueError:
        return None


def load_human_history():
    """Estimate operator time from deduplicated prompt timestamps."""
    timestamps = []
    counts = []
    for path, needle in _hsrc:
        count = 0
        try:
            with open(path, errors='ignore') as source:
                for line in source:
                    if needle not in line:
                        continue
                    count += 1
                    try:
                        timestamp = _event_timestamp(json.loads(line))
                    except json.JSONDecodeError:
                        timestamp = None
                    if timestamp is not None:
                        timestamps.append(timestamp)
            _hoff[path] = path.stat().st_size
        except OSError:
            continue
        counts.append(count)

    unique = []
    for timestamp in sorted(timestamps):
        if not unique or timestamp - unique[-1] > 3:
            unique.append(timestamp)
    active_seconds = 0
    if unique:
        active_seconds = 60
        active_seconds += sum(
            min(HUMAN_ACTIVE_GRACE_SECONDS, right - left)
            for left, right in zip(unique, unique[1:])
        )
    HUMAN_PROMPTS[0] = max(counts, default=0)
    HUMAN_LEDGER['active_seconds'] = max(0, active_seconds)
    HUMAN_LEDGER['last_signal_at'] = unique[-1] if unique else 0


load_human_history()


def human_tick():
    spike = 0
    for f, needle in _hsrc:
        try:
            o0 = _hoff.get(f); sz = f.stat().st_size
            if o0 is None:
                _hoff[f] = sz; continue          # anchor at now: only NEW prompts light the tape
            if sz <= o0: continue
            with open(f, errors='ignore') as fh:
                fh.seek(o0)
                for ln in fh:
                    if needle in ln:
                        spike += 1; HUMAN_PROMPTS[0] += 1
                _hoff[f] = fh.tell()
        except Exception: pass
    return spike


_CODEX_TRANSCRIPTS = {}
def pane(t):
    if t == 'human:':
        return ''
    if t and t.startswith('codex-thread:'):
        thread_id = t.split(':', 1)[1]
        path = _CODEX_TRANSCRIPTS.get(thread_id)
        if path is None:
            matches = list((pathlib.Path.home() / '.codex/sessions').glob(
                f'**/*{thread_id}*.jsonl'
            ))
            path = matches[0] if matches else False
            _CODEX_TRANSCRIPTS[thread_id] = path
        if not path:
            return ''
        try:
            return path.read_bytes()[-16000:].decode('utf8', 'ignore')
        except Exception:
            return ''
    if not t:
        return ''
    r = subprocess.run(['tmux','capture-pane','-p','-t',t], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ''


# --- cumulative token/dollar ledger from real transcripts ---
CLPROJ = pathlib.Path.home() / ".claude/projects/-Users-daniellefong-cc-rane-claude"
CODEX_S = pathlib.Path.home() / ".codex/sessions"
PRICE = {"opus": (15, 75, 1.5, 18.75), "sonnet": (3, 15, 0.3, 3.75),
         "fable": (15, 75, 1.5, 18.75), "gpt": (1.25, 10, 0.125, 1.25)}  # ~$/Mtok in,out,cr,cw
HKEYS = tuple(k for _,_,_,k in HEADS)
last_txt = {}
cum = {h: {"tok": 0.0, "usd": 0.0} for h in ('opus','sonnet','gpt','fable')}
_off = {}
def ledger_tick():
    exact_claude_transcripts = {
        pathlib.Path(path)
        for key, path in _TRANSCRIPT.items()
        if path and _TYPE.get(key) == 'claude'
    }
    claude_transcripts = exact_claude_transcripts or set(CLPROJ.glob("*.jsonl"))
    for f in claude_transcripts:
        try:
            o0 = _off.get(f, 0); sz = f.stat().st_size
            if sz <= o0: continue
            with open(f) as fh:
                fh.seek(o0)
                for ln in fh:
                    if '"usage"' not in ln: continue
                    try: o = json.loads(ln)
                    except Exception: continue
                    msg = o.get("message") or {}
                    model = msg.get("model") or ""
                    h = next((x for x in ("opus","sonnet","fable") if x in model), None)
                    u = msg.get("usage")
                    if not h or not u: continue
                    i, out = u.get("input_tokens",0), u.get("output_tokens",0)
                    cr = u.get("cache_read_input_tokens",0); cw = u.get("cache_creation_input_tokens",0)
                    pr = PRICE[h]
                    cum[h]["tok"] += i+out+cr+cw
                    for blk in (msg.get("content") or []):
                        if isinstance(blk, dict) and blk.get("type") == "text" and blk.get("text","").strip():
                            last_txt[h] = blk["text"].strip().split("\n")[0][:100]
                    cum[h]["usd"] += (i*pr[0] + out*pr[1] + cr*pr[2] + cw*pr[3]) / 1e6
                _off[f] = fh.tell()
        except Exception: pass
    try:
        thread_id = next(
            (session for key, session in _SESSION.items()
             if _TYPE.get(key) == 'codex_app' and session),
            '',
        )
        candidates = list(CODEX_S.glob(f"*/*/*/*{thread_id}*.jsonl")) if thread_id else []
        f = candidates[0] if candidates else max(
            CODEX_S.glob("*/*/*/*.jsonl"),
            key=lambda x: x.stat().st_mtime,
        )
        tail = f.read_bytes()[-8000:].decode("utf8","ignore")
        m = re.findall(r'"total_token_usage":\{"input_tokens":(\d+),"cached_input_tokens":(\d+),"output_tokens":(\d+)', tail)
        if m:
            i, ci, out = map(int, m[-1])
            pr = PRICE["gpt"]
            cum["gpt"]["tok"] = i+out
            cum["gpt"]["usd"] = ((i-ci)*pr[0] + ci*pr[2] + out*pr[1]) / 1e6
    except Exception: pass

def sampler():
    N = len(HEADS)
    prev = ['']*N
    burst = [None]*N   # None or {'start_spend','acc','idle'}
    if LOG.exists():
        for ln in LOG.read_text().splitlines()[-3600:]:
            try:
                o = json.loads(ln)
                values = o['v']
                if len(values) < N:
                    continue
                if not all(isinstance(value, (int, float)) for value in values[:N]):
                    continue
                series[o['t']] = values
            except Exception:
                pass
    while True:
        t = int(time.time()); v = []
        spends = []
        hum_spike = human_tick()
        if hum_spike or MIC.level >= 0.025:
            HUMAN_LEDGER['last_signal_at'] = t
        if t - HUMAN_LEDGER['last_signal_at'] <= HUMAN_ACTIVE_GRACE_SECONDS:
            HUMAN_LEDGER['active_seconds'] += 1
        for i,(_, p, _, hk) in enumerate(HEADS):
            if hk == 'human':
                v.append(min(2000, int(MIC.level * 1400) + hum_spike * 1200))
                spends.append(None)
                continue
            txt = pane(p)
            act = 0 if txt == prev[i] else sum(a!=b for a,b in zip(txt.ljust(4000), prev[i].ljust(4000)))
            prev[i] = txt; v.append(min(act, 2000))
            spends.append(None)  # ledger owns spend now
        for i in range(N):
            if HKEYS[i] == 'human':
                continue
            act, sp = v[i], spends[i]
            b = burst[i]
            ledger_key = 'gpt' if _TYPE.get(HKEYS[i]) in ('codex', 'codex_app') else HKEYS[i]
            if act > ACT_T:
                if b is None:
                    burst[i] = {'start_spend': cum[ledger_key]['usd'], 'acc': act, 'idle': 0}
                else:
                    b['acc'] += act; b['idle'] = 0
            elif b is not None:
                b['idle'] += 1
                if b['idle'] >= 3 and b['acc'] > 300:   # burst over (ignore repaint blips)
                    cost = max(0.0, cum[ledger_key]['usd'] - b['start_spend'])
                    with lock:
                        totals[i] += cost
                        events.append({'t': t, 'head': i, 'cost': cost, 'total': totals[i]})
                    try:
                        hk = HKEYS[i]
                        snip = last_txt.get(ledger_key, '')
                        with open(pathlib.Path(__file__).parent / 'slack.md', 'a') as f:
                            f.write(f"[{time.strftime('%H:%M:%S')}] rig: {hk.upper()} burst ~${cost:.2f} — {snip}\n")
                    except Exception: pass
                    burst[i] = None
        with lock:
            series[t] = v + spends
            with open(LOG,'a') as f: f.write(json.dumps({'t':t,'v':v+spends})+'\n')
        ledger_tick()
        time.sleep(1.0)

ZOOMS = [1,2,5,10,30,60]  # sec per column
_prev_sent = {}
_lock_until = {}
_tgt_seen, _tgt_t = {}, {}
CHARGE = ['⡀','⡄','⡆','⡇','⣇','⣧','⣷','⣿']
def render(w, zi, offset, follow):
    now = int(time.time()); z = ZOOMS[zi]
    cols = max(10, w - TAPE_COLUMN - 1 - LEDGER_CELLS)
    end = now if follow else now - offset
    t0 = end - cols*z
    with lock: snap = dict(series)
    try: st_ = json.loads(STATE.read_text())
    except Exception: st_ = {}
    sent = st_.get('sent', {})
    def s_needle(k): return st_.get('needle_' + k) if k in ('opus','fable') else None
    EFF5 = ["low","medium","high","xhigh","max"]
    EFF6 = ["nothink"] + EFF5
    GEFF = ["none","low","medium","high","xhigh"]
    SOL_EFF = ["low","medium","high","xhigh","max","ultra"]
    def target(k):
        desired = st_.get('desired', {}).get(k)
        if st_.get('effort_policy', {}).get(k) == 'pinned' and desired:
            return desired
        v = st_.get(k)
        if v is None: return None
        if _TYPE.get(k) == 'codex_app':
            lv = SOL_EFF
        else:
            lv = EFF6 if k == 'opus' else (GEFF if k == 'gpt' else EFF5)
        return lv[min(len(lv)-1, v*len(lv)//128)]
    # periodic full clear to purge stray output/scroll residue — ONCE per 5s, no strobe
    _tnow = time.time()
    if _tnow - getattr(render, '_cleared', 0) > 5:
        render._cleared = _tnow
        out = ['\033[2J\033[H']
    else:
        out = ['\033[H']
    for i,(name, _, c, key) in enumerate(HEADS):
        buckets = []
        for ci in range(cols):
            lo, hi = t0+ci*z, t0+(ci+1)*z
            vals = [snap[t][i] for t in range(lo,hi) if t in snap]
            buckets.append(sum(vals)/max(1,len(vals)) if vals else 0)
        mx = max(max(buckets), 50)
        # DSD/tape-style density coding: dots-per-cell ~ level, dithered
        DITHER = (16, 4, 64, 8, 128, 2, 32, 1)   # spread-out braille bit order
        chars = []
        for ci, b in enumerate(buckets):
            k = min(8, round(b / mx * 8))
            rot = ((t0 + ci * z) // z * 3) % 8     # anchored to absolute time: stable under panning
            dots = 0
            for j in range(k): dots |= DITHER[(rot + j) % 8]
            chars.append(chr(0x2800 + dots))
        with lock: evs = [e for e in events if e['head'] == i and t0 <= e['t'] <= end]
        if evs:                                    # only the latest burst labelled
            e = max(evs, key=lambda e: e['t'])
            col = (e['t'] - t0) // z + 1
            lab = f"${e['cost']:.2f}/${e['total']:.2f}"
            for j, ch in enumerate(lab):
                if 0 <= col+j < len(chars): chars[col+j] = ch
        if not follow:                              # scrub cursor readout
            cur_col = cols - 1
            vals = [snap[t][i] for t in range(end-z, end) if t in snap]
            lab = f"◀{kfmt(sum(vals)/max(1,len(vals)))}"
            for j, ch in enumerate(lab):
                if 0 <= cur_col-len(lab)+j < len(chars): chars[cur_col-len(lab)+j] = ch
        ndl = s_needle(key)
        if ndl is not None:
            RED = '\033[38;5;196m'                    # distinct from the orange FABLE row
            scrubbed = not follow                      # tape wound into the past
            if scrubbed and ndl >= 126:
                chars[-1] = RED + '▶' + c              # live position: off-screen right
            elif ndl <= 1:
                chars[0] = RED + '◀' + c               # transcript start: beyond frame left
            else:
                ncol = min(len(chars)-1, int(ndl/127 * (len(chars)-1)))
                chars[ncol] = RED + '┃' + c            # red needle in frame
        if key == 'human':
            spark = c + meter_for(c) + ''.join(chars[METER_W:]) + RST   # meter covers oldest cells
        else:
            spark = c + ''.join(chars) + RST
        tgt = target(key)
        now_ = time.time()
        sv = sent.get(key)
        if _prev_sent.get(key) != sv and sv is not None:
            if _prev_sent.get(key) is not None: _lock_until[key] = now_ + 1.2
            _prev_sent[key] = sv
        fast = '⚡' if i == 0 else ''
        if key == 'human':
            eff = ''                            # meter slot carries REC state — no duplicate dot
        elif tgt and tgt != sv:                      # CHARGING: brief pulse, then rest
            if _tgt_seen.get(key) != tgt:
                _tgt_seen[key] = tgt; _tgt_t[key] = now_
            if now_ - _tgt_t.get(key, 0) < 3.0:
                ph = int(now_ * 10)
                bar = ''.join(CHARGE[(ph + j) % 8] for j in range(3))
                eff = f"{fast}\033[5m{bar}\033[25m{tgt[:4]}"
            else:
                eff = f"{fast}→{tgt[:5]}"
        elif now_ < _lock_until.get(key, 0):       # LOCK IN: inverse flash
            eff = f"{fast}\033[7m⟦{(sv or '')[:5].upper()}⟧\033[27m"
        else:
            eff = fast + (sv or '·')[:6]
        if key == 'human':
            human_cost = HUMAN_LEDGER['active_seconds'] / 3600 * HUMAN_RATE_USD
            curs = (
                f"{HUMAN_PROMPTS[0]}p ~${human_cost:.1f}"
                f"·${HUMAN_RATE_USD:.0f}/h"
            )
        else:
            c_ = cum['gpt' if _TYPE.get(HKEYS[i]) in ('codex', 'codex_app') else HKEYS[i]]
            curs = f"{kfmt(c_['tok'])}t ~${c_['usd']:,.2f}"
        name_cell = fit_cells(name, NAME_CELLS)
        effort_cell = fit_cells(eff, EFFORT_CELLS)
        ledger_cell = fit_cells(curs, LEDGER_CELLS, align='right')
        out.append(
            f"\033[K{c}{B}{name_cell}{RST}{DIM}{effort_cell}{RST}"
            f"{spark} {DIM}{ledger_cell}{RST}"
        )
    span = cols*z
    mode = 'LIVE' if follow else f'-{offset}s'
    legend = f" {z}s/col {mode}"
    legend_start = max(0, cols - len(legend))
    axis = list('─' * cols)
    step = max(1, cols // 5)                       # ~5 time ticks
    for ci in range(0, max(0, legend_start - 9), step):
        tt = t0 + ci * z
        lt = time.localtime(tt)
        today = time.localtime()
        fmt = '%H:%M:%S' if (lt.tm_yday == today.tm_yday and lt.tm_year == today.tm_year) else '%m-%d %H:%M'
        lab = '┴' + time.strftime(fmt, lt)
        for j, ch in enumerate(lab):
            if ci + j < cols: axis[ci + j] = ch
    for j, ch in enumerate(legend):
        p = legend_start + j
        if 0 <= p < cols: axis[p] = ch
    # The first axis cell and first tape cell share the same terminal column.
    axis_prefix = ' ' * (TAPE_COLUMN - 1) + '├'
    axis_suffix = '┤' + ' ' * LEDGER_CELLS
    out.append(f"\033[K{DIM}{axis_prefix}{''.join(axis)}{axis_suffix}{RST}")
    frame_s = '\n'.join(out) + '\033[J'
    if frame_s != getattr(render, '_last', None):
        render._last = frame_s
        with WLOCK:
            sys.stdout.write(frame_s); sys.stdout.flush()

def eq_painter():
    # pylon sync tier: repaint ONLY the 12 human meter cells at 120fps. Idle when static.
    hrow = next((i for i, (_, _, _, k) in enumerate(HEADS) if k == 'human'), None)
    while True:
        time.sleep(1/120)
        if hrow is None or getattr(render, '_last', None) is None: continue
        c = HEADS[hrow][2]
        seq = (
            f'\0337\033[{hrow+2};{TAPE_COLUMN+1}H'
            + c + meter_for(c) + RST + '\0338'
        )
        if seq == getattr(eq_painter, '_last', None): continue    # silence = zero writes
        eq_painter._last = seq
        with WLOCK:
            sys.stdout.write(seq); sys.stdout.flush()

def main():
    threading.Thread(target=sampler, daemon=True).start()
    threading.Thread(target=eq_painter, daemon=True).start()
    fd = sys.stdin.fileno(); old = termios.tcgetattr(fd)
    tty.setcbreak(fd)
    sys.stdout.write('\033[2J\033[?25l\033[?1000h\033[?1006h')  # mouse on
    zi, offset, follow = 2, 0, True
    cheat = False
    CHEAT = [' TMUX CHEAT SHEET (? to hide)   prefix = ctrl-b',
             '  detach: ⌃b d    attach: djclaude       switch pane: ⌃b ←→↑↓ (or click)',
             '  kill pane: ⌃b x    respawn dead pane: tmux respawn-pane -k -t djclaude:0.N "cmd"',
             '  zoom pane: ⌃b z    scrollback: ⌃b [ (q quits)    resize: ⌃b ⌥arrows',
             '  rig: djclaude --restart = rebuild · faders: Lvol=sonnet Rvol=gpt Rpitch=fable']
    try:
        while True:
            try: _tape = json.loads(STATE.read_text()).get('tape_off')
            except Exception: _tape = None
            if _tape is not None and _tape != getattr(main, '_last_tape', None):
                main._last_tape = _tape
                offset, follow = (_tape, False) if _tape > 0 else (0, True)
            w = os.get_terminal_size().columns
            render(w, zi, offset, follow)
            if cheat:
                sys.stdout.write('\n' + '\n'.join(f'\033[K\033[38;5;110m{l}\033[0m' for l in CHEAT))
                sys.stdout.flush()
            r,_,_ = select.select([fd],[],[],1/240)
            if not r: continue
            data = os.read(fd, 64).decode('latin1')
            for m in re.finditer(r'\x1b\[<(\d+);\d+;\d+[Mm]', data):
                btn = int(m.group(1))
                ctrl = btn & 16; up = (btn & 3 == 0) and btn & 64
                down = (btn & 3 == 1) and btn & 64
                if btn in (64,65) or up or down:
                    scroll_up = (btn & 1) == 0
                    if ctrl or btn in (80,81):   # ctrl-scroll: zoom
                        zi = max(0, zi-1) if scroll_up else min(len(ZOOMS)-1, zi+1)
                    else:                         # scroll: scrub
                        step = ZOOMS[zi]*5
                        offset = offset + step if scroll_up else max(0, offset-step)
                        follow = offset == 0
            if '?' in data:
                cheat = not cheat
                subprocess.run(['tmux','resize-pane','-t',f'{S}:0.5','-y',str(10 if cheat else 5)], check=False)
            if 'e' in data or '\x1b[F' in data: offset, follow = 0, True
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        sys.stdout.write('\033[?1000l\033[?1006l\033[?25h')

if __name__ == '__main__': main()
