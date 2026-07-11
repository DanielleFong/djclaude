#!/usr/bin/env python3
"""Capture raw Rane ONE MIDI for short, labeled calibration gestures."""

import argparse
import json
import time

import mido


def serialize(message, elapsed):
    record = {
        "elapsed_ms": round(elapsed * 1_000, 3),
        "type": message.type,
    }
    for field in ("channel", "control", "value", "note", "velocity", "pitch"):
        if hasattr(message, field):
            record[field] = getattr(message, field)
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=12)
    parser.add_argument("--output", default="/tmp/rane-midi-capture.jsonl")
    arguments = parser.parse_args()

    mido.set_backend("mido.backends.rtmidi")
    started_at = time.monotonic()
    deadline = started_at + arguments.seconds
    with mido.open_input("Rane ONE") as port, open(arguments.output, "w") as output:
        while time.monotonic() < deadline:
            for message in port.iter_pending():
                elapsed = time.monotonic() - started_at
                output.write(json.dumps(serialize(message, elapsed)) + "\n")
            output.flush()
            time.sleep(0.0005)


if __name__ == "__main__":
    main()
