# Hyundai Ioniq 2017 — CAN Reverse Engineering

For reference, the WiCAN firmware is checked out in the `wican-fw/` directory (gitignored; pull if you need to reference the latest version).

## Tools

- **`generate-profile.py`** — Generate WiCAN vehicle profiles from `ioniq-2017-pids.yaml`, upload/download/diff with device
- **`decode-captures.py`** — Decode captured UDS payloads using WiCAN expression evaluator (Python port of `expression_parser.c`)
- **`can-request.py`** — CLI tool for custom CAN/UDS requests via WiCAN WebSocket ELM327 terminal mode. Supports interactive REPL, `--param`, `--ecu`, `--raw`, `--scan` modes. **Use `--reboot` to restore AutoPID after session** (WebSocket terminal overrides AutoPID mode). Dependencies: `websockets`, `pyyaml`, `requests` (optional, for reboot).

## Key Files

- **`ioniq-2017-pids.yaml`** — SOURCE OF TRUTH for all PID definitions (211 parameters, 167 verified)
- **`captures.yaml`** — Raw UDS response payloads from capture sessions
- **`docs/wican-iso-tp-index-conversion.md`** — WiCAN vs ISO-TP vs Torque byte index mapping
- **`docs/CLI commands.md`** — Reference for `can-request.py` usage and examples

## WiCAN Access

- Home: `http://10.0.2.86` | VPN: `http://192.168.3.2`
- WebSocket terminal: `ws://<ip>/ws` (send `{"ws_mode": "terminal", "terminal_type": "elm327"}`)

## Ideas

- Use known PIDs to automatically deduce vehicle state to help understand new PIDs
