# Hyundai Ioniq 2017 — CAN Reverse Engineering

For reference, the WiCAN firmware is checked out in the `wican-fw/` directory (gitignored; pull if you need to reference the latest version).

## Tools

> Reverse-engineering a new PID/DID end-to-end (discover → capture → analyze → define → verify) is documented in the **reverse-engineer-pid** skill; general project/device context is in the **ioniq-reverse-engineering** skill.

All functionality is exposed through a single installable CLI, **`canair`** (argparse subcommands; `uv tool install .`, or `uv run canair …` in the repo).

- **`canair wican`** — Generate WiCAN vehicle profiles from the active profile's `pids/`, upload/download/diff with device
- **`canair query`** — Custom CAN/UDS requests via WiCAN WebSocket ELM327 terminal mode. **Prefer positional query steps** like `canair query "query BMS 2101"` (multi mini-language — handles sessions, wake, keepalives; a bare selector like `BMS:2101` is treated as a query step). Use `canair query --param`/`canair query BMS` for simple single-ECU reads, `canair scan` for discovery. Companions: `canair discover`, `canair io`, `canair routines`, `canair tester-present`, `canair raw` (**last resort** — hex dump only, no decoding), `canair repl` (interactive). `--verbose` is for debugging canair itself, not normal use. **Use `--reboot` to restore AutoPID after session** (WebSocket terminal overrides AutoPID mode). Dependencies: `websockets`, `pyyaml`, `requests` (optional, for reboot).
- **`canair captures`** — Query captured UDS payloads across all capture files. Use after adding new captures to spot patterns. Takes a QUERY (mini-language, like the other tools): `BMS 2102` (ECU + PID, most useful), `"BMS:2102,2103"` (several PIDs), `BMS` (all PIDs for an ECU), or a quoted cross-ECU `"VCU:2101 BMS:2101"`. Add `--diff` for the byte-level diff view or `--step` to interactively step through captures. Standalone modes (no QUERY): `--summary` (stats per ECU/date), `--latest [ECU]` (most recent payload per PID). Any mode can be scoped to a date range with `--since`/`--until` (inclusive, `YYYY-MM-DD`) or `--date` (single day).
- **`canair decode`** — Parameter/value-centric decoding: applies WiCAN expressions to all historical captures. **Default `canair decode BMS 2101` shows each param's value range** across captures (payload/byte-diff views live in `canair captures`). Modes: `--param` (filter), `--compact` (one line per capture), `--stats` (n/distinct/mean/median/stdev per param), `--corr PARAM` (Pearson correlation of every param vs a reference — validate a candidate against a known signal), `--plot` (interactive signal explorer: sweep ImHex-style byte interpretations `u8/i16/f32/…`×endianness and params, plot across captures, apply transforms `delta/abs/normalize/…`, zoom/pan the x-axis, overlay a `--corr` signal, show the visible date/time range, list the backing captures in a modal (`i`), flag bytes already mapped by a defined param, and read off the equivalent WiCAN expression), `--try "NAME[:unit]=EXPR"` (test a candidate expression against captures **without** editing YAML; works even for a not-yet-defined PID), `--verified`/`--unverified`, `--json`.
- **`canair research`** — Report the open reverse-engineering backlog from the per-ECU `research:` sections in the profile's `pids/`. Complements `canair coverage` (undecoded *bytes*) by surfacing *planned* work (scans/decodes/verifies). Modes: `canair research` (all open, priority-sorted), `--summary` (counts by status/type/priority/ECU), `--ecu`, `--type scan|decode|verify|iocontrol_scan`, `--status`, `--priority P1|P2|P3`, `--prerequisite acc|ready|…`, `--all` (include done), `--json`. Use as the "what should I RE next?" entry point.
- **`canair coverage`** — Audit PID definitions for decoding gaps. Cross-references each PID's parameter expressions against its longest captured payload and reports **UNMAPPED** data bytes, incomplete **BITS** (bytes read bit-by-bit with undecoded bits left), and **NO CAPTURE** PIDs (params defined but nothing captured). Modes: `canair coverage` (all), `canair coverage IGPM [22BC03]` (filter), `--bitfields`, `--unmapped`, `--no-capture`, `--all` (include fully-mapped), `--json`. Run after adding params/captures to spot what still needs decoding.
- **`canair pids`** — Safely add/update the profile's `pids/` **parameters** and **research** entries from the CLI (surgical, comment-preserving; each edit is YAML-reparsed and schema-validated via `canair validate pids`, and auto-reverted on failure). Subcommands: `upsert-param ECU PID NAME EXPR [--unit --min --max --source --notes --verified/--unverified …]`, `add-research ECU --type --target --status [--priority --prereq …]`, `set-status ECU TARGET STATUS [--type]`. Prefer this over hand-editing the rich per-ECU YAML.

### Profiles

Vehicle data lives in a **profile** bundle — a directory with `pids/`, `ecus.yaml`, `captures/`, and generated `out/`. The repo ships `profiles/ioniq-2017/` as the default/example. Selection precedence: `--profile NAME|PATH` (global flag, before the subcommand) > `CANAIR_PROFILE` env var > `default_profile` in config > single discovered profile (auto). Profiles are discovered from `--profiles-dir`, `$CANAIR_PROFILES_DIR`, `profiles_dir` in config, `~/.config/canair/profiles/` (user, uncommitted), and the repo's bundled `profiles/`; user profiles shadow bundled ones by name. Inspect with `canair profile list` / `show [NAME]` / `path [NAME]`.

## Key Files

- **`profiles/ioniq-2017/pids/`** — SOURCE OF TRUTH for all PID definitions, split by ECU (220 parameters, 192 verified). Validate with `canair validate pids`
- **`canair validate pids`** — Schema validation for the profile's `pids/` YAML files (schema is tool-owned: `canlib/schema/pids_schema.yaml`; `pids/_meta.yaml` stays in the profile)
- **`profiles/ioniq-2017/captures/`** — Raw UDS response payloads, split by date (e.g. `2026-04-19.yaml`). Schema in `captures/SCHEMA.yaml` (tool-owned: `canlib/schema/captures_schema.json`). Validate with `canair validate captures`. **NEVER hand-write or edit these YAML files.** Record device reads via `canair query … --save` with `--label`/`--state`/`--notes` (non-interactive), e.g. `canair query "query MCU" "query VCU:2101" --wican vpn --save --label "…" --state "ready, parked" --notes "…"`. `--save` works with `canair scan`/`raw`/`discover`, positional query/raw steps, and `--monitor`. For edits/removals use `canlib.captures` helpers (`set_capture_note`, `delete_capture`). **After adding captures, run `canair captures --summary` to check for new patterns.**
- **`canair validate captures`** — Schema validation for the profile's `captures/` YAML files
- **`canair bix`** — Byte index converter: WiCAN ↔ ISO-TP ↔ Torque ↔ bix. Use `canair bix w9` or `canair bix E` for quick lookups, `--table` for full table. **`--annotate HEX` (`-a`)** maps a raw UDS response payload to a table with WiCAN Bnn, ISO-TP index, Torque letter, bix, and role per byte. Supports `-1` (21xx, default) and `-2` (22xxxx) subfunction modes.
- **`docs/wican-iso-tp-index-conversion.md`** — Reference table for byte index notation differences (local only, not tracked in git)
- **`docs/CLI commands.md`** — Reference for `canair query` usage and examples (local only, not tracked in git)

## WiCAN Access

Device addresses are configured in `~/.config/canair/config.yaml` (a legacy repo-root `config.yaml` is still read for back-compat; both gitignored). Copy from `config.example.yaml`:

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
