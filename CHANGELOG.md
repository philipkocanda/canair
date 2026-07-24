# Changelog

All notable changes to **canair** are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.1.0] - 2026-07-24

### Added

- **`canair ecu add TX`** — register an ECU into a profile **offline** (no live
  bus), the counterpart to `discover --register` for seeding a known ECU into a
  blank/contributable profile. `ecu` is now a command group (`show` default +
  `add`). Validated and comment-preserving.
- **First-run profile chooser.** On the first interactive run that needs a
  profile, canair offers to pick a discovered profile or create a new one, with
  explicit path messaging, and records the choice as `default_profile`. Never
  fires when scripted/piped or when `--profile`/`CANAIR_PROFILE` is set.
- **`canair investigate --bits`** — rank individual toggling bits (`Bn:k`), not
  just bytes, so body/comfort-ECU status signals surface. Also fixes the
  no-co-polled-anchor case to rank by state separation with a hint (instead of
  misleadingly reporting "no varying bytes").
- **`canair investigate --events`** — the bit/byte edge timeline: each
  rising/falling transition with its timestamp and value, aligned to the nearest
  capture note (the narrated event log). Automates decoding event-driven captures
  (door/lock/hood etc.).
- **`canair correlate --find-mirrors`** — cross-ECU byte/bit mirror finder
  (time-aligned equal positions across co-polled PIDs); the cross-ECU companion
  to `decode --find-mirrors` (single-PID). Use with `--bits` for bit-level.
- **`canair bix --annotate --ecu ECU --pid PID`** — overlay which defined
  parameter (and bit) maps each byte, flagging unmapped data bytes. Makes a wrong
  byte offset obvious at a glance.
- **`canair pids rename-param` / `rm-param`** — rename or remove a parameter
  (comment-preserving, schema-validated, auto-reverted on failure). Removes the
  last "must hand-edit YAML" case for parameter maintenance.
- **`keep_mode` awareness in analysis.** `decode`, `correlate`, and `investigate`
  now warn when the scope includes `keep:unique` sessions (only rising-edge
  transitions were stored; falling edges/durations are absent) and caveat
  rate/duration transforms (`--corr-transform delta|cumsum`, `--lag-scan`) on
  such data.

### Changed

- **`canair validate pids`** now flags a duplicate *shipped* parameter name
  across PIDs (a device signal-name collision) as an error — previously this
  only surfaced at `wican autopid write` time.
- **ECU-file validation is profile-scoped.** Validating (and thus writing via
  `canair pids`/`ecu add`) an ECU file now resolves the vehicle-state vocabulary
  from the file's own profile rather than the globally-active one, so edits to a
  non-active profile work even when several profiles are discovered.

### Removed

- **`canair tester-present` command.** It duplicated behavior already provided
  automatically: opening an extended session (via a `session <ECU>` query step
  or any command's `--session`) keeps that session alive with idle-aware
  TesterPresent (`3E00`) keepalives. Send a one-off by hand with a query step
  (`canair query BMS:3E00`); the interactive `repl`'s `!tester [id]` loop
  remains for manual keepalive spamming. TesterPresent (SID `0x3E`) is shared by
  UDS and KWP2000 and is sent identically for both.

### Docs

- **Task-first documentation site** under `docs/`, published with MkDocs Material
  to [philipkocanda.github.io/canair](https://philipkocanda.github.io/canair/):
  getting-started, the full **Bring your own car** walkthrough, concepts, a
  reference, the bundled-profile tour, and a contributing guide.
- **Generated CLI reference** (`scripts/gen_cli_reference.py` →
  `docs/reference/cli/`) rendered from each command's `--help`, with a CI
  `--check` gate so it can't drift.
- **`CONTRIBUTING.md`** and prominent "contribute your profile/PIDs back"
  encouragement across the README and docs.
- README trimmed to a compact, high-level gateway that links into the docs site.

## [1.0.0] - 2026-07-23

First stable release. canair is a general-purpose CAN/UDS/KWP2000 diagnostic
reverse-engineering CLI that talks to a vehicle over the air through a WiCAN
dongle (both the WiCAN Pro and the classic/non-Pro WiCAN are supported).

### Added

- **`canair --version`** flag, single-sourced from the installed package
  metadata (`canlib.__version__` via `importlib.metadata`).
- **Live device tooling** — `query`, `scan` (range/iocontrol/routines/sessions),
  `discover`, `io`, `routines`, `identity`, `raw`, `repl`.
- **DTC handling** — `dtc` reads stored Diagnostic Trouble Codes across ECUs
  (UDS `0x19` / KWP2000 `0x18`), logs scans, reports changes, and can clear
  fault memory (`0x14`).
- **Passive sniffing** — `sniff` live per-ID broadcast table with optional
  `.asc`/`.blf`/`.csv` logging (raw SLCAN transport).
- **Capture pipeline** — `--save`/`--monitor` journaled capture recording with
  crash recovery (`captures --recover`), plus `captures` search/diff/step.
- **Analysis** — `decode` (stats, correlation, interactive `--plot` explorer,
  `--try` expression testing), `coverage` (decoding-gap audit), `research`
  (RE backlog).
- **Definition editing** — `pids` (surgical, validated, comment-preserving
  edits to per-ECU YAML) and `validate` (schema validation).
- **WiCAN integration** — `wican` AutoPID profile generation/upload/download/
  diff and device mode switching (device sync is Pro-only).
- **Profiles** — multi-vehicle profile bundles with `profile create/list/show`;
  ships `profiles/ioniq-2017/` as the default example.
- **Utilities** — `bix` byte-index converter, `ecu` registry inspection,
  `status` transport/mode snapshot, `config` user-config management.
- **Dual-transport architecture** — every bus feature works over both the raw
  `slcan-tcp` transport (default) and the `wican-ws` WebSocket ELM327 terminal.
- Command safety blocklist preventing UDS programming/write sessions against a
  real vehicle.

[Unreleased]: https://github.com/philipkocanda/canair/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/philipkocanda/canair/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/philipkocanda/canair/releases/tag/v1.0.0
