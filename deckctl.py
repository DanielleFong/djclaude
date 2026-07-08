#!/usr/bin/env python3
"""deckctl — let the heads press deck buttons via a virtual MIDI device.
Usage: deckctl.py <action>  ·  actions emit notes on 'djclaude-ctl' (map them in
Serato: Settings->MIDI, click a Serato control, trigger the action to learn).
  play-left(n60) play-right(n61) cue-left(n62) cue-right(n63)
  browse-up(n64) browse-down(n65) load-left(n66) load-right(n67)
  attention == play-left ON/OFF twice (motor jolt: look at the decks!)
"""
import sys, time
import mido
mido.set_backend('mido.backends.rtmidi')
NOTES = {'play-left':60,'play-right':61,'cue-left':62,'cue-right':63,
         'browse-up':64,'browse-down':65,'load-left':66,'load-right':67}
def tap(port, note, hold=0.05):
    port.send(mido.Message('note_on', note=note, velocity=127))
    time.sleep(hold)
    port.send(mido.Message('note_off', note=note))
def main():
    act = sys.argv[1] if len(sys.argv) > 1 else ''
    with mido.open_output('djclaude-ctl', virtual=True) as p:
        time.sleep(0.4)   # let CoreMIDI clients notice the source
        if act == 'attention':
            for _ in range(2): tap(p, NOTES['play-left']); time.sleep(0.35)
        elif act in NOTES:
            tap(p, NOTES[act])
        else:
            print(__doc__); return 1
        time.sleep(0.2)
if __name__ == '__main__': sys.exit(main() or 0)
