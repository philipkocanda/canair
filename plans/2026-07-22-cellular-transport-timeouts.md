# Cellular Timeouts on VCU/MCU — Diagnosis & Transport Options

Analysis of a drive session (2026-07-22, "Driving from kidswijs to home") where
VCU and MCU saw many timeouts while ESC/EPS/AAF did not, and all frames were
slow to appear. WiCAN was reachable over the **cellular/VPN** link (iPhone
hotspot). This doc records the root cause and the transport choices that do vs
don't help. **No code/config changes made — analysis only** (per decision).

## Symptom

- Lots of timeouts querying **VCU** (0x7E2) and **MCU** (0x7E3) during the drive.
- **ESC** (0x7D1) and **EPS** (0x7D4) were *not* timing out. AAF (0x7E6) also fine.
- Everything was slower — longer gaps between frames for all ECUs.
- WiCAN on cellular data (VPN address `192.168.3.2`).

## Confirmed facts

- **Effective transport was `slcan-tcp`** at `192.168.3.2:35000` (the `vpn`/cellular
  address). `canair config show` resolves this from repo `config.yaml`
  (`transport.type: slcan-tcp`, `default_wican: vpn`); `~/.config/canair/config.yaml`
  has no `transport:` block.
- **Not total failure — an elevated drop rate.** Scoped to the driving sessions,
  captures still landed: ESC 608, MCU 518, VCU 460, EPS 447, AAF 133
  (`canair captures --summary --date 2026-07-22 --state driving`). VCU/MCU are
  down ~15-25% vs ESC, consistent with dropped/timed-out requests, not a dead ECU.
- **Payload size is NOT the differentiator.** ESC `22C101` is a 48-byte
  multi-frame response (larger than VCU 2101/2102 ~22-23 B) yet had the *most*
  successful captures. So "bigger response = more timeouts" is wrong here.
- **`slcan-tcp` = host-side ISO-TP.** canair runs the `can-isotp` stack in Python;
  the WiCAN just bridges raw CAN frames. Per-request `recv` timeout is per whole
  UDS response (2.0 s `RawTerminal` / 3.0 s monitor); per-frame guards are
  `rx_flowcontrol_timeout`/`rx_consecutive_frame_timeout` = 1000 ms
  (`canlib/transport/uds_raw.py`, `raw_terminal.py`).

## Root cause

With `slcan-tcp`, a multi-frame UDS response requires a round trip *mid-transfer*:

    ECU -> First Frame -> canair sends Flow Control (0x30) back -> ECU -> Consecutive Frames

On cellular, **every** frame — including the tester's Flow Control frame — makes
a full round trip across the laggy/jittery/lossy link. That directly explains
"longer between frames for all of them" (each frame now carries a network RTT),
and it makes multi-frame transfers fragile: if the Flow Control / consecutive
frames arrive late, the ECU can abort the transfer. TCP head-of-line blocking on
packet loss compounds this for multi-frame reads.

## Why VCU/MCU specifically (hypothesis, well-supported but not proven)

Since it isn't payload size, the differentiator is ECU-side:

- **VCU/MCU are the powertrain/EPCU ECUs** — busiest on the bus during an active
  drive (real-time motor control), with a **low-priority, KWP2000 (`21xx`),
  session-based** diagnostic path (only answer inside a `1081` session kept alive
  by TesterPresent).
- Added round-trip latency pushes their tight internal inter-frame/session
  timeouts over the edge intermittently -> higher drop rate.
- ESC/EPS (chassis, UDS `22xx`, stateless) and AAF (thermal) have timing headroom
  and no session to lapse, so they rode out the latency.

## Does switching transport protocol help? (e.g. SavvyCAN binary)

**No — SavvyCAN and RealDash do not help.** The axis that matters is *where ISO-TP
runs*, not the wire encoding (ASCII vs binary). Firmware evidence
(`wican-fw/main/`):

| Mode | Wire format | ISO-TP location |
|------|-------------|-----------------|
| **slcan** (current `slcan-tcp`) | ASCII | **Host** (canair) |
| **savvycan / GVRET** | Binary | **Host** |
| **realdash66** | Binary | **Host** |
| **elm327** (canair `wican-ws`) | ASCII | **Device** |
| **auto_pid** | ASCII -> JSON | **Device** |

- SavvyCAN is the **GVRET raw-frame binary protocol** — `0xF1` opcodes,
  `BUILD_CAN_FRAME`, DLC hard-capped at 8 bytes (`gvret.c:347-382`, `gvret.h:48-65`).
  **No** First Frame / Flow Control / Consecutive Frame handling at all.
- Only `elm327.c` and `autopid.c` contain flow-control logic: `elm327.c:878-925`
  inspects `0x10/0x20/0x30` PCI and sends its own `0x30` FC frame
  (`elm327.c:713-742`); auto_pid reuses that engine (`main.c:483`).
- Binary encoding only trims bytes per frame; it does **not** reduce the number of
  round trips. Cellular pain is dominated by RTT/jitter/loss, not bandwidth — so
  SavvyCAN would still hit the late-Flow-Control aborts on VCU/MCU.

## What actually helps: terminate ISO-TP on the dongle

Only the two device-side-ISO-TP modes fix the mechanism (FF/FC/CF stay on the CAN
bus at native speed; only the reassembled payload crosses cellular, once):

- **`elm327` (= canair `wican-ws`)** — for interactive ad-hoc drive queries.
  - Caveat 1: multi-frame **transmit** not implemented — requests >7 bytes are
    rejected (`elm327.c:806-814`). All current reads (`2101`, `22C101`, …) are
    short, so fine today; would block a future long request.
  - Caveat 2: canair read loop has an early-break heuristic
    (`canlib/terminal.py:154-172`) that could truncate a response if cellular
    delays a term_out chunk >1 s — worth an empirical check on cellular.
- **`auto_pid` + MQTT** — for hands-off logging during a drive. Dongle polls the
  profile PIDs itself (device-side ISO-TP) and pushes decoded values over MQTT —
  fully async, most latency-tolerant. Config-driven (not interactive);
  `/autopid_data` is cached/last-value.
- Keep **`slcan-tcp`** for home LAN and for `canair sniff` (passive broadcast
  capture), where host-side raw frames are what you want and latency is low.

## Recommendation (not yet implemented)

- Interactive querying over cellular -> use `wican-ws` (e.g. `--transport wican-ws`
  for cellular sessions, or bind `vpn` -> `wican-ws` while keeping `home` ->
  `slcan-tcp`).
- Passive/continuous logging over cellular -> `auto_pid` + MQTT.

## Status

- [x] Root cause identified (host-side ISO-TP over cellular; FC round-trip).
- [x] SavvyCAN/RealDash ruled out (raw-frame bridges; firmware-confirmed).
- [ ] No changes made — user opted for analysis only. Revisit if cellular drive
      querying is needed again.
