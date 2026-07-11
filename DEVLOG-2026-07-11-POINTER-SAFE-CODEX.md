# Pointer-safe platter control for the stock Codex app

**Date:** 2026-07-11
**Claim:** signal observed on ChatGPT Codex desktop build `26.707.31428`
**Not yet claimed:** replicated cross-build support or established event-to-photon latency

We made a Rane ONE platter scrub the visible stock Codex transcript without moving
the macOS pointer or stealing focus. The winning architecture is:

```text
Rane MIDI
  → local MIDI daemon
  → loopback UDP
  → persistent CDP websocket
  → Chromium Input.dispatchMouseEvent(type: "mouseWheel")
  → exact .thread-scroll-container
```

## Why the earlier routes failed

1. **System mouse events:** visibly scroll, but follow/capture the operator's pointer.
2. **Accessibility:** semantically addressable, but slow and discontinuous for a
   platter.
3. **Direct `scrollTop`:** reports a transient numeric change that the stock
   virtualizer can undo without visible motion.
4. **Chromium wheel input:** runs the stock app's real wheel and virtualization
   handlers while leaving the operating-system pointer untouched.

An apparent early success was confounded by the operator using her ordinary scroll
wheel. That correction mattered: visual evidence outranked our receipt and exposed
the direct-DOM false positive.

## The renderer-native move

The bridge binds only to a debugging endpoint on `127.0.0.1`, finds exactly one
visible toolbar title and exactly one transcript container, and dispatches:

```js
await cdp.send("Input.dispatchMouseEvent", {
  type: "mouseWheel",
  x: transcriptRect.centerX,
  y: transcriptRect.centerY,
  deltaX: 0,
  deltaY: platterDelta * 4,
  pointerType: "mouse",
});
```

The `x` and `y` values belong to Chromium's input router. They do not become a
macOS mouse move, click, or focus event. This preserves the human's parallel work
while delivering the event shape the stock virtualized transcript expects.

## Safety invariants

- loopback debugging endpoint only;
- one exact visible task-title match;
- one exact transcript-container match;
- no pointer, click, focus, or keyboard API in the platter path;
- ambiguous identity fails closed;
- Sol and Fable transports remain independent.

## Next proof

Correlate raw MIDI timestamps, CDP acknowledgement, and captured display frames.
Sweep slow touch, direction reversal, long rewind through virtualized history,
scan-strip absolute seek, motor rotation, reconnect, and task switching. The target
is one display interval with correct polarity and no one-page virtual wall.

The bridge already maintains fixed-resolution cumulative histograms for
source→CDP acknowledgement, bridge-ingress→CDP acknowledgement, and CDP dispatch
acknowledgement. It publishes p50, p95, p99, p99.9, and p99.99 once per second,
outside the input path. p99.99 remains marked unqualified below 10,000 observations.
First-photon results remain a distinct ScreenCaptureKit distribution: a fast
renderer acknowledgement is never reported as a rendered frame.

Sol held the renderer lane, Fable kept the coordination deck and booth evolving,
and Danielle rejected the false visual evidence until the instrument felt right.
The acceptance phrase was concise: **IT WORKS.**
