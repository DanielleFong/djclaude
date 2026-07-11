# djclaude latency ladder — measured 2026-07-07
| stage | before | after | note |
|---|---|---|---|
| tmux send (fork+exec) | 4.19 ms | — | replaced |
| tmux send (persistent -C pipe) | — | 0.5 µs | write+flush, measured n=200 |
| python string overhead | — | 0.4 µs | = total Rust headroom; not worth a rewrite |
| MIDI ingest | 10 ms poll | 2 kHz target direct poll | callback delivery regressed under Python 3.14/rtmidi; raw acquisition-to-receipt distribution is not yet established |
| daemon/dock loops | 100/30 Hz | 2 kHz / 240 Hz | MIDI loop target / dock paint target |
| stock Codex renderer | system HID / AX / DOM | CDP `mouseWheel` | pointer-safe visible motion accepted on build 26.707.31428; event→photon distribution not yet established |
| tmux escape-time | 500 ms | 0 | Esc handled instantly |
| remaining gates | | | correlated raw MIDI → CDP ack → captured frame; reversal, long rewind, scan strip, reconnect, and cross-build replication |
| unfixable | | | CC transcript scrolls page-wise; no line/pixel API |

## model-side, measured 2026-07-07 (single runs, 400-token generations)
| head·effort | ttft | gen tok/s | note |
|---|---|---|---|
| opus-4.8 | 0.96s | 47 | fast mode is harness-level; `speed` param rejected by API |
| fable low | 3.7s | 46 | ttft = adaptive thinking time |
| fable medium | 3.8s | 51 | |
| fable high | 4.1s | 42 | |
| fable xhigh | 5.1s | 62 | |
| fable max | 9.4s | ~42 effective | output arrives as post-think burst |
**finding: the effort fader buys THINKING TIME (ttft 3.7→9.4s), not typing speed (~45-60 tok/s flat).**
