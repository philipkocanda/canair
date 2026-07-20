# Hyundai Ioniq 2017 — CAN Reverse Engineering

For reference, the WiCAN firmware is checked out in the `wican-fw/` directory (gitignored; pull if you need to reference the latest version).

## Tools

> Reverse-engineering a new PID/DID end-to-end (discover → capture → analyze → define → verify) is documented in the **reverse-engineer-pid** skill; general project/device context is in the **ioniq-reverse-engineering** skill.

- **`generate-profile.py`** — Generate WiCAN vehicle profiles from `pids/`, upload/download/diff with device
- **`canreq.py`** — CLI tool for custom CAN/UDS requests via WiCAN WebSocket ELM327 terminal mode. **Prefer `--multi "query ..."` for decoded output** (handles sessions, wake, keepalives). Use `--param`/`--ecu` for simple single-ECU reads, `--scan` for discovery. **`--raw` is last resort** (hex dump only, no decoding). `--verbose` is for debugging canreq itself, not normal use. **Use `--reboot` to restore AutoPID after session** (WebSocket terminal overrides AutoPID mode). Dependencies: `websockets`, `pyyaml`, `requests` (optional, for reboot).
- **`query-captures.py`** — Query captured UDS payloads across all capture files. Use after adding new captures to spot patterns. Modes: `--ecu ECU --pid PID` (combined, most useful), `--ecu ECU` (all captures for ECU), `--summary` (stats per ECU/date), `--latest [ECU]` (most recent payload per PID), `--diff ECU PID` (byte-level diff across captures). Any mode can be scoped to a date range with `--since`/`--until` (inclusive, `YYYY-MM-DD`) or `--date` (single day).
- **`decode.py`** — Parameter/value-centric decoding: applies WiCAN expressions to all historical captures. **Default `decode.py BMS 2101` shows each param's value range** across captures (payload/byte-diff views live in `query-captures.py`). Modes: `--param` (filter), `--compact` (one line per capture), `--stats` (n/distinct/mean/median/stdev per param), `--corr PARAM` (Pearson correlation of every param vs a reference — validate a candidate against a known signal), `--plot` (interactive signal explorer: sweep ImHex-style byte interpretations `u8/i16/f32/…`×endianness and params, plot across captures, apply transforms `delta/abs/normalize/…`, overlay a `--corr` signal, and read off the equivalent WiCAN expression), `--try "NAME[:unit]=EXPR"` (test a candidate expression against captures **without** editing YAML; works even for a not-yet-defined PID), `--verified`/`--unverified`, `--json`.
- **`research.py`** — Report the open reverse-engineering backlog from the per-ECU `research:` sections in `pids/`. Complements `pid-coverage.py` (undecoded *bytes*) by surfacing *planned* work (scans/decodes/verifies). Modes: `research.py` (all open, priority-sorted), `--summary` (counts by status/type/priority/ECU), `--ecu`, `--type scan|decode|verify|iocontrol_scan`, `--status`, `--priority P1|P2|P3`, `--prerequisite acc|ready|…`, `--all` (include done), `--json`. Use as the "what should I RE next?" entry point.
- **`pid-coverage.py`** — Audit PID definitions for decoding gaps. Cross-references each PID's parameter expressions against its longest captured payload and reports **UNMAPPED** data bytes, incomplete **BITS** (bytes read bit-by-bit with undecoded bits left), and **NO CAPTURE** PIDs (params defined but nothing captured). Modes: `pid-coverage.py` (all), `pid-coverage.py IGPM [22BC03]` (filter), `--bitfields`, `--unmapped`, `--no-capture`, `--all` (include fully-mapped), `--json`. Run after adding params/captures to spot what still needs decoding.
- **`pids-edit.py`** — Safely add/update `pids/` **parameters** and **research** entries from the CLI (surgical, comment-preserving; each edit is YAML-reparsed and schema-validated via `validate-pids.py`, and auto-reverted on failure). Subcommands: `upsert-param ECU PID NAME EXPR [--unit --min --max --source --notes --verified/--unverified …]`, `add-research ECU --type --target --status [--priority --prereq …]`, `set-status ECU TARGET STATUS [--type]`. Prefer this over hand-editing the rich per-ECU YAML.

## Key Files

- **`pids/`** — SOURCE OF TRUTH for all PID definitions, split by ECU (220 parameters, 192 verified). Validate with `python3 validate-pids.py`
- **`validate-pids.py`** — Schema validation for `pids/` YAML files
- **`captures/`** — Raw UDS response payloads, split by date (e.g. `2026-04-19.yaml`). Schema in `captures/SCHEMA.yaml`. Validate with `python3 validate-captures.py`. **NEVER hand-write or edit these YAML files.** Record device reads via `canreq.py … --save` with `--label`/`--state`/`--notes` (non-interactive), e.g. `canreq.py --multi "query MCU" "query VCU:2101" --wican vpn --save --label "…" --state "ready, parked" --notes "…"`. `--save` works with `--scan`/`--raw`/`--discover`/`--multi` (query/raw steps)/`--monitor`. For edits/removals use `canlib.captures` helpers (`set_capture_note`, `delete_capture`). **After adding captures, run `python3 query-captures.py --summary` to check for new patterns.**
- **`validate-captures.py`** — Schema validation for `captures/` YAML files
- **`bix.py`** — Byte index converter: WiCAN ↔ ISO-TP ↔ Torque ↔ bix. Use `python3 bix.py w9` or `python3 bix.py E` for quick lookups, `--table` for full table. **`--annotate HEX` (`-a`)** maps a raw UDS response payload to a table with WiCAN Bnn, ISO-TP index, Torque letter, bix, and role per byte. Supports `-1` (21xx, default) and `-2` (22xxxx) subfunction modes.
- **`docs/wican-iso-tp-index-conversion.md`** — Reference table for byte index notation differences (local only, not tracked in git)
- **`docs/CLI commands.md`** — Reference for `canreq.py` usage and examples (local only, not tracked in git)

## WiCAN Access

Device addresses are configured in `config.yaml` (gitignored). Copy from `config.example.yaml`:

```yaml
wican_addresses:
  home: "10.0.2.86"       # Device on local LAN
  vpn: "192.168.3.2"      # Device via VPN
default_wican: home
```

All CLI tools use `--wican home|vpn|<ip>` to select the target. Without `config.yaml`, falls back to `192.168.80.1` (WiCAN AP mode).

- WebSocket terminal: `ws://<ip>/ws` (send `{"ws_mode": "terminal", "terminal_type": "elm327"}`)

## Ideas

- Use known PIDs to automatically deduce vehicle state to help understand new PIDs
