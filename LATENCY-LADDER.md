# djclaude latency ladder — measured 2026-07-07
| stage | before | after | note |
|---|---|---|---|
| tmux send (fork+exec) | 4.19 ms | — | replaced |
| tmux send (persistent -C pipe) | — | 0.5 µs | write+flush, measured n=200 |
| python string overhead | — | 0.4 µs | = total Rust headroom; not worth a rewrite |
| MIDI ingest | 10 ms poll | event callback | mido rtmidi cb -> deque |
| daemon/dock loops | 100/30 Hz | 240 Hz | outruns both displays (175/240Hz) |
| tmux escape-time | 500 ms | 0 | Esc handled instantly |
| remaining gates | | | tmux server cmd handling ~0.3ms · CC page repaint · Ghostty vsync (4.2/5.7ms frame) |
| unfixable | | | CC transcript scrolls page-wise; no line/pixel API |
