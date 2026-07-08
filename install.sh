#!/bin/bash
# djclaude v0.1 installer — DJ your AI models.
set -euo pipefail
echo "🎛️  djclaude v0.1"
command -v tmux >/dev/null || { echo "need tmux (brew install tmux)"; exit 1; }
command -v python3 >/dev/null || { echo "need python3"; exit 1; }
cd "$(dirname "$0")"
python3 -m venv .venv && ./.venv/bin/pip install -q mido python-rtmidi
echo "→ deps installed"
echo "→ plug in your controller, then run the MIDI-learn to map YOUR faders:"
echo "   ./.venv/bin/python learn2.py   # wiggle controls, edit mapping.json"
echo "→ edit djclaude launcher: point head commands at your own claude/codex setups"
echo "→ launch: ./djclaude"
echo "docs & demo: https://djclaude.cc · repo: github.com/DanielleFong/djclaude"
