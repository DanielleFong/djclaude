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
| **platters** | scratch through the head's transcript. 1 tick = one 3-line step (tune: NOTCH). Hard edge walls, zero buffers, motor-braking + release-rebound deadbands |
| **needle strips** | ABSOLUTE map of the whole transcript: left tip = first message, right tip = LIVE. Touch = glide there at 120 pages/s (seek pacer). Tips snap; edge-hold auto-crawls; pull past the top and the TAPE scrubs |
| **crossfader** | displayed; routing reserved |
| **play/motor** | ignored by default (tune: toggle) — platters are scratch instruments |

## The dock (bottom pane)
Per-head rows: dithered braille activity tape (DSD-style, time-anchored — no shimmer),
live effort label, orange needle ┃ (▶/◀ arrows when off-frame), cumulative ledger in
tokens + ~dollars parsed from real transcripts, $burst/total labels, wall-clock tick
marks. Ctrl-scroll = zoom · scroll = scrub · `e` = LIVE · `?` = tmux cheat sheet.

## The heads talk back
`deckctl.py attention` — the motorized platter physically JOLTS. Heads are taught to
post to slack.md first and jolt only for operator-worthy events. Also play/cue/browse/load
via the `djclaude-ctl` virtual MIDI device (map once in your DJ software's MIDI-learn).

## Tuning & calibration
- `http://127.0.0.1:7683/tune` — NOTCH, motor-ignore, per-head SPAN (transcript height)
- `./calibrate.py` — guided wizard maps ANY MIDI controller (wiggle prompts → mapping.json)
- `LATENCY-LADDER.md` — measured: 0.5µs sends, event-driven ingest, vsync-bound

## Anywhere
`djclaude` attaches or builds; `--restart` rebuilds; `--quartet` = 4 heads + shared slack
buffer. Phone: ttyd + cloudflared behind a WorkOS gateway with per-user machine grants.

## Billing honesty
Heads bill whatever auth you wire (subscription OAuth, API keys). Fable at max effort is
a firehose — the dock ledger exists so you SEE it. Read `djclaude` before running.
