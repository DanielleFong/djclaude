# djclaude 🎛️🐉

DJ a multi-model AI rig with a real DJ controller. A Rane ONE's faders drive the
reasoning effort of four heads running side by side in tmux — while Serato plays.

```
OPUS 4.8 (fast, nothink) │ SONNET 5 (nothink) │ shared buffer │ GPT 5.5 (codex) │ FABLE 5
─────────────────────────────────────────────────────────────────────────────────────────
      spend timeline dock: per-head braille sparklines, $burst/total labels,
      ctrl-scroll zoom, scroll scrub, `?` tmux cheat sheet
```

- **Left volume fader** → Sonnet effort (low→max)
- **Right volume fader** → GPT-5.5 reasoning (none→xhigh, driven through codex's model picker)
- **Right pitch fader** → Fable effort (up=low, down=max)
- **Crossfader** → displayed; head routing planned
- Effort changes are prompt-cache-safe (verified against the Anthropic API)
- Spurious-jump guard filters Serato sync snaps so DJing doesn't scramble your efforts
- MIDI is polled at a 2 kHz target cadence; this avoids a Python 3.14/rtmidi
  callback regression. End-to-end display latency is qualified separately.

## Pieces
- `djclaude` — launcher (`djclaude` attach-or-build, `--restart`, `--fresh`; `djfreshclaude` = thin-harness variant)
- `midi_daemon.py` — MIDI CC → deck effort + scroll transports
- `codex_scroll.swift` — persistent native pixel scrolling for a Codex app deck
- `codex_cdp_bridge.mjs` — pointer-safe stock Codex renderer wheel transport
- `timeline.py` — the dock: 1Hz spend sampler + zoomable braille timeline
- `mapping.json` — CC map (use the MIDI-learn snippets in git history for other controllers)
- `rig-context.md` — shared context: head roles + append-only slack protocol

Billing note: heads run on whatever auth their launch wrapper resolves — read
`djclaude` before running; it assumes some personal wrappers you'll want to replace.

Born live on a Friday, faders side by side with Serato. PRs welcome.

## Codex app deck

The working stock-app route is `scroll="codex_cdp"`. Start Codex with its debugging
endpoint bound to loopback, then run the bridge with the exact visible task title:

```sh
/Applications/ChatGPT.app/Contents/MacOS/ChatGPT \
  --remote-debugging-address=127.0.0.1 \
  --remote-debugging-port=9229

node codex_cdp_bridge.mjs \
  --port 9229 \
  --thread-title "Your exact task title"
```

The bridge requires exactly one visible toolbar title match and one
`thread-scroll-container`, then sends Chromium `Input.dispatchMouseEvent` wheel
events at that element. Those coordinates stay inside the renderer: no macOS mouse
event is created, the physical pointer does not move, and the app's real wheel and
virtualization handlers run. The bridge fails closed when identity is ambiguous.

The hot path is ack-paced: one renderer packet may be in flight while later platter
ticks coalesce into the next packet. Target checks and receipt I/O run off-path.
`/tmp/dj-sol-cdp-metrics.json` reports source→ack, bridge-ingress→ack, and CDP-ack
p50/p95/p99/p99.9/p99.99 histograms. A p99.99 value is explicitly unqualified until
the histogram contains at least 10,000 samples. Use `summarize_scrub_metrics.py` on
the ScreenCaptureKit JSONL to report the separate event→changed-frame distribution.

Direct DOM `scrollTop` mutation, system HID routing, and the Accessibility path are
not the platter transport: the first is undone by the stock virtualizer, the second
can capture the operator's pointer, and the third is a slower semantic fallback.
Stock-app scan-strip seeking remains a candidate pending the same visual acceptance
test as the platter. Set a deck to `tmux` when the CLI transcript is the target.

See `DEVLOG-2026-07-11-POINTER-SAFE-CODEX.md` for the experiment ladder and claim
boundary.
