#!/bin/bash
# Launch the dragons tmux rig: Sonnet | Fable, statusbar below, MIDI daemon.
set -e
cd "$(dirname "$0")"
CLAUDE=/Users/daniellefong/.local/bin/claude
tmux kill-session -t dragons 2>/dev/null || true
tmux new-session -d -s dragons -x 220 -y 60 "$CLAUDE --model claude-sonnet-5"
tmux split-window -h -t dragons:0 "$CLAUDE --model 'claude-fable-5[1m]'"
tmux split-window -v -f -l 5 -t dragons:0 "./.venv/bin/python statusbar.py"
./.venv/bin/python midi_daemon.py > daemon.log 2>&1 &
echo $! > daemon.pid
echo "dragons rig up. attach with: tmux attach -t dragons  (daemon pid $(cat daemon.pid))"
