#!/usr/bin/env python3
"""djclaude calibration wizard — maps ANY MIDI controller. Wiggle when prompted."""
import json, time, sys
import mido
mido.set_backend('mido.backends.rtmidi')
ports = mido.get_input_names()
print("MIDI inputs:", *(f"  {i}: {p}" for i, p in enumerate(ports)), sep="\n")
port_name = ports[int(input("pick your controller #: ") or 0)]
ROLES = [
 ("opus",   "LEFT pitch/effort fader (head 1 thinking)", "fader"),
 ("fable",  "RIGHT pitch/effort fader (head 2 thinking)", "fader"),
 ("jog_opus",  "LEFT platter / jog (scratch head 1)", "jog"),
 ("jog_fable", "RIGHT platter / jog (scratch head 2)", "jog"),
 ("strip_opus",  "LEFT needle/seek strip (skip if none)", "strip"),
 ("strip_fable", "RIGHT needle/seek strip (skip if none)", "strip"),
 ("crossfader", "crossfader (skip if none)", "fader"),
]
mapping = {"port": port_name, "controls": {},
           "efforts": ["low","medium","high","xhigh","max"],
           "gpt_efforts": ["none","low","medium","high","xhigh"]}
with mido.open_input(port_name) as port:
    for key, label, kind in ROLES:
        input(f"\n→ {label}\n  wiggle it for 2s then press ENTER (or just ENTER to skip)…")
        list(port.iter_pending())
        print("  listening 3s…"); time.sleep(0.2)
        seen = {}
        end = time.time() + 3
        while time.time() < end:
            for m in port.iter_pending():
                if m.type == 'control_change':
                    seen[(m.channel, m.control)] = seen.get((m.channel, m.control), 0) + 1
            time.sleep(0.005)
        if not seen: print("  (skipped)"); continue
        (ch, cc), n = max(seen.items(), key=lambda kv: kv[1])
        entry = {"channel": ch, "cc": cc}
        if kind == "jog": entry["mode"] = "jog"
        if kind == "strip": entry["mode"] = "strip"
        if kind == "fader" and key in ("opus", "fable"):
            entry["invert"] = (input("  is UP = less thinking? [Y/n] ").lower() != 'n')
        mapping["controls"][key] = entry
        print(f"  ✓ {key} = ch{ch} cc{cc} ({n} events)")
json.dump(mapping, open("mapping.json", "w"), indent=2)
print("\n🎛️  mapping.json written — run ./djclaude")
