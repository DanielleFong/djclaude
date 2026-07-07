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

## Pieces
- `djclaude` — launcher (`djclaude` attach-or-build, `--restart`, `--fresh`; `djfreshclaude` = thin-harness variant)
- `midi_daemon.py` — MIDI CC → detents → tmux send-keys
- `timeline.py` — the dock: 1Hz spend sampler + zoomable braille timeline
- `mapping.json` — CC map (use the MIDI-learn snippets in git history for other controllers)
- `rig-context.md` — shared context: head roles + append-only slack protocol

Billing note: heads run on whatever auth their launch wrapper resolves — read
`djclaude` before running; it assumes some personal wrappers you'll want to replace.

Born live on a Friday, faders side by side with Serato. PRs welcome.
