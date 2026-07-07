import mido, time, collections
mido.set_backend('mido.backends.rtmidi')
seen = collections.OrderedDict()
with mido.open_input('Rane ONE') as port:
    end = time.time() + 30
    while time.time() < end:
        for m in port.iter_pending():
            if m.type == 'control_change':
                k = ('cc', m.channel, m.control)
                seen.setdefault(k, []).append(m.value)
            elif m.type in ('note_on','note_off'):
                seen.setdefault((m.type, m.channel, m.note), []).append(m.velocity)
            elif m.type == 'pitchwheel':
                seen.setdefault(('pitch', m.channel), []).append(m.pitch)
        time.sleep(0.005)
for k, v in seen.items():
    print(k, f"n={len(v)} min={min(v)} max={max(v)} first={v[0]} last={v[-1]}")
