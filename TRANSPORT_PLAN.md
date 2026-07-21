# Pluggable Transport + `canair status` — Implementation Plan

Make canair's CAN transport an **explicit, config-driven choice** (no
auto-detection, no auto-switching of device mode), decoupling it from the WiCAN
Pro WebSocket so it also works with a classic WiCAN (SLCAN) and, in principle,
non-WiCAN hardware. Add a maximally-helpful `canair status` command.

## Decisions (locked)

- Scope: **A + B + C**.
- Transports now: **`wican-ws`** (Pro WebSocket ELM327 terminal) and
  **`slcan-tcp`** (SLCAN over TCP: classic WiCAN / gateway). (socketcan /
  slcan-serial are future.)
- **Remove auto-switching** of the WiCAN device `protocol`. Commands *expect* the
  device to already be in the mode the transport needs; if not, fail with a
  clear, actionable error. Device-mode changes are an explicit opt-in command.
- `canair status` is a dedicated top-level command.
- Config is explicit, Unix-style, predictable, with good error messages.

## Confirmed facts

- WiCAN firmware protocols: `slcan` / `savvycan` / `realdash66` / `elm327` /
  `auto_pid` (`config_server.c`); **classic WiCAN defaults to `slcan` on TCP
  3333**; Pro uses `35000`. `SlcanTcpBus` already speaks SLCAN over TCP.
- The `/ws` ELM327 terminal is **Pro-only** and works in any `protocol`; classic
  WiCAN has no such terminal, so diagnostics there go over the raw path
  (`RawUdsClient` = client-side ISO-TP/UDS, built in Phase 2).

## Transport config schema

`~/.config/canair/config.yaml`:
```yaml
transport:
  type: wican-ws            # wican-ws | slcan-tcp
  host: 192.168.3.2         # both
  port: 35000               # slcan-tcp (Pro 35000, classic 3333); auto-detected from WiCAN if omitted
  bitrate: 500000           # slcan-tcp; falls back to profile can_datarate, else 500000
```
Back-compat: no `transport:` block → `wican-ws` via `wican_addresses` /
`default_wican`. Per-command overrides: `--transport`, `--wican` (host),
`--port`, `--bitrate`.

Resolution precedence (highest first): CLI flag > `transport:` block >
`wican_addresses`/`default_wican` fallback.

## Part A — config resolver + `canair status`

- `canlib/transport/config.py`: `TransportConfig` (type/host/port/bitrate +
  `is_raw`/`is_elm`) and `resolve_transport(args) -> TransportConfig`.
- `canlib/commands/status.py` — `canair status [--json]`:
  - Configured transport (type + params + which command families it supports).
  - Reachability: TCP connect (raw) / `/check_status` (WiCAN HTTP); clear
    "unreachable at <addr>" errors, never a traceback.
  - WiCAN extras when available: `protocol`, port, `sleep_status`, battery, IP/fw
    — and a **mode-mismatch warning** if device `protocol` ≠ transport needs.
  - Active vehicle profile (name + path).
  - Exit 0 healthy / non-zero unreachable-or-misconfigured; `--json`.
- Tests: resolver precedence + status rendering (mocked HTTP/socket).

## Part B — remove auto-switch; explicit device-mode control

- `add_connection_args`: add `--transport`, `--port`, `--bitrate`.
- `sniff` + raw monitor: drop `protocol_mode` auto-switch. Open the configured
  `slcan-tcp` transport directly; if it's a WiCAN reachable over HTTP and its
  `protocol` isn't `slcan`, **error clearly** ("device is in '<p>'; set it with
  `canair wican --set-protocol slcan`, or in the web UI") before connecting.
- `canair wican --set-protocol <name> [--yes]`: the ONLY place that changes the
  device mode (explicit, consented, reboots + waits). Uses `wican_mode.set_protocol`.
- Keep `wican_mode.protocol_mode` in the tree but unused by default flow (or
  remove if fully orphaned).

## Part C — route commands by transport type

- In `_live.async_main`, resolve the transport and branch:
  - `wican-ws` → today's `WiCANTerminal` path.
  - `slcan-tcp` (raw) → open `SlcanTcpBus` + `RawUdsClient`; dispatch raw
    implementations of the supported commands.
- Raw implementations (reuse `build_query_plan`, `_decode_pid_result`,
  `RawUdsClient`, `print_ecu_results`):
  - `query` (one-shot pipelined reads), `raw` (single UDS request), `scan`
    (probe a PID range), `monitor` (the Phase 2 raw backend, minus auto-switch).
  - `--raw-can` flag is superseded by `--transport slcan-tcp` (drop/alias it).
- Commands not yet supported over raw (`io`, `routines`, `discover`, `identity`,
  `*-scan`) → clear "not supported over '<transport>' yet (use wican-ws)" error.

## Tests / verification

- Unit: resolver precedence; status output (mocked); raw query/raw/scan result
  shapes (fake client); error paths (wrong mode, unreachable).
- On-device: `canair status` in each mode; `canair query`/`raw`/`monitor` over
  `slcan-tcp` (device pre-set to slcan); `canair wican --set-protocol` round-trip;
  restore to `auto_pid`. Full pytest + ruff green.

## Status

- [x] A — `TransportConfig` + `resolve_transport` (`canlib/transport/config.py`) +
  `canair status` (transport, WiCAN protocol/sleep/battery/ip, profile,
  mode-mismatch, `--json`, exit codes) + tests.
- [x] B — `--transport`/`--port`/`--bitrate` on `add_connection_args`;
  `canair wican --set-protocol <mode> [--yes]` (explicit device-mode set);
  `wican_mode.require_protocol` preflight; removed auto-switch from `sniff` and
  the raw monitor (removed the now-orphaned `protocol_mode`/`_confirm`).
- [x] C — transport-type dispatch in `_live.async_main` (`transport.is_raw` →
  `canlib.modes.raw_ops.run_raw`); raw `query`/`raw`/`monitor` over `slcan-tcp`;
  dropped `--raw-can` (superseded by `--transport slcan-tcp`); other commands
  return a clear "not supported over slcan-tcp yet" error.
- [x] On-device verified (2026-07-21): `canair status` (wican-ws + slcan-tcp,
  incl. mode-mismatch warning); `canair wican --set-protocol slcan|auto_pid`;
  `canair query`/`raw` over `--transport slcan-tcp` (decoded, matches ELM path);
  device restored to `auto_pid`. Full pytest + ruff green.
- [ ] Follow-ups: raw `scan`; socketcan / slcan-serial transports; route the
  advanced commands (io/routines/discover/identity/*-scan) over raw.

