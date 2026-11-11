---
name: wican-hardware-and-protocol
description: "Ground truth about the WiCAN device itself — the WiCAN Pro hardware (ESP32-S3 + MIC3624/STN2120 co-processor), the two physically distinct paths from canair to the vehicle CAN bus, the firmware's protocol modes and their mutual exclusions, the WebSocket/HTTP API surface, sleep/power behaviour, and measured ELM327 throughput limits. Load this ONLY for deep protocol, transport or device-API work — changing a transport backend in src/canair/transport/, debugging ISO-TP/ELM327 desync or timeouts, adding a WiCAN API call, reasoning about throughput/latency, or reading the wican-fw C source. NOT needed for ordinary work: decoding signals (use reverse-engineer-signal), profile data (use contributing-profiles), or general canair code changes (use contributing-code)."
---

# WiCAN hardware, firmware and protocol — ground truth

Everything here was established by reading the **wican-fw C source** and by **measuring a live
device**, on 2026-08-09; citations were re-based onto `v4.52p` on 2026-10-20. Each claim carries a
`file:line` citation or is labelled as measured. Where the source does not settle a question, this
skill says so rather than guessing.

**Every line number and version in this skill is relative to this exact basis. Re-check it before
trusting a citation** — a moved line is silent, and a wrong-branch citation looks perfectly real
([Rule 0](firmware-source.md)).

| Basis | Value |
|---|---|
| `wican-fw` branch | `wican-pro` |
| `wican-fw` commit | `fab54ad` |
| `wican-fw` tag | `v4.52p` |
| Schematic | `wican-fw/sch/wican_obd_pro_sch_v151.pdf` rev **V1.51**, **sheet 3 of 3 only** |
| Device firmware | **`v4.50p`** (`WiCAN-OBD-PRO`) — two releases *behind* the checkout |
| ELM327 co-processor | `MIC3624 V2.3` board carrying `STN2120 v5.8.1` (`STDI` / `STI`), chip firmware `V2.3.22` |
| ELM327 identity | `ELM327 v2.3` (`ATI`) |

The checkout being ahead of the device is tolerable here **only** because, between `v4.50p` and
`v4.52p`, the WS router and `wican-fw/main/config_server.c` are byte-identical, and
`wican-fw/main/elm327.c` differs only in the bundled chip firmware (`V2.3.22` → `V2.3.24`, which
shifts every `elm327.c` line from 1370 on by +3; the citations are already shifted). That is a
fact about these tags, not a standing guarantee (Trap 3 in
[`firmware-source.md`](firmware-source.md)). The device's measured chip firmware is `V2.3.22`;
`V2.3.24` is unmeasured.

Companion analysis with the full throughput argument and the reclaimable-win ranking:
`plans/2026-08-09-wican-ws-throughput-ceiling.md`.

**Do not load this skill for normal work.** It is for people editing
`src/canair/transport/`, chasing a desync or timeout, calling a device API, or reading the firmware.

**Every device call in this skill needs the user's explicit approval** (`AGENTS.md` → The car):
`canair status`, `canair repl`, a timing run, an HTTP or WebSocket route, a reboot. Read the
firmware source freely. Propose any call against the device, and wait for an explicit "yes".

## Where the detail lives — load only the file you need

| Task | Read |
|---|---|
| Reading `wican-fw` at all: the branch, the dead emulator and router copies, the tag, v6 | [`firmware-source.md`](firmware-source.md) (Rule 0) |
| GPIO map, chip identity, the three OBD CAN interfaces, gateway segments, the MIC3624 UART link and chip reflash | [`hardware.md`](hardware.md) |
| Protocol modes and their mutual exclusions, `/ws` routing and framing, HTTP routes, `/check_status` fields, sleep and wake | [`firmware-api.md`](firmware-api.md) |
| Latency numbers, the frame-count digit, `ATAT`/`ATST`, `STPX`, per-ECU `ATCRA`/flow control, `ATCRA` and EEPROM housekeeping | [`elm327-performance.md`](elm327-performance.md) |

Facts that bite most often, each detailed in the file named:

- **Read the `wican-pro` branch.** `main` is classic-WiCAN firmware, and the Pro tree carries a
  compiled-out emulator and a dead `wican-fw/main/ws_router.c` (`firmware-source.md`).
- **`/ws` has one connection slot.** A second client overwrites the first, so `canair lock` is
  load-bearing (`firmware-api.md`).
- **The terminal pauses AutoPID**, and an ELM327 request holds it off for 10 s
  (`firmware-api.md`).
- **One command per WebSocket frame.** Every command flushes the UART first, so pipelining and
  CR-batching cannot work (`hardware.md`, `firmware-api.md`).
- **The ELM327 cost is the adapter's end-of-response wait.** A learned frame-count digit removes
  it, and an undercount desynchronises the pipe (`elm327-performance.md`).
- **`ATCRA` and `ATAT` persist across sessions**, and `ATSP` writes the chip EEPROM
  (`elm327-performance.md`).

## The two paths to the vehicle bus — the fact that matters most

There is **one** CAN controller on the ESP32 and **one** co-processor, and they are *separate routes
to the car*. Which one carries your traffic is decided by the device's configured protocol mode.

```
                            ┌── UART1 @2Mbaud ── MIC3624/STN2120 ── CAN ── car
canair ── WiFi ── ESP32-S3 ─┤     (elm327, auto_pid)
                            └── TWAI GPIO2/GPIO1 ─ transceiver ── CAN ── car
                                  (slcan, realdash66, savvycan, MQTT)
```

| | `wican-ws` / `elm327-tcp` | `slcan-tcp` |
|---|---|---|
| Device mode | `elm327`, `auto_pid` | `slcan` |
| Silicon on the bus | **MIC3624** | **ESP32-S3 TWAI** |
| ISO-TP done by | the chip | **canair** (`src/canair/transport/raw/isotp_stack.py`) |
| ESP32 TWAI state | **disabled** | enabled |

**Under `auto_pid`/`elm327` the TWAI controller is not idle — it is off.** `can_enable()` is
explicitly commented out on the Pro AutoPID path (`wican-fw/main/main.c:902-907`):

```c
if(protocol == AUTO_PID)
{
    // can_set_bitrate(can_datarate);
    // #if HARDWARE_VER != WICAN_PRO
    // can_enable();
    // #endif
```

and the TWAI→ELM327 frame hand-off is compiled out entirely
(`wican-fw/main/main.c:432-438`, `#if HARDWARE_VER != WICAN_PRO`). `can_enable()` on Pro happens
only for `REALDASH` (`wican-fw/main/main.c:873`), `SAVVYCAN` (`:878`), and when MQTT is enabled
(`:956-961`).

Consequences to design around:

- **`canair wican mode set` must change the device mode, not just the client transport** — it is
  switching which chip is on the bus. This is why the command auto-aligns `transport.type`.
- The two transports have **different failure modes, different filtering and different silicon**. A
  bug reproduced on one says nothing about the other.
- `canair sniff` is `slcan-tcp`-only for a hardware reason, not a software one.
- `elm327_init(...)` is called unconditionally on Pro for **every** protocol
  (`wican-fw/main/main.c:897`), so the chip link always exists even in `slcan` mode.

## Checklist before changing transport code

- [ ] `wican-fw` on `wican-pro`, and the tag diffed against the device's `git_version`.
- [ ] Device firmware generation known (`canair status`, approved or run by the user); anything
      that uses a 4.x-only interface
      (`/ws`, the protocol mode, the legacy HTTP routes, AutoPID sync) is gated through
      `src/canair/device/wican/firmware.py`'s policy table, not an ad-hoc version check.
- [ ] Not reading the `!= WICAN_PRO` emulator block, or `wican-fw/main/ws_router.c`.
- [ ] Clear on which chip the change affects — MIC3624 (`wican-ws`, `elm327-tcp`) or ESP32 TWAI
      (`slcan-tcp`) — and whether it must work on both.
- [ ] New `send_uds` calls pass `expected_sid` **and** `expected_echo` (a desync is otherwise
      undetectable — see `docs/development/link-health.md`).
- [ ] `set_header` + `send_uds` held inside one `terminal.transaction()`.
- [ ] Any adapter setting written outside the init string is reset, or added to the init string.
- [ ] Feature gated on a **chip** capability probe, not a WiCAN firmware version — the ESP32
      reflashes the chip independently.
- [ ] `elm327-tcp` clones are not STN parts: no `ST*` command may be unconditional.
