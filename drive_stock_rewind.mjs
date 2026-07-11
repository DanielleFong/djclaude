#!/usr/bin/env node
/** Bounded software platter gesture for stock-renderer acceptance testing. */

import dgram from "node:dgram";

const bridge = dgram.createSocket("udp4");
const probe = dgram.createSocket("udp4");
const direction = Number(process.argv[2] ?? -1) < 0 ? -1 : 1;
const durationMs = Math.max(100, Number(process.argv[3] ?? 2500));
const intervalMs = 4;
const startedAt = performance.now();
let sequence = 0;

const timer = setInterval(() => {
  const elapsed = performance.now() - startedAt;
  if (elapsed >= durationMs) {
    clearInterval(timer);
    bridge.close();
    probe.close();
    console.log(JSON.stringify({ direction, durationMs, events: sequence }));
    return;
  }

  sequence += 1;
  const monotonicNs = Number(process.hrtime.bigint());
  const event = Buffer.from(JSON.stringify({
    type: "jog",
    head: "opus",
    delta: direction,
    monotonic_ns: monotonicNs,
    unix_ns: String(BigInt(Math.round((performance.timeOrigin + performance.now()) * 1_000_000))),
    synthetic: true,
  }));
  bridge.send(event, 17685, "127.0.0.1");

  // The screen probe accepts one outstanding trigger and ignores the rest.
  const measurement = Buffer.from(JSON.stringify({
    seq: sequence,
    head: "opus",
    steps: direction,
    monotonic_ns: monotonicNs,
  }));
  probe.send(measurement, 17683, "127.0.0.1");
}, intervalMs);
