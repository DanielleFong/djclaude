#!/bin/bash
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SESSION="dj-midi-daemon"

pkill -f "midi_daemon.py" 2>/dev/null || true
sleep 0.25

# Older launchers sometimes started the same daemon with a relative path, so
# the path-specific process match above could leave its HTTP sidecar behind.
while read -r listener_pid; do
  [[ -n "$listener_pid" ]] && kill "$listener_pid" 2>/dev/null || true
done < <(lsof -t -iTCP:7683 -sTCP:LISTEN 2>/dev/null || true)
sleep 0.25

tmux kill-session -t "$SESSION" 2>/dev/null || true
tmux new-session -d -s "$SESSION" \
  "cd '$HERE' && exec .venv/bin/python midi_daemon.py >> daemon.log 2>&1"
tmux display-message -p -t "$SESSION" '#{pane_pid}' > "$HERE/daemon.pid"

echo "djclaude MIDI daemon restarted in tmux session: $SESSION"
