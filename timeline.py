#!/usr/bin/env python3
"""djclaude dock: per-head spend/activity timeline. Zoom: ctrl-scroll. Pan/scrub: scroll.
End/e = live-follow. Braille columns, 1Hz sampler, 30fps renderer."""
import json, os, re, select, subprocess, sys, termios, time, tty, pathlib, threading

S = 'djclaude'
STATE = pathlib.Path('/tmp/dragons-state.json')
LOG = pathlib.Path('/tmp/dragons-timeline.jsonl')
try:
    _LAY = json.loads(pathlib.Path('/tmp/dragons-layout.json').read_text())
except Exception:
    _LAY = {'opus': f'{S}:0.0', 'fable': f'{S}:0.1'}
_ALL = [('OPUS','opus','\033[38;5;203m'), ('SONNET','sonnet','\033[38;5;44m'),
        ('GPT','gpt','\033[38;5;114m'), ('FABLE','fable','\033[38;5;220m')]
HEADS = [(n, _LAY[k], c, k) for n, k, c in _ALL if k in _LAY]
DIM, RST, B = '\033[2m', '\033[0m', '\033[1m'
series = {}   # t -> [act0..act3, spend0..spend3]
events = []   # {'t','head','cost','total'} emitted at burst end
totals = [0.0]*4
lock = threading.Lock()
ACT_T = 40    # activity threshold for burst detection
UNIT = ['$', 'tok', 'ctx', 'tok']

def kfmt(v):
    if v >= 1e6: return f"{v/1e6:.1f}M"
    return f"{v/1000:.1f}k" if v >= 1000 else f"{v:.0f}"


def pane(t):
    r = subprocess.run(['tmux','capture-pane','-p','-t',t], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ''


# --- cumulative token/dollar ledger from real transcripts ---
CLPROJ = pathlib.Path.home() / ".claude/projects/-Users-daniellefong-cc-rane-claude"
CODEX_S = pathlib.Path.home() / ".codex/sessions"
PRICE = {"opus": (15, 75, 1.5, 18.75), "sonnet": (3, 15, 0.3, 3.75),
         "fable": (15, 75, 1.5, 18.75), "gpt": (1.25, 10, 0.125, 1.25)}  # ~$/Mtok in,out,cr,cw
HKEYS = tuple(k for _,_,_,k in HEADS)
cum = {h: {"tok": 0.0, "usd": 0.0} for h in ('opus','sonnet','gpt','fable')}
_off = {}
def ledger_tick():
    for f in CLPROJ.glob("*.jsonl"):
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
                    cum[h]["usd"] += (i*pr[0] + out*pr[1] + cr*pr[2] + cw*pr[3]) / 1e6
                _off[f] = fh.tell()
        except Exception: pass
    try:
        f = max(CODEX_S.glob("*/*/*/*.jsonl"), key=lambda x: x.stat().st_mtime)
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
            try: o = json.loads(ln); series[o['t']] = o['v']
            except Exception: pass
    while True:
        t = int(time.time()); v = []
        spends = []
        for i,(_, p, _, _) in enumerate(HEADS):
            txt = pane(p)
            act = 0 if txt == prev[i] else sum(a!=b for a,b in zip(txt.ljust(4000), prev[i].ljust(4000)))
            prev[i] = txt; v.append(min(act, 2000))
            spends.append(None)  # ledger owns spend now
        for i in range(N):
            act, sp = v[i], spends[i]
            b = burst[i]
            if act > ACT_T:
                if b is None:
                    burst[i] = {'start_spend': cum[HKEYS[i]]['usd'], 'acc': act, 'idle': 0}
                else:
                    b['acc'] += act; b['idle'] = 0
            elif b is not None:
                b['idle'] += 1
                if b['idle'] >= 3 and b['acc'] > 300:   # burst over (ignore repaint blips)
                    cost = max(0.0, cum[HKEYS[i]]['usd'] - b['start_spend'])
                    with lock:
                        totals[i] += cost
                        events.append({'t': t, 'head': i, 'cost': cost, 'total': totals[i]})
                    burst[i] = None
        with lock:
            series[t] = v + spends
            with open(LOG,'a') as f: f.write(json.dumps({'t':t,'v':v+spends})+'\n')
        ledger_tick()
        time.sleep(1.0)

ZOOMS = [1,2,5,10,30,60]  # sec per column
def render(w, zi, offset, follow):
    now = int(time.time()); z = ZOOMS[zi]
    label_w = 16; cols = max(10, w - label_w - 22)
    end = now if follow else now - offset
    t0 = end - cols*z
    with lock: snap = dict(series)
    try: sent = json.loads(STATE.read_text()).get('sent', {})
    except Exception: sent = {}
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
            rot = (ci * 3) % 8                     # per-column rotation -> texture
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
        spark = c + ''.join(chars) + RST
        eff = ('⚡' if i == 0 else '') + (sent.get(key) or '·')[:6]
        c_ = cum[HKEYS[i]]
        curs = f"{kfmt(c_['tok'])}t ~${c_['usd']:,.2f}"
        out.append(f"\033[K{c}{B}{name:<7}{RST}{DIM}{eff:<8}{RST}{spark} {DIM}{curs:>16}{RST}")
    span = cols*z
    mode = 'LIVE' if follow else f'-{offset}s'
    legend = f" {z}s/col {span//60}m{span%60:02d}s {mode}"
    axis = f"├{'─'*max(0,cols-2-len(legend))}{legend}┤"
    out.append(f"\033[K{DIM}{'':<{label_w}}{axis}{RST}")
    sys.stdout.write('\n'.join(out) + '\033[J'); sys.stdout.flush()

def main():
    threading.Thread(target=sampler, daemon=True).start()
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
            w = os.get_terminal_size().columns
            render(w, zi, offset, follow)
            if cheat:
                sys.stdout.write('\n' + '\n'.join(f'\033[K\033[38;5;110m{l}\033[0m' for l in CHEAT))
                sys.stdout.flush()
            r,_,_ = select.select([fd],[],[],1/30)
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
            if 'q' in data: break
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        sys.stdout.write('\033[?1000l\033[?1006l\033[?25h')

if __name__ == '__main__': main()
