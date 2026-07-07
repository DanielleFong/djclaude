#!/usr/bin/env python3
"""djclaude dock: one line, effort levels. 30fps, redraw on change."""
import json, sys, time, pathlib

STATE = pathlib.Path('/tmp/dragons-state.json')
DIM, RST, B = '\033[2m', '\033[0m', '\033[1m'
HEADS = [('OPUS','\033[38;5;203m',None), ('SONNET','\033[38;5;44m','sonnet'),
         ('GPT','\033[38;5;114m','gpt'), ('FABLE','\033[38;5;220m','fable')]
BLK = ' ⡀⡄⡆⡇⣇⣧⣷⣿'
def bar(v, w=8, c=''):
    f = max(0.0,min(1.0,v))*w
    return c + ''.join(BLK[round(max(0.0,min(1.0,f-i))*8)] for i in range(w)) + RST
def xf(v, w=13):
    p = round(v/127*(w-1))
    return f"{DIM}{'⣀'*p}{RST}\033[38;5;196m⣿{RST}{DIM}{'⣀'*(w-1-p)}{RST}"

sys.stdout.write('\033[2J\033[?25l')
last = None
while True:
    try: s = json.loads(STATE.read_text())
    except Exception: s = {}
    sent = s.get('sent', {})
    parts = []
    for name, c, key in HEADS:
        if key is None:
            parts.append(f"{c}{B}{name}{RST} {DIM}⚡nothink{RST}")
        else:
            parts.append(f"{c}{B}{name}{RST} {bar(s.get(key,0)/127,8,c)} {sent.get(key) or '·':<6}")
    line = ('\033[H\033[K ' + f' {DIM}│{RST} '.join(parts)
            + f" {DIM}│{RST} X {xf(s.get('crossfader',64))}")
    if line != last:
        sys.stdout.write(line); sys.stdout.flush(); last = line
    time.sleep(1/30)
