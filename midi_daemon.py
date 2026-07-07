#!/usr/bin/env python3
"""Rane ONE -> Claude/Codex tmux rig (djclaude).
  right pitch fader (ch1 cc9, up=low)  -> FABLE  /effort low..max
  left  volume      (ch0 cc28)         -> SONNET /effort low..max
  right volume      (ch1 cc28)         -> GPT5.5 codex /model picker digit 1..4
  left pitch, crossfader               -> display only (phase 2)
State -> /tmp/dragons-state.json for statusbar.py.
"""
import json, time, subprocess, pathlib

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
SETTLE = 0.3
HB_PERIOD = 90   # seconds per heartbeat
HB_MSG = 'hb: read slack.md; if you have an active task continue it, else assist another head. brief.'

state = {n: 0 for n in CFG['controls']}
state.update({'sent': {}})

def write_state(): STATE_FILE.write_text(json.dumps(state))

def detent(v, levels): return min(len(levels)-1, v*len(levels)//128)

def tmux(*args): subprocess.run(['tmux', *args], check=False)

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

def main():
    ctl = {(c['channel'], c['cc']): (n, c.get('invert', False))
           for n, c in CFG['controls'].items()}
    pending = None
    hb_last = time.time(); hb_phase = 0
    last_raw, jump_cand = {}, {}   # spurious-jump guard (Serato sync snaps pitch)
    with mido.open_input(CFG['port']) as port:
        print(f"listening on {CFG['port']}", flush=True)
        write_state()
        while True:
            moved = False
            for m in port.iter_pending():
                if m.type != 'control_change': continue
                nc = ctl.get((m.channel, m.control))
                if not nc: continue
                name, inv = nc
                raw = m.value
                lr = last_raw.get(name)
                if lr is not None and abs(raw - lr) > 40:
                    # big jump: require a second nearby event to confirm (real sweeps
                    # send dense streams; Serato sync snaps send isolated extremes)
                    jc = jump_cand.get(name)
                    if jc is None or abs(raw - jc) > 25:
                        jump_cand[name] = raw
                        continue
                jump_cand.pop(name, None)
                last_raw[name] = raw
                v = 127 - raw if inv else raw
                if state.get(name) != v:
                    state[name] = v; moved = True
            if moved:
                pending = time.time(); write_state()
            if pending and time.time() - pending > SETTLE:
                pending = None
                send('opus', detent(state['opus'], OPUS_EFFORTS))
                send('fable', detent(state['fable'], EFFORTS))
                send('sonnet', detent(state['sonnet'], EFFORTS))
                send('gpt', detent(state['gpt'], GPT_EFFORTS))
                write_state()
            now = time.time()
            if now - hb_last > HB_PERIOD:
                hb_last = now
                x = state.get('crossfader', 64) / 127   # 0=left deck, 1=right deck
                hb_phase += 1
                left = (hb_phase % 4) / 4 >= x          # duty-cycle split
                L = [h for h in ('opus','sonnet') if h in PANES] or ['opus']
                R = [h for h in ('gpt','fable') if h in PANES] or ['fable']
                head = L[hb_phase % len(L)] if left else R[hb_phase % len(R)]
                tmux('send-keys', '-t', PANES[head], HB_MSG, 'Enter')
                if head == 'gpt':
                    time.sleep(0.8); tmux('send-keys', '-t', PANES['gpt'], 'Enter')
                with open(HERE / 'slack.md', 'a') as f:
                    f.write(f"[{time.strftime('%H:%M:%S')}] rig: ♥ heartbeat -> {head} (x={x:.2f})\n")
            time.sleep(0.01)

if __name__ == '__main__':
    main()
