# Changelog

All notable changes to **canair** are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-07-23

First stable release. canair is a general-purpose CAN/UDS/KWP2000 diagnostic
reverse-engineering CLI that talks to a vehicle over the air through a WiCAN
dongle (both the WiCAN Pro and the classic/non-Pro WiCAN are supported).

### Added

- **`canair --version`** flag, single-sourced from the installed package
  metadata (`canlib.__version__` via `importlib.metadata`).
- **Live device tooling** — `query`, `scan` (range/iocontrol/routines/sessions),
  `discover`, `io`, `routines`, `identity`, `tester-present`, `raw`, `repl`.
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

[Unreleased]: https://github.com/philipkocanda/canair/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/philipkocanda/canair/releases/tag/v1.0.0
