#!/usr/bin/env python3
"""Summarize event-to-frame scrub latency without inflating tail claims."""

import argparse
import json
import math
from pathlib import Path


PERCENTILES = (0.5, 0.95, 0.99, 0.999, 0.9999)


def percentile(values, fraction):
    if not values:
        return None
    rank = max(1, math.ceil(len(values) * fraction))
    return values[rank - 1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "path",
        nargs="?",
        default="/tmp/dj-scrub-stock-cdp.jsonl",
    )
    arguments = parser.parse_args()

    samples = []
    statuses = {}
    for line in Path(arguments.path).read_text().splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        status = record.get("status", "unknown")
        statuses[status] = statuses.get(status, 0) + 1
        latency = record.get("capture_callback_ms")
        if status == "observed" and isinstance(latency, (int, float)):
            samples.append(float(latency))

    samples.sort()
    result = {
        "metric": "screen_capture_event_to_changed_frame",
        "n": len(samples),
        "statuses": statuses,
        "p99_99_qualified": len(samples) >= 10_000,
        "minimum_recommended_n": 10_000,
        "mean_ms": sum(samples) / len(samples) if samples else None,
        "max_ms": samples[-1] if samples else None,
    }
    for fraction in PERCENTILES:
        label = str(fraction * 100).rstrip("0").rstrip(".").replace(".", "_")
        result[f"p{label}_ms"] = percentile(samples, fraction)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
