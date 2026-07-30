# CLI reference

Every capability is a `canair <subcommand>`. These pages are generated from each command's `--help` (the source of truth), so they never drift from the code.

> Regenerate with `python3 scripts/gen_cli_reference.py`; CI checks they are current.

## Commands

### Live device

- [`canair status`](status.md) — ``canair status`` — show the configured transport, device state, and profile.
- [`canair query`](query.md) — Query ECUs/parameters live. Positional STEPs use the multi mini-language.
- [`canair monitor`](monitor.md) — Monitor ECUs/parameters live in a scrollable, refreshing view. Positional STEPs use the multi mini-language (same as `canair query`).
- [`canair scan`](scan.md) — Scan an ECU. Choose a kind:
- [`canair discover`](discover.md) — Sweep a range of TX addresses (sends 10 01 to each) to find ECUs.
- [`canair raw`](raw.md) — Send a raw UDS request (hex in, hex out).
- [`canair sniff`](sniff.md) — ``canair sniff`` — passive CAN bus sniffer (raw SLCAN-over-TCP backend).
- [`canair io`](io.md) — IOControl (0x2F): interactive TUI, or single actuator command with --did.
- [`canair routines`](routines.md) — RoutineControl (0x31): interactive TUI, or single command with --rid.
- [`canair identity`](identity.md) — Query ECU identity data and decode it. Supports UDS (22 F1xx) and KWP2000 (1A 8x/9x) ECUs; the protocol is auto-selected from the profile registry or an on-device probe (override with --protocol).
- [`canair dtc`](dtc.md) — Read stored Diagnostic Trouble Codes with UDS 0x19 (reportDTCByStatusMask), or clear them with UDS 0x14. Clearing mutates ECU fault memory and prompts for confirmation unless --yes is given.
- [`canair repl`](repl.md) — Drop into an interactive live terminal (REPL) over the WiCAN

### Analysis

- [`canair captures`](captures.md) — Query captured data. Choose a kind:
- [`canair decode`](decode.md) — Decode captured UDS payloads using PID parameter definitions.
- [`canair correlate`](correlate.md) — Find every strong cross-signal relationship across a drive/session.
- [`canair hunt`](hunt.md) — Answer 'which byte carries a signal I already know?' Choose a domain kind:
- [`canair investigate`](investigate.md) — Point this at an unknown signal and get one ranked table telling you
- [`canair coverage`](coverage.md) — Audit PID definitions for decoding gaps.
- [`canair research`](research.md) — Report open reverse-engineering work from ecus/ research: sections.

### Authoring

- [`canair pids`](pids.md) — Safely edit ecus/ parameters and research entries (domain A — diagnostic UDS PIDs, freeform WiCAN expressions). The broadcast-frame (domain B) authoring counterpart is `canair signals` (linear signals/ maps).
- [`canair signals`](signals.md) — ``canair signals`` — view/edit broadcast signal definitions (domain B).
- [`canair ecu`](ecu.md) — Inspect or edit the profile's ECU registry.
- [`canair bus`](bus.md) — List the active profile's CAN bus segments with their human names, descriptions, and the number of ECUs on each (from can_buses.yaml).
- [`canair wican`](wican.md) — Build and sync the WiCAN device's AutoPID profile.
- [`canair validate`](validate.md) — Validate a profile's data files against their schemas and

### Import / export

- [`canair import`](import.md) — Import external CAN data into the active profile. Choose a kind:
- [`canair export`](export.md) — ``canair export`` — export canair data to interchange formats.

### Setup

- [`canair profile`](profile.md) — List, inspect, and create vehicle profiles — the per-vehicle
- [`canair config`](config.md) — ``canair config`` — view and manage user configuration.
- [`canair update`](update.md) — ``canair update`` — update the CLI from its git-clone install.
- [`canair completion`](completion.md) — Enable `canair` tab-completion (subcommands, flags, ECU/PID names).

### Other

- [`canair states`](states.md) — List the active profile's vehicle operating states, or edit the vocabulary (add/rm/rename/set-description/set-predicate). Read-only companion of the state auto-suggestion used when recording captures.
- [`canair contribute`](contribute.md) — ``canair contribute`` — open a pull request for the active profile.
- [`canair bix`](bix.md) — Convert byte indices between WiCAN, ISO-TP, Torque, and OBDb notations.
- [`canair logs`](logs.md) — ``canair logs`` — view the central rotating diagnostics event log.
