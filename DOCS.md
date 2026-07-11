# djclaude — the manual (one page)

## What it is
A DJ controller is the control surface for a multi-model AI rig in tmux. Heads (Claude
Opus/Fable, optionally Sonnet + GPT/codex) run side by side; your hands set how hard
they think and scrub through everything they've ever said. While Serato (or nothing) plays.

## Controls
| control | action |
|---|---|
| **pitch faders** | reasoning effort per head: NOTHINK → low → medium → high → xhigh → max. 1.5s relaxation: sweep freely, the resting detent sends once. Dock shows charge-up ⣇⣧⣷ then ⟦LOCK IN⟧ |
| **volume faders** | effort for the extra heads (sonnet / gpt) in `--quartet` |
| **platters** | scratch through the head's transcript. Terminal decks use tmux wheel events; the accepted stock Codex route uses renderer-local Chromium wheel input. Hard edge walls, zero buffers, motor-braking + release-rebound deadbands |
| **needle strips** | ABSOLUTE map for terminal transcripts: left tip = first message, right tip = LIVE. Stock Codex absolute seek remains a candidate until it passes visual acceptance independently of the platter |
| **crossfader** | displayed; routing reserved |
| **play/motor** | ignored by default (tune: toggle) — platters are scratch instruments |

## The dock (bottom pane)
Per-head rows: dithered braille activity tape (DSD-style, time-anchored — no shimmer),
live effort label, orange needle ┃ (▶/◀ arrows when off-frame), cumulative ledger in
tokens + ~dollars parsed from real transcripts, $burst/total labels, wall-clock tick
marks. Ctrl-scroll = zoom · scroll = scrub · `e` = LIVE · `?` = tmux cheat sheet.
Bottom row is the pink HUMAN tape: real operator prompt events + live mic, with the
pylon-ported 16-band braille EQ (48kHz, 8ms hops = 125 band-frames/s, 120fps eased
region painter, peak-hold shadows) riding the tape head in LIVE mode.

## The heads talk back
`deckctl.py attention` — the motorized platter physically JOLTS. Heads are taught to
post to slack.md first and jolt only for operator-worthy events. Also play/cue/browse/load
via the `djclaude-ctl` virtual MIDI device (map once in your DJ software's MIDI-learn).

## Tuning & calibration
- `http://127.0.0.1:7683/tune` — NOTCH, motor-ignore, per-head SPAN (transcript height)
- `./calibrate.py` — guided wizard maps ANY MIDI controller (wiggle prompts → mapping.json)
- `LATENCY-LADDER.md` — measured and explicitly unmeasured latency stages

## Codex app transport
For the accepted stock-app route, configure the Sol head with
`scroll="codex_cdp"`. `codex_cdp_bridge.mjs` connects to a loopback-only Chromium
debugging endpoint, proves one exact visible task title and one transcript container,
then dispatches `mouseWheel` input inside the renderer. It never moves the system
pointer or changes focus. The stock wheel and virtualization handlers receive the
event; direct `scrollTop` mutation does not satisfy this invariant.

`codex_scroll.swift` remains a semantic Accessibility fallback and verifier, not the
tactile platter route. See `README.md` for startup and
`DEVLOG-2026-07-11-POINTER-SAFE-CODEX.md` for the evidence ladder.

Override `LEFT_SCROLL_BACKEND=tmux` or `RIGHT_SCROLL_BACKEND=tmux` in the rig
file when the Codex CLI pane, rather than the desktop app, should receive the jog.

## Anywhere
`djclaude` attaches or builds; `--restart` rebuilds; `--quartet` = 4 heads + shared slack
buffer. Phone: ttyd + cloudflared behind a WorkOS gateway with per-user machine grants.

## Billing honesty
Heads bill whatever auth you wire (subscription OAuth, API keys). Fable at max effort is
a firehose — the dock ledger exists so you SEE it. Read `djclaude` before running.
