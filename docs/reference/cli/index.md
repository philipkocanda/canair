# CLI reference

Every capability is a `canair <subcommand>`. These pages are generated from each command's `--help` (the source of truth), so they never drift from the code.

> Regenerate with `uv run python scripts/gen/gen_cli_reference.py`; CI checks they are current.

## Commands

### Live device

- [`canair status`](status.md) — Show the configured transport, device state, and profile.
- [`canair read`](read.md) — Read ECUs/signals live.
- [`canair monitor`](monitor.md) — Monitor ECUs/signals live in a scrollable, refreshing view.
- [`canair scan`](scan.md) — Scan an ECU.
- [`canair discover`](discover.md) — Sweep a range of TX addresses (sends 10 01 to each) to find ECUs.
- [`canair raw`](raw.md) — Send a raw UDS request (hex in, hex out).
- [`canair sniff`](sniff.md) — Passive CAN bus sniffer (raw SLCAN-over-TCP backend).
- [`canair io`](io.md) — IOControl (UDS 0x2F, or an entry's own KWP2000 0x30 commands): interactive TUI, or single actuator command with --did.
- [`canair routines`](routines.md) — RoutineControl (0x31): interactive TUI, or single command with --rid.
- [`canair identity`](identity.md) — Query ECU identity data and decode it.
- [`canair dtc`](dtc.md) — Read, clear or review Diagnostic Trouble Codes.
- [`canair repl`](repl.md) — Drop into an interactive live terminal (REPL) over an ELM327 transport (wican-ws / elm327-tcp) — type raw ELM327 (AT...) and UDS requests by hand and see the decoded response, for exploratory poking the one-shot commands don't cover.
- [`canair lock`](lock.md) — Inspect and clear the device connection mutex.

### Analysis

- [`canair captures`](captures.md) — Query captured data.
- [`canair decode`](decode.md) — Decode captured UDS payloads using PID parameter definitions.
- [`canair align`](align.md) — Emit a time-aligned, wide table of several ECU:PID:PARAM signals — one row per reference sample, one column per signal, nearest-joined within a tolerance.
- [`canair correlate`](correlate.md) — Find every strong cross-signal relationship across a drive/session.
- [`canair hunt`](hunt.md) — Answer 'which byte carries a signal I already know?'
- [`canair investigate`](investigate.md) — Point this at an unknown signal and get one ranked table telling you everything worth knowing about each of its bytes.
- [`canair search`](search.md) — Find a string, VIN or byte pattern anywhere in the captures — without already knowing which ECU and PID holds it.
- [`canair coverage`](coverage.md) — Audit PID definitions for decoding gaps.
- [`canair research`](research.md) — Report open reverse-engineering work from ecus/ research: sections.

### Authoring

- [`canair pids`](pids.md) — Safely edit ecus/ parameters and research entries (domain A — diagnostic UDS PIDs, freeform WiCAN expressions).
- [`canair signals`](signals.md) — View/edit broadcast signal definitions (domain B).
- [`canair ecu`](ecu.md) — Inspect or edit the profile's ECU registry.
- [`canair bus`](bus.md) — List the active profile's CAN bus segments with their human names, descriptions, and the number of ECUs on each (from can_buses.yaml).
- [`canair states`](states.md) — List the active profile's vehicle operating states, or edit the vocabulary (add/rm/rename/set-description/set-predicate/set-implies/set-excludes).
- [`canair groups`](groups.md) — List the active profile's named selector groups, or edit the vocabulary (add/rm/rename/set-description/set-members).
- [`canair wican`](wican.md) — Build and sync the WiCAN device's AutoPID profile.
- [`canair validate`](validate.md) — Validate a profile's data files against their schemas and report problems.

### Import / export

- [`canair import`](import.md) — Import external CAN data into the active profile.
- [`canair export`](export.md) — Export canair data to interchange formats.

### Setup

- [`canair profile`](profile.md) — List, inspect, and create vehicle profiles — the per-vehicle bundles that hold all the reverse-engineering data.
- [`canair config`](config.md) — View and manage user configuration.
- [`canair update`](update.md) — Update the CLI from its git-clone install.
- [`canair completion`](completion.md) — Enable `canair` tab-completion (subcommands, flags, ECU/PID names).

### Other

- [`canair reset`](reset.md) — Restart one ECU with ECUReset (0x11), then wait until it answers TesterPresent again.
- [`canair action`](action.md) — Profile-defined actions: named requests for make-specific things such as putting the car to sleep.
- [`canair memory`](memory.md) — Read ECU memory with ReadMemoryByAddress (0x23) into dumps kept on this machine, and list, compare and export them.
- [`canair variants`](variants.md) — List the variants of the active profile's car — the versions an owner can name, such as a battery size or a facelift — or edit them (add/set-meta/unset-meta/set-revisions/rename/rm).
- [`canair contribute`](contribute.md) — Open a pull request contributing the active profile upstream.
- [`canair bix`](bix.md) — Convert byte indices between WiCAN, ISO-TP, Torque, and OBDb notations.
- [`canair logs`](logs.md) — View the central rotating diagnostics event log.
