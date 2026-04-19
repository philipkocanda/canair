# Hyundai Ioniq 2017 — CAN Reverse Engineering

For reference, the WiCAN firmware is checked out in the `wican-fw/` directory (gitignored; pull if you need to reference the latest version).

## Tools

- **`generate-profile.py`** — Generate WiCAN vehicle profiles from `pids/`, upload/download/diff with device
- **`canreq.py`** — CLI tool for custom CAN/UDS requests via WiCAN WebSocket ELM327 terminal mode. **Prefer `--multi "query ..."` for decoded output** (handles sessions, wake, keepalives). Use `--param`/`--ecu` for simple single-ECU reads, `--scan` for discovery. **`--raw` is last resort** (hex dump only, no decoding). `--verbose` is for debugging canreq itself, not normal use. **Use `--reboot` to restore AutoPID after session** (WebSocket terminal overrides AutoPID mode). Dependencies: `websockets`, `pyyaml`, `requests` (optional, for reboot).
- **`wican.py`** — WiCAN device management CLI. Subcommands: `config` (view/save device config), `sleep` (toggle sleep mode, set voltage/timing), `status` (device status summary), `protocol` (switch CAN protocol: auto_pid/slcan/elm327/savvycan/realdash66), `logs` (list/download/query SD card OBD log databases), `reboot`. Uses `--wican home|vpn|<url>` for address selection. Dependencies: `requests`.
- **`query-captures.py`** — Query captured UDS payloads across all capture files. Use after adding new captures to spot patterns. Modes: `--ecu ECU --pid PID` (combined, most useful), `--ecu ECU` (all captures for ECU), `--summary` (stats per ECU/date), `--latest [ECU]` (most recent payload per PID), `--diff ECU PID` (byte-level diff across captures).
- **`decode.py`** — Decode captured payloads using PID parameter definitions. Applies WiCAN expressions to all historical captures and shows decoded values. Use to validate expressions and spot anomalies. Modes: `decode.py BMS 2101` (full table), `--param SOC_BMS` (filter params), `--compact` (one-liner per capture), `--verified`/`--unverified`, `--json`, `--raw`.

## Key Files

- **`pids/`** — SOURCE OF TRUTH for all PID definitions, split by ECU (220 parameters, 192 verified). Validate with `python3 validate-pids.py`
- **`validate-pids.py`** — Schema validation for `pids/` YAML files
- **`captures/`** — Raw UDS response payloads, split by date (e.g. `2026-04-19.yaml`). Schema in `captures/SCHEMA.yaml`. Validate with `python3 validate-captures.py`. **After adding captures, run `python3 query-captures.py --summary` to check for new patterns.**
- **`validate-captures.py`** — Schema validation for `captures/` YAML files
- **`bix.py`** — Byte index converter: WiCAN ↔ ISO-TP ↔ Torque ↔ bix. Use `python3 bix.py w9` or `python3 bix.py E` for quick lookups, `--table` for full table. **`--annotate HEX` (`-a`)** maps a raw UDS response payload to a table with WiCAN Bnn, ISO-TP index, Torque letter, bix, and role per byte. Supports `-1` (21xx, default) and `-2` (22xxxx) subfunction modes.
- **`docs/wican-iso-tp-index-conversion.md`** — Reference table for byte index notation differences
- **`docs/CLI commands.md`** — Reference for `canreq.py` usage and examples

## WiCAN Access

- Home: `http://10.0.2.86` | VPN: `http://192.168.3.2`
- WebSocket terminal: `ws://<ip>/ws` (send `{"ws_mode": "terminal", "terminal_type": "elm327"}`)

## Ideas

- Use known PIDs to automatically deduce vehicle state to help understand new PIDs
