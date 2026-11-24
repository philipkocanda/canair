# Live device

**Every command in this file except `lock` and `logs` talks to the car: run none without the
user's explicit approval.**

- **`read`** (alias `query`) — Custom CAN/UDS requests. **Prefer positional query steps**
  (`canair read BMS:2101`; `query/` handles sessions, wake and keepalives). **Bind each PID to its
  ECU with a colon (`IGPM:22BC07`), never a space**: a space separates *independent ECU
  selectors*, so `"query IGPM 22BC07"` means "all of IGPM plus a bogus ECU" and is rejected.
  `@group` expands to a saved selector set. A token picks PIDs by prefix *or* suffix (`VCU:22`);
  an undefined token is inferred or refused, never guessed (`query/__init__.py::inferred_request`);
  OBD-II is `BMS:01/0D`. TesterPresent keepalives are automatic. `--reboot` restores AutoPID.
- **`monitor`** (alias `mon`) — Live view; same steps as `read`. Recording (`--save`/`--label`/
  `--state`/`--notes`) and the keep-modes live **here, not on `read`**. Bare `monitor` on a TTY
  opens a picker. Polling is per-PID with backoff. Design: `live/monitor/`.
- **`scan`** — Kinds `range` (bare `scan <ECU>`), `sessions`, `routines`, `iocontrol`, `memory`.
  All are **safe probes** except a KWP `0x30` sweep and a `range` of an actuating service, which
  ask first. Hits go to the ECU's `routines:`/`iocontrol_discoveries:`/`memory:`. Without
  `--range`, `range` sweeps the ECU's DID families (`ecus/did_families.py`).
- **`discover`** (alias `disc`) — Which ECUs answer; `--register` writes a new ECU into `ecus/`;
  `--functional` sends one allow-listed read on the functional id. That allow-list is never
  widened by `--unsafe` (`safety/blocklist.py::check_functional_request`).
- **`identity`** (alias `id`) — Identity DIDs/records, printed only. Nothing is written to the
  ECU's `identity:` (it can hold a live VIN: see the PII rules in **contributing-profiles**).
- **`io`** / **`routines`** — IOControl and RoutineControl. **Mutative**: the user approves each
  actuation. Actuators auto-release when the session ends. `io` refuses a KWP `0x30` ON until the
  entry declares a `release:` — never declare one you have not seen hand the output back on the
  car (`safety/actuation.py`).
- **`reset`** — ECUReset `0x11`, off until the user sets `safety.allow_reset`. Never enable that
  key for the user. Rules: `safety/reset.py`.
- **`action`** — The profile's guarded `actions.yaml`. Every request passes the blocklist
  (`--unsafe` plays no part). A `frames:` action injects a broadcast frame (`slcan-tcp` only), off
  until the user sets `safety.allow_frame_injection` — **never enable it**. Design: `actions/`.
- **`memory`** — Memory reads (`0x23` only). **Dumps are user state**
  (`<config dir>/memory/<profile>/`), never profile data; a memory PID never ships to AutoPID.
  Docs: `docs/concepts/memory-reads.md`; design: `memory/`, `live/memory/`.
- **`dtc`** — Read, clear (`--clear`) and `dtc log`. **The DTC log is user state, not a bundle
  member** (`<config dir>/dtc_log/<profile>.yaml`). Design: `dtc/`, `live/dtc/`.
- **`sniff`** — Passive sniffer, **`slcan-tcp` only**; the only way to see broadcasts.
- **`raw`** (hex in, hex out; last resort) and **`repl`** (ELM327 terminal, ELM transports only).
- **`status`** (alias `st`) — What am I talking to, and is it usable? It runs the same fallback
  probe as a live command. Never resolve only the selected device there.
- **`lock`** — Inspect or clear the device connection mutex. **canair never kills another process
  on its own**: `--force` asks the holder to leave, then names the PID and the `kill` to run.
  Design: `device/lock.py`.
- **`logs`** — The central diagnostics log (`~/.config/canair/logs/canair.log`), written by
  `transport/stats.py`.
