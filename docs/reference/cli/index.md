# CLI reference

Every capability is a `canair <subcommand>`. These pages are generated from each command's `--help` (the source of truth), so they never drift from the code.

> Regenerate with `python3 scripts/gen_cli_reference.py`; CI checks they are current.

## Commands

### Live device

- [`canair status`](status.md) — ``canair status`` — show the configured transport, device state, and profile.
- [`canair read`](read.md) — Read ECUs/parameters live. Positional STEPs use the multi mini-language.
- [`canair monitor`](monitor.md) — Monitor ECUs/signals live in a scrollable, refreshing view. Positional STEPs use the multi mini-language (same as `canair read`). With no STEP on a terminal, an interactive picker offers saved groups and ECUs, then PIDs.
- [`canair scan`](scan.md) — Scan an ECU. Choose a kind:
- [`canair discover`](discover.md) — Sweep a range of TX addresses (sends 10 01 to each) to find ECUs. With --functional, send one read on the functional (broadcast) id instead and list every ECU that answers, grouped by bus: which buses a gateway car routes diagnostics to.
- [`canair raw`](raw.md) — Send a raw UDS request (hex in, hex out).
- [`canair sniff`](sniff.md) — ``canair sniff`` — passive CAN bus sniffer (raw SLCAN-over-TCP backend).
- [`canair io`](io.md) — IOControl (UDS 0x2F, or an entry's own KWP2000 0x30 commands): interactive TUI, or single actuator command with --did. Every actuator switched on is released on exit and on Ctrl+C with its release request: returnControlToECU (2F{DID}00) on UDS, the entry's declared release: on KWP2000, where the 0x30 option byte has no standard release. A 0x30 ON without a declared release is refused (set one with `canair pids set-iocontrol ECU KEY --release HEX`). The TUI's value keys (v/+/-) build UDS 0x2F requests, so they are refused for a KWP2000 ECU.
- [`canair routines`](routines.md) — RoutineControl (0x31): interactive TUI, or single command with --rid.
- [`canair identity`](identity.md) — Query ECU identity data and decode it. Supports UDS (22 F1xx) and KWP2000 (1A 8x/9x) ECUs; the protocol is auto-selected from the profile registry or an on-device probe (override with --protocol).
- [`canair dtc`](dtc.md) — Read, clear or review Diagnostic Trouble Codes. Choose a kind:
- [`canair repl`](repl.md) — Drop into an interactive live terminal (REPL) over an ELM327
- [`canair lock`](lock.md) — ``canair lock`` — inspect and clear the device connection mutex.

### Analysis

- [`canair captures`](captures.md) — Query captured data. Choose a kind:
- [`canair decode`](decode.md) — Decode captured UDS payloads using PID parameter definitions.
- [`canair align`](align.md) — Emit a time-aligned, wide table of several ECU:PID:PARAM signals — one row per reference sample, one column per signal, nearest-joined within a tolerance. The first selector sets the row cadence; the rest join onto it.
- [`canair correlate`](correlate.md) — Find every strong cross-signal relationship across a drive/session.
- [`canair hunt`](hunt.md) — Answer 'which byte carries a signal I already know?' Choose a domain kind:
- [`canair investigate`](investigate.md) — Point this at an unknown signal and get one ranked table telling you
- [`canair search`](search.md) — Find a string, VIN or byte pattern anywhere in the captures — without already
- [`canair coverage`](coverage.md) — Audit PID definitions for decoding gaps.
- [`canair research`](research.md) — Report open reverse-engineering work from ecus/ research: sections.

### Authoring

- [`canair pids`](pids.md) — Safely edit ecus/ parameters and research entries (domain A — diagnostic UDS PIDs, freeform WiCAN expressions). The broadcast-frame (domain B) authoring counterpart is `canair signals` (linear signals/ maps).
- [`canair signals`](signals.md) — ``canair signals`` — view/edit broadcast signal definitions (domain B).
- [`canair ecu`](ecu.md) — Inspect or edit the profile's ECU registry.
- [`canair bus`](bus.md) — List the active profile's CAN bus segments with their human names, descriptions, and the number of ECUs on each (from can_buses.yaml).
- [`canair states`](states.md) — List the active profile's vehicle operating states, or edit the vocabulary (add/rm/rename/set-description/set-predicate/set-implies/set-excludes). Read-only companion of the state auto-suggestion used when recording captures.
- [`canair groups`](groups.md) — List the active profile's named selector groups, or edit the vocabulary (add/rm/rename/set-description/set-members). Reference a group on the command line with the @ sigil, e.g. `canair monitor @charging`.
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

- [`canair action`](action.md) — Profile-defined actions: named requests for make-specific things such as putting the car to sleep. Each action declares live reads and a condition that must be TRUE before it is sent; its requests still pass the command blocklist, which only a code-owned profile quirk can relax for that exact request.
- [`canair variants`](variants.md) — List the variants of the active profile's car — the versions an owner can name, such as a battery size or a facelift — or edit them (add/set-meta/unset-meta/set-revisions/rename/rm). A variant can limit which ECU revisions its car carries and override some profile.yaml settings.
- [`canair contribute`](contribute.md) — Open a pull request contributing the active profile upstream.
- [`canair bix`](bix.md) — Convert byte indices between WiCAN, ISO-TP, Torque, and OBDb notations.
- [`canair logs`](logs.md) — ``canair logs`` — view the central rotating diagnostics event log.
