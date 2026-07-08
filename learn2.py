import mido, time
mido.set_backend('mido.backends.rtmidi')
with mido.open_input('Rane ONE') as port, open('midi-log.txt','w',buffering=1) as f:
    end = time.time() + 180
    while time.time() < end:
        for m in port.iter_pending():
            f.write(f"{time.time():.2f} {m}\n")
        time.sleep(0.005)
