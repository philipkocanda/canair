# Raw CAN Backend (SLCAN over TCP) — Implementation Plan

Add a **raw-CAN backend** to canair alongside the existing ELM327 WebSocket
terminal, using the WiCAN's SLCAN-over-TCP mode. Deliver **passive sniffing
first**, then **client-side ISO-TP + pipelined UDS** as a faster monitor backend.

## Decisions (locked)

| Question | Choice |
|---|---|
| Backend | **SLCAN over TCP** (also covers the SocketCAN / BUSMASTER device modes) |
| First capability | **Sniffing first**, then pipelined UDS |
| Mode switching | **Auto-switch + restore to `elm327` on exit, with a consent prompt** |
| Dependencies | **python-can stack** (`python-can`, later `can-isotp` + `udsoncan`) |

## Key facts (from firmware research + current code)

- WiCAN mode is a single boot-time `protocol` (`slcan`/`savvycan`/`realdash66`/
  `elm327`/`auto_pid`), changed only by rewriting device config
  (`GET /load_config` → edit → `POST /store_config`) then a **~2–5 s reboot**.
  Not live-switchable; a raw session pauses ELM327/AutoPID (and Home Assistant).
- Raw modes carry **single CAN frames only — no device-side ISO-TP**. We build
  ISO-TP + UDS client-side. Raw RX filter is accept-all → passive sniff + TX.
- We already have HTTP config plumbing (`wican_api.get_config`/`get_status`;
  `commands/wican.py` POSTs) and a single-connection `flock` (`lock.py`). No
  `python-can`/`isotp` deps yet (all hand-rolled today).
- ⚠️ The firmware checkout (v4.13) is **older than the actual Pro** (no `/ws`
  elm327 handshake, which the Pro uses). Exact Pro ports / `protocol` names /
  whether SLCAN is TCP 3333 vs `/ws` **must be verified live** (Phase 0).
- python-can's built-in `slcan` interface is serial-only → we write a small
  custom `can.BusABC` that speaks SLCAN ASCII over TCP; then `Notifier`,
  `Logger`, `can-isotp`, `udsoncan` all layer on top.

## Phase 0 — Verify on the real WiCAN Pro (gating)

- `GET /load_config` + `GET /check_status`: enumerate `protocol` values, `port`,
  `port_type`; confirm `slcan` availability and how it's reached on the Pro
  (TCP `3333` vs a `/ws` SLCAN handshake).
- Briefly switch to `slcan`, connect, confirm frames flow, switch back.
- Output: documented Pro specifics that pin the transport (TCP vs WS).

## Phase 1 — Raw transport + `canair sniff` (no ISO-TP)

- **Dep:** add `python-can` to `pyproject.toml`.
- **`canlib/transport/slcan_tcp.py`** — `SlcanTcpBus(can.BusABC)`: TCP socket to
  `host:port`; SLCAN open/close/bitrate (`C`/`Sx`/`O`, optional `Z1`
  timestamps); `send()` → `t`/`T` frames; `_recv_internal()` with line buffering
  → `can.Message`. Pure framing/parse unit-tested against a fake socket.
- **`canlib/wican_mode.py`** — `get_protocol()`, `set_protocol(name)`
  (load → mutate → `POST /store_config` → await reboot + reconnect), and a guard
  that **switches to `slcan` with consent on entry and restores `elm327` on exit**
  (try/finally + SIGINT-safe). Reuses `wican_api` + `WiCANLock`.
- **`canlib/commands/sniff.py`** — `canair sniff`: open bus (auto-switch w/
  consent), capture via python-can `Notifier`; live view (reuse the Textual
  scroll UI) with per-ID **count / period (Hz) / last data / changed bytes**;
  flags `--filter`, `--duration`, `--listen-only`, `--save` (python-can `Logger`:
  `.asc`/`.blf`/`.csv`).
- **Config/profile:** `can_bitrate` (default `500000`; Ioniq = ATSP6) + raw
  `port` (default `3333`).

## Phase 2 — ISO-TP + UDS + pipelining (larger, follow-on)

- Add `can-isotp` + `udsoncan`. One `isotp` stack per ECU (tx/rx addressing)
  sharing the bus via a Notifier; `udsoncan` (or a thin UDS layer) for
  `readDataByIdentifier`.
- **Pipelining:** create stacks for all target ECUs, fire requests without
  waiting, collect responses concurrently (demuxed by response ID) → overlaps
  ECU think-time.
- **Monitor integration:** backend abstraction so `MonitorController` targets
  either the ELM327 terminal (default) or the raw UDS client (`--raw`); bridge
  python-can threads ↔ asyncio via a thread→`asyncio.Queue`. Reuse
  `decode_param_rows` + the same profile PIDs. Measure vs the optimized ELM path.

## Cross-cutting

- ELM327 terminal stays the **default**; raw is opt-in per command.
- **Tests:** SLCAN framing/parse (unit, fake socket), mode-switch HTTP (mocked
  `requests`), sniff aggregation (pure), Phase-2 isotp/UDS over a fake bus.
  Device tests are manual.
- **Docs:** `SKILL.md` + `AGENTS.md` for the new mode and `canair sniff`.

## Risks / call-outs

- One mode at a time + reboot to switch: a raw session fully takes over the
  device and pauses Home Assistant; restore-on-exit must be bulletproof
  (two reboots per session).
- python-can is thread/blocking: Phase 1 sniff can be synchronous; Phase 2
  monitor needs the thread↔asyncio bridge.
- Pro specifics unknown until Phase 0 can change the transport (TCP vs `/ws`;
  same SLCAN framing either way → `SlcanTcpBus` vs `SlcanWsBus`).

## Status

- [~] Phase 0 — device verification **(blocked: WiCAN offline / car asleep at
  12.4 V)**. Retry `GET /load_config` when reachable to confirm the Pro's
  `protocol` values + whether SLCAN is TCP `3333` or tunneled over `/ws`.
- [x] Phase 1 — code + unit tests (all hardware-independent, tested against fakes):
  - `python-can` dependency added.
  - `canlib/transport/slcan_tcp.py` — `SlcanTcpBus` + `format/parse_slcan_frame`
    (`tests/test_slcan_tcp.py`, 24 tests).
  - `canlib/wican_api.py` — `store_config` (POST /store_config).
  - `canlib/wican_mode.py` — `set_protocol` + `protocol_mode` consent/restore
    guard (`tests/test_wican_mode.py`, 9 tests).
  - `canlib/commands/sniff.py` + `_sniff_tui.py` — `canair sniff` (per-ID live
    table, `--save`/`--filter`/`--listen-only`/`--duration`, ansi-dark TUI +
    non-TTY fallback) (`tests/test_sniff.py`, 12 tests).
- [ ] **Phase 1 on-device verification** — run `canair sniff` against the real
  Pro once awake: confirm the mode switch, live frames, and restore-on-exit;
  verify the SLCAN transport assumption (TCP 3333 vs `/ws`).
- [ ] Phase 2 — ISO-TP + UDS pipelining + monitor `--raw` backend.

