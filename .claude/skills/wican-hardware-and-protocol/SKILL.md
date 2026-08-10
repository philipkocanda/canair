---
name: wican-hardware-and-protocol
description: "Ground truth about the WiCAN device itself — the WiCAN Pro hardware (ESP32-S3 + MIC3624/STN2120 co-processor), the two physically distinct paths from canair to the vehicle CAN bus, the firmware's protocol modes and their mutual exclusions, the WebSocket/HTTP API surface, sleep/power behaviour, and measured ELM327 throughput limits. Load this ONLY for deep protocol, transport or device-API work — changing a transport backend in canlib/transport/, debugging ISO-TP/ELM327 desync or timeouts, adding a WiCAN API call, reasoning about throughput/latency, or reading the wican-fw C source. NOT needed for ordinary work: decoding signals (use reverse-engineer-signal), profile data (use contributing-profiles), or general canair code changes (use contributing-code)."
---

# WiCAN hardware, firmware and protocol — ground truth

Everything here was established by reading the **wican-fw C source** and by **measuring a live
device**, on 2026-08-09. Each claim carries a `file:line` citation or is labelled as measured.
Where the source does not settle a question, this file says so rather than guessing.

**Every line number and version below is relative to this exact basis. Re-check it before
trusting a citation** — a moved line is silent, and a wrong-branch citation looks perfectly real
(Rule 0).

| Basis | Value |
|---|---|
| `wican-fw` branch | `wican-pro` |
| `wican-fw` commit | `10672628cc662665e8ad0d993e6a71d7f1d8813f` (`1067262`, "idf v6.0.2") |
| `wican-fw` tag | `v4.51p_beta-01` |
| Schematic | `wican-fw/sch/wican_obd_pro_sch_v151.pdf` rev **V1.51**, **sheet 3 of 3 only** |
| Device firmware | **`v4.50p`** (`WiCAN-OBD-PRO`) — one release *behind* the checkout |
| ELM327 co-processor | `MIC3624 V2.3` board carrying `STN2120 v5.8.1` (`STDI` / `STI`) |
| ELM327 identity | `ELM327 v2.3` (`ATI`) |

The checkout being ahead of the device is tolerable here **only** because
`wican-fw/main/elm327.c` and the WS router are byte-identical between `v4.50p` and
`v4.51p_beta-01` (Trap 3). That is a fact about these two tags, not a standing guarantee.

Companion analysis with the full throughput argument and the reclaimable-win ranking:
`plans/2026-08-09-wican-ws-throughput-ceiling.md`.

**Do not load this skill for normal work.** It is for people editing
`canlib/transport/`, chasing a desync or timeout, calling a device API, or reading the firmware.

## Rule 0 — read the right source, or every conclusion is wrong

This cost a whole audit round. **Three separate traps, all of which produce plausible-looking but
wrong citations.**

### Trap 1: the branch

`wican-fw/` is a gitignored checkout. Its **`main` branch is the classic-WiCAN firmware and does not
describe the Pro.** Confirm before reading anything:

```
git -C wican-fw rev-parse --abbrev-ref HEAD    # want: wican-pro
git -C wican-fw describe --tags                # want: v4.51p_beta-01 or later
```

| | `main` | `wican-pro` |
|---|---|---|
| `HARDWARE_VER` | `WICAN_V300` | `WICAN_PRO` (`wican-fw/CMakeLists.txt:48`) |
| SoC | `esp32c3` | `esp32s3` (`wican-fw/sdkconfig:650`) |
| Tag suffix | `v4.21` | `p` suffix — `v4.50p`, `v4.51p_beta-01` |
| ELM327 | **software emulation** | **proxied to a hardware chip** |

`git diff --stat main wican-pro` = **361 files changed, 78 376 insertions**. Eleven whole components
exist only on `wican-pro` (`autopid`, `ws_server`, `obd_logger`, `cmdline`, `vpn_manager`,
`cert_manager`, `ha_webhooks`, `https_client_mgr`, `esp_wireguard`, `restart_tracker`,
`wican_common`), as do `wican-fw/main/obd.c`, `wican-fw/main/ws_router.c` and `wican-fw/main/web/`.

`wican-ws` is **Pro-only**, so anything read on `main` about ELM327 behaviour is about hardware this
project never talks to.

### Trap 2: two `elm327_process_cmd`, two `ws_router.c`

The Pro tree still carries the classic software emulation, and it is **compiled out**:

- `wican-fw/main/elm327.c:65` opens `#if HARDWARE_VER != WICAN_PRO` — the emulator, including
  `wican-fw/main/elm327.c:70-71` (`device_description = "ELM327 v1.3a"`, `identify = "OBDLink MX"`)
  and `elm327_process_cmd` at `wican-fw/main/elm327.c:1025`. **Not on the `wican-ws` path.**
- The live path is `elm327_run_command` at `wican-fw/main/elm327.c:2534`.
- The signature itself splits at `wican-fw/main/elm327.h:42-48` — the non-Pro variant takes a
  `twai_message_t*`, the Pro variant a response callback. **If the function you are reading takes a
  TWAI frame, you are in the emulator.**

Same trap again: **`wican-fw/main/ws_router.c` is dead code, not in the build** — `ws_router`
appears nowhere in `wican-fw/main/CMakeLists.txt`. The compiled copy is
`wican-fw/components/ws_server/ws_router.c` (`wican-fw/components/ws_server/CMakeLists.txt:4-7`).
The two are near-identical and **differ by ~40 lines in numbering**, so a wrong citation lands on a
real-looking line. Check with:

```
rtk grep -rn ws_router wican-fw/main/CMakeLists.txt      # must return nothing
```

### Trap 3: the checked-out tag is not necessarily the running firmware

Get the device's own version from `canair status` (or `/check_status`, field `fw_version`), then
diff the files you care about across tags before trusting a line number:

```
git -C wican-fw diff --stat v4.50p v4.51p_beta-01 -- main/elm327.c
```

For the 2026-08-09 audit that returned empty for both `wican-fw/main/elm327.c` and the router, so
the citations held. Do not assume it.

## The hardware

Four variants, selected by a **hand-edited CMake line — no runtime detection, no Kconfig option, no
probe** (`wican-fw/CMakeLists.txt:7-10`, selected at `:45-51`):

| Variant | # | `_STR` | Binary prefix |
|---|---|---|---|
| `WICAN_V210` | 1 | `OBD` | `obd` |
| `WICAN_V300` | 2 | `OBD` | `obd` |
| `WICAN_USB_V100` | 3 | `USB` | `usb` |
| `WICAN_PRO` | 4 | `OBD-PRO` | `obd_pro` |

77 sites branch on `HARDWARE_VER`. The reported string is `"WiCAN-%s"` built at
`wican-fw/main/config_server.c:1757` from `-DHARDWARE_VERSION`, and the binary name embeds
`git describe --tags --always --dirty` (`wican-fw/CMakeLists.txt:33-43`).

**WiCAN Pro** = ESP32-S3 + a **MIC3624** OBD co-processor, whose silicon is an **STN2120**. GPIO map
(`wican-fw/main/hw_config.h:26-46`; non-Pro at `:51-57`):

| Signal | Pro GPIO | non-Pro GPIO |
|---|---|---|
| TWAI TX / RX | 2 / 1 | 0 / 3 |
| CAN transceiver standby | 38 | 6 |
| UART1 to MIC3624 (TX/RX) | 16 / 15 | — |
| UART2 external ELM327 (TX/RX) | 17 / 18 | — |
| `OBD_RESET_PIN` / `OBD_READY_PIN` / `OBD_SLEEP_PIN` | 41 / 7 / 9 | — |
| Button / IMU interrupt | 8 / 3 | 8 (button) |

Pro also has SDMMC 4-bit (SD card) and an ICM-42670 IMU. Schematic:
`wican-fw/sch/wican_obd_pro_sch_v151.pdf` (binary — not readable by grep).

### Chip identity, measured

| command | reply |
|---|---|
| `STI` | `STN2120 v5.8.1` |
| `STDI` | `MIC3624 V2.3` |
| `ATI` | `ELM327 v2.3` |
| `AT@1` | `OBDII to RS232 Interpreter` |
| `AT@2` | `?` (the reference for an unsupported command) |

`MIC3624` is the **module** identity the firmware matches on; `STN2120` is the silicon underneath,
so the **full ST command set is available**. The strings `STN2120` and `STDI` appear **nowhere in
the firmware source** — they come from the chip. Firmware matches `VTVERS`'s reply prefix at
`wican-fw/main/elm327.c:2996-2999`, setting `device_type = 0x3624`.

> **DEFECT in canair:** `canlib/transport/elm327_terminal.py:88-92` claims the WiCAN answers `ATI`
> with `OBDLink MX`. It answers `ELM327 v2.3`. `OBDLink MX` is the *emulator's* string
> (`wican-fw/main/elm327.c:71`), which is not compiled on Pro.

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
| ISO-TP done by | the chip | **canair** (`canlib/transport/isotp_stack.py`) |
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

## CAN buses — three at the OBD connector, one driven by the ESP32, several in the car

Three different things get called "the CAN bus" here, and conflating them has produced wrong
conclusions in both directions. Keep them apart.

### Hardware: the OBD connector breaks out THREE CAN interfaces

The `OBD Connector` block of `wican-fw/sch/wican_obd_pro_sch_v151.pdf` carries three independent
CAN interfaces:

| Interface | Nets at the connector | Driven by, in this firmware |
|---|---|---|
| HS-CAN | `HS_CAN_H` / `HS_CAN_L` | the ESP32-S3 TWAI controller, through one transceiver |
| MS-CAN | `MS_CAN_H` / `MS_CAN_L` | **nothing in the ESP32 firmware** |
| SW-CAN | `SW_CAN` (single wire) | **nothing in the ESP32 firmware** |

meatpi issue [#708](https://github.com/meatpiHQ/wican-fw/issues/708) — "Documentation of additional
CAN interfaces", opened 2026-03-20 — asks whether the second and third transceivers are identical
and how to address the pins. It is **still open with no reply**, so there is no vendor documentation
of MS-CAN/SW-CAN addressing. Treat those two as *present but unreachable* until proven otherwise.

**Reading that schematic:** its text is converted to vector outlines (zero `/Font` objects, zero
`Tj`/`TJ` operators), so net names **cannot be grepped or extracted** — inflating its streams yields
only path geometry. Open it as an image instead. Note also that only **sheet 3 of 3** is published,
the ESP32 sheet; the MIC3624/STN2120 sheet is absent, which is why the co-processor's bus wiring
below is inference rather than fact.

### ESP32 side: one TWAI controller, wired to HS-CAN only

The TWAI controller is a **singleton with no bus/channel/port parameter** anywhere in its API
(`wican-fw/main/can.h:53-67`: `can_enable`, `can_disable`, `can_send`, `can_receive`, `can_init`,
`can_set_bitrate`, `can_set_silent`, `can_get_bitrate`, `can_msgs_to_rx`), instantiated statically
at `wican-fw/main/can.c:81-83`.

`wican-fw/main/hw_config.h:31-33` gives the Pro exactly **one** pin trio — `TX_GPIO_NUM 2`,
`RX_GPIO_NUM 1`, `CAN_STDBY_GPIO_NUM 38`. That file declares no second TX/RX pair for any variant,
and `CAN_STDBY` is a single transceiver's standby line (driven low to enable at
`wican-fw/main/can.c:174`, held high for sleep at `wican-fw/main/sleep_mode.c:813`).

**Methodology warning — this is how this file previously got the count wrong.** A grep across
`wican-fw/` for `mcp2515|mcp2517|mcp251xfd|tcan4550|sja1000|CAN_FD|canfd|twai_fd` returns **zero
matches**, and it is tempting to conclude "one CAN interface, no CAN-FD". It supports only the
narrower claim that **the ESP32 firmware drives one classical-CAN controller**. A source grep is
blind to hardware that is present but unsupported, and blind to names you did not think to search —
`SWCAN`, `MS_CAN` and `LIN` were in the tree the whole time. For a *hardware* question read the
schematic; for a *firmware* question read the source.

The undriven interfaces do leave traces, all of them stubs:

- `wican-fw/main/gvret.c:614` — `case SETUP_EXT_BUSES:` carries the comment "setup
  enable/listenonly/speed for SWCAN, Enable/Speed for LIN1, LIN2": the GVRET/SavvyCAN protocol
  reserves slots for them.
- `wican-fw/main/gvret.c:256` sends a literal `0` where the single-wire-mode flag belongs, commented
  "was single wire mode. Should be rethought for this board."
- `CAN1Speed` / `CAN1_Enabled` / `CAN1ListenOnly` at `wican-fw/main/gvret.h:71-73` are likewise
  **wire-protocol fields**, not hardware; only bus 0 is backed (`wican-fw/main/gvret.c:760` sets
  `CAN0_Enabled` only).

Bitrates are classical-CAN only, a static 11-entry timing table indexed by the enum at
`wican-fw/main/can.h:26-37` (`CAN_5K`…`CAN_1000K`, plus `CAN_AUTO`); default `CAN_500K`
(`wican-fw/main/can.c:68`). Two live traps:

- **`CAN_AUTO` is non-functional** — silently rewritten to `CAN_500K` at
  `wican-fw/main/can.c:306-312`, and the auto-bitrate scan loop is commented out at `:329-371`.
- **The RX acceptance filter is forced accept-all** and the configurable filter/mask is commented
  out (`wican-fw/main/can.c:128-133`). So `slcan-tcp` sees every frame on the segment it is wired
  to, and hardware-level filtering is not available to canair on that path.

### Could canair reach MS-CAN or SW-CAN?

**Not today, and never on `slcan-tcp`** — the TWAI controller's one transceiver is wired to HS-CAN.

The only plausible route is the **STN2120**, whose product family does support MS-CAN and
single-wire GMLAN, and which sits on the unpublished sheet where the `MS_CAN_*` / `SW_CAN` nets must
terminate.
**That is inference, not verified:** no ST command anywhere in the firmware selects those buses, and
the wiring cannot be confirmed from what the repo ships. Before building on it, note that protocol
selection is **mutative and persists across sessions** (see the housekeeping trap below) —
`ATDP`/`ATDPN`/`STPRS` are the read-only probes, `ATSP`/`STP` are not.

Sheet 3 does show the ESP32's transceiver driving the *same* `HS_CAN_H`/`HS_CAN_L` nets that land on
the OBD connector, so the two silicon paths most likely share the HS-CAN pins — but confirming that
needs the MIC3624 sheet, so it remains unproven.

### Car side: several segments, bridged by a gateway, and the OBD port reaches only some

This is what canair's `can_buses.yaml` models. The Ioniq declares `ALL`, `B-CAN` (100 kbit/s),
`P-CAN`, `C-CAN`, `MM-CAN`, `H-CAN`, `D-CAN` (all 500 kbit/s) in
`profiles/ioniq-2017/can_buses.yaml`; 15 of 30 ECU files declare a `can_bus:` (5 `[B-CAN]`,
2 `[P-CAN]`, 2 `[H-CAN, P-CAN]`, …).

The distinction that matters for transport work:

- **Diagnostic requests (`22`/`21`/`19`/`2F`/`31`) reach ECUs on other segments** because the
  gateway routes them. This is why one CAN connection at the OBD port can address the whole car, and
  why `0x7BB`/`0x778`/`0x7CE` answer despite not being on the diagnostic segment.
- **Broadcast frames do not cross the gateway wholesale.** A signal that exists on B-CAN or P-CAN is
  generally *not* visible at the OBD port. So `canair sniff` and the whole `signals/` domain see
  only what the gateway forwards onto the port's segment — an absent frame is not evidence the
  signal does not exist.
- **canair does not model which segment the OBD port physically reaches** (no `D-CAN` reference
  anywhere in `canlib/`). `can_bus:` is documentation for humans and for `canair bus`; nothing
  routes on it. Do not add code that assumes otherwise without designing it deliberately.

## The MIC3624 co-processor link

Three UARTs, all build-time gated:

| UART | Variant | Baud | Pins | Role |
|---|---|---|---|---|
| `UART_NUM_1` | Pro | 2 000 000 (fallback 115 200) | 16/15 | **the MIC3624** |
| `UART_NUM_2` | Pro | 2 000 000 | 17/18 | a **wired ELM327 port onto the same chip** |
| `UART_NUM_0` | USB variant | 4 000 000 | default | host serial |

UART1 setup: `wican-fw/main/elm327.c:3446-3464`, buffer `UART_BUF_SIZE = (18*1024)`
(`wican-fw/main/obd.h:21`). UART2 feeds the *same* `elm327_process_cmd`
(`wican-fw/main/wc_uart.c:181`) — it is not a second bus path.

**Baud negotiation** (`elm327_set_baudrate`, `wican-fw/main/elm327.c:2008-2091`): probe with
`VTVERS\r` at 2 Mbaud; if silent, drop to 115 200 and re-probe; then `STSBR 2000000\r` expecting
`OK`, switch the local port, and `STWBR\r` to **persist it in the chip**. Constants at
`wican-fw/main/elm327.c:1318-1329` (`UART_TIMEOUT_MS 1200`, `DESIRED_BAUD_RATE 2000000`,
`DEFAULT_BAUD_RATE 115200`, `ELM327_CMD_MUTEX_TIMOUT 10000`).

**The ESP32 owns the chip's firmware.** Expected versions `OBD_FW_VER_V18 "V2.3.18"` /
`OBD_FW_VER_V22 "V2.3.22"` (`wican-fw/main/elm327.h:27-28`); the image is **embedded in the ESP32
binary** (`wican-fw/main/CMakeLists.txt:113` `EMBED_FILES "obd_fw/V2.3.22.txt"`, symbols at
`wican-fw/main/elm327.c:1370-1371`). On any boot where `VTVERS` does not report the expected version
it **silently reflashes** the MIC3624 (`elm327_update_obd`, `wican-fw/main/elm327.c:3056`, entering
the bootloader with `VTDLMIC3422\r` at `:3083`); it also runs when the chip is stuck out of normal
state, so it doubles as recovery. Forceable over HTTP with
`{"command":"force_update_obd"}` (`wican-fw/main/config_server.c:1383-1386`).

**Implication:** the ST command surface canair sees is pinned by the *ESP32* firmware version,
not by what the chip shipped with. A capability probe should key on the chip reply, not on the WiCAN
version.

Boot sequence: `wican-fw/main/elm327.c:3466-3492` — hard reset → `elm327_update_obd(false)` →
`elm327_powerpin_commands()` → poll `elm327_chip_get_status()` up to 10 × 200 ms → `uart_flush` →
`obd_init()`.

UART1 access is serialised by `xuart1_semaphore` (`wican-fw/main/elm327.c:3441`,
`elm327_lock()` at `:2093-2096`, taken in `elm327_run_command` at `:2536`) — **one mutex around the
whole command+response cycle**, and every command begins with
`uart_flush_input(UART_NUM_1); xQueueReset(uart1_queue);` (`wican-fw/main/elm327.c:2597-2598`).
Those two facts are why **pipelining is impossible on this path** regardless of client behaviour: a
second in-flight request's reply is destroyed by the next command's flush. Full argument in the plan
doc.

## Protocol modes

`#define`s, not an enum, at `wican-fw/main/config_server.h:51-55`; string mapping in
`config_server_protocol()` (`wican-fw/main/config_server.c:521-544`):

| config string | constant | inbound parse | bus route on Pro |
|---|---|---|---|
| `slcan` | `SLCAN` 0 | `slcan_parse_str` | ESP32 TWAI |
| `realdash66` | `REALDASH` 1 | `real_dash_parse_66` | ESP32 TWAI |
| `savvycan` | `SAVVYCAN` 2 | `gvret_parse` | ESP32 TWAI |
| `elm327` | `OBD_ELM327` 3 | `elm327_process_cmd` | **MIC3624** |
| `auto_pid` | `AUTO_PID` 4 | `elm327_process_cmd` + `autopid_init` | **MIC3624** |

Anything unrecognised falls back to `OBD_ELM327` (`wican-fw/main/config_server.c:543`). Default is
`"protocol":"elm327"` (`:232`). There is **no MQTT mode** — MQTT is an orthogonal `mqtt_en` flag.
Per-location keys `home_protocol`/`drive_protocol` also exist (`:229-230`).

### Mutual exclusions — each one is a real interaction bug source

1. **ELM327 WebSocket terminal ⟂ AutoPID.** Entering `terminal_type: elm327` **pauses AutoPID**
   (clears `DEV_AUTOPID_ENABLED_BIT` = `BIT11`, `wican-fw/main/dev_status.h:48`) and restores it on
   exit — `wican-fw/components/ws_server/ws_router.c:180-184`. Open *and* close handlers reset to
   monitor+console (`:266-277`), so an unclean disconnect cannot leave AutoPID permanently paused.
   **This is why canair's `--reboot` flag exists to restore AutoPID** — and why it is often
   unnecessary.
2. **AutoPID ⟂ an external ELM327 app.** An inbound ELM327 request clears
   `DEV_AUTOPID_ELM327_APP_BIT` and arms a **10-second inactivity timer** that re-sets it
   (`wican-fw/main/main.c:328-329`). A canair session polling faster than 10 s therefore holds
   AutoPID off continuously; a slower one lets it interleave.
3. **BLE ⟂ ELM327/AutoPID broadcast forwarding** (`wican-fw/main/main.c:449`).
4. **SmartConnect ⟂ `slcan`/`realdash66`/`savvycan`** — under SmartConnect the effective protocol is
   forced to `AUTO_PID` or `OBD_ELM327` (`wican-fw/main/main.c:847-858`), silently making the raw
   modes unreachable.
5. **Monitor-mode WS streaming ⟂ terminal mode** (`wican-fw/main/main.c:409`).

## WebSocket API (`/ws`)

Registered at `wican-fw/components/ws_server/ws_server.c:199-206` (`.is_websocket = true`,
`.handle_ws_control_frames = true`), started from `wican-fw/main/config_server.c:3774`.

**Only one connection slot** — `s_ctx` is a single static struct
(`wican-fw/components/ws_server/ws_server.c:44`), so a second client **overwrites the first's fd**.
Two concurrent canair sessions on `wican-ws` do not fail cleanly; they corrupt each other. The
device connection mutex (`canair lock`) is what prevents this, and it is load-bearing, not a
nicety.

Routing rule, `wican-fw/components/ws_server/ws_router.c:284-362`:

- **`payload[0] != '{'` → the router declines and the raw bytes go to the protocol stack**
  (`:291-294`). That is the entire mechanism separating control JSON from ELM327 traffic. It also
  means a payload that merely *looks* like JSON can never reach the ELM327 chip.
- `ws_mode` has exactly **two** values (`:78-91`): `monitor` (0, default) and `terminal` (1);
  `terminal_type` is `console` (0, default) or `elm327` (1). Anything else logs
  `Unknown ws_mode` and replies `{"type":"ws_mode","ok":false}` (`:342-347`).
- canair's handshake `{"ws_mode":"terminal","terminal_type":"elm327"}` matches `:307`+.
- Once in terminal mode, a JSON frame with **no** `ws_mode` and a `"cmd"` string is executed
  (`:350-358`).

Device→client framing: terminal output is **always** `{"type":"term_out","data":"…"}`
(`ws_router_send_term_out`, `:116-133`), sent as `HTTPD_WS_TYPE_TEXT`. Command execution:

```c
elm327_run_command(tmp, (uint32_t)n, 2000, NULL, ws_elm327_output_cb, false, 0);
```

`wican-fw/components/ws_server/ws_router.c:244` — **timeout hardcoded 2000 ms**,
`stop_after_first_frame = false`, `expected_frame_id = 0`. Commands are copied into `char tmp[256]`
with `strnlen(cmd, sizeof(tmp) - 2)` (`:235-244`), so they are **silently truncated at 254 bytes**,
and a CR is appended only if absent (`:239-243`).

Two behaviours worth knowing before designing a batching or pipelining scheme:

- `elm327_run_command` returns on the **first** prompt
  (`wican-fw/main/elm327.c:2656-2658`, `:2703-2705`, `:2769-2771`), so **multiple CR-separated
  commands in one frame do not work** — replies after the first are destroyed by the next command's
  `uart_flush_input`.
- On mutex contention the router replies the literal text `"Terminal busy\n"` and returns `ESP_OK`
  (`wican-fw/components/ws_server/ws_router.c:204-206`). **canair could parse that as a UDS
  response.** Check `classify_response` in `canlib/uds_parse.py` against that exact string.

Monitor mode streams **SLCAN ASCII** built by `slcan_parse_frame` from `can_receive()`
(`wican-fw/main/main.c:403-413`) — so on Pro under `auto_pid`/`elm327` it yields **nothing**,
because TWAI is off. Conversely, when a WS client is connected, inbound WS bytes are parsed as SLCAN
**regardless of the configured protocol** (`wican-fw/main/main.c:278-284`), coexisting with the
ELM327 branch rather than replacing it.

A second WebSocket, `/obd_logger_ws`, exists at
`wican-fw/components/obd_logger/obd_logger_iface.c:86-92`; its message format has not been examined.

## HTTP API

One `esp_http_server`; core handlers registered at `wican-fw/main/config_server.c:3490-3515`,
component handlers at `:3683-3688`, and the `/*` catch-all **deliberately last** (`:3690`, `:3716`)
to avoid shadowing (see the comment at `:3516`).

The ones canair uses or should care about:

| URI | Method | Purpose |
|---|---|---|
| `/check_status` | GET | status JSON (`check_status_handler` `:1943-1953`) |
| `/load_config` | GET | config get |
| `/store_config` | POST | config set (`protocol` required, 2–64 chars, `:2635-2645`) |
| `/system_commands` | POST | only `reboot`, `force_update_obd`, `set_rtc_time` (`:1331`+) |
| `/system_reboot` | POST | reboot |
| `/autopid_data` | GET | live AutoPID values |
| `/upload/ota.bin` | POST | ESP32 OTA (`esp_ota_*`, `:1964-2005`) |

Others: `/`, `/logo.svg`, `/store_canflt`, `/load_canflt`, `/store_auto_data`, `/load_auto_pid`,
`/load_auto_pid_car_data`, `/upload/car_data.json`, `/load_car_config`, `/api/destinations_stats`,
`/store_car_data`, `/scan_available_pids`, `/std_pid_info`, plus component routes `/ws`,
`/obd_logger_ws`, `/download_db`, `/obd_logs*`, `/cert_manager*`, `/vpn*`,
`/autopid/test_pid`, `/autopid/test_can_filter`, `/api/webhook`, `/restart_tracker*`.

`/check_status` fields include `fw_version`, `hw_version`, `git_version`, `protocol`, `can_datarate`
(live, from `can_get_bitrate()`, `:1789`), `batt_voltage`, `sleep_status`, `sleep_volt`,
`wakeup_volt`, **`obd_chip_status`** (`"Sleep"` `:1827` / `"Ready"` `:1831`), `uptime`, `device_id`,
`ecu_status`, `vpn_status`. Safe mode runs a **separate** server with `/`, `/upload_firmware`,
`/factory_reset` (`wican-fw/main/safemode.c:223-248`).

## Sleep and power

Completely different per generation — `wican-fw/main/sleep_mode.c:63` (`!= WICAN_PRO`) vs `:587`
(`== WICAN_PRO`).

**Pro is a soft sleep state machine, not ESP32 deep sleep.** States
`STATE_NORMAL / LOW_VOLTAGE / SLEEPING / WAKE_PENDING` (`wican-fw/main/sleep_mode.h:32-37`), driven
by `light_sleep_task` (`wican-fw/main/sleep_mode.c:837`+), voltage read every 3 s from ADC1 ch3
(`:918-919`), scaled `avg*11/1000 + 0.1` and rounded to 0.1 V (`:772-779`).

Two traps:

- **`wakeup_volt` from config is ignored by the ESP32 path** — it uses `sleep_voltage + 0.1f`
  (`wican-fw/main/sleep_mode.c:865`), with the config call commented out at `:861-864`. The config
  value is used only to program the MIC3624 (below). Fallback `sleep_volt` is **13.1 V** on the
  ESP32 path (`:856-859`) but **13.2 V** on the chip path (`wican-fw/main/obd.c:250-268`), with
  `wakeup_volt` fallback 13.5 V there.
- **`enter_deep_sleep()` is dead code** (`wican-fw/main/sleep_mode.c:798-835`) — no callers
  anywhere. Had it been wired, wake would have been EXT0 on `OBD_READY_PIN`. Wake is instead always
  a **full ESP32 restart** (`:1007-1014`), which is why a woken device has zero uptime and a fresh
  lock state.

Wake on Pro: voltage ≥ `sleep_volt + 0.1` held 1 s → restart; or `periodic_wakeup` +
`wakeup_interval` expiry when voltage > `CRITICAL_VOLTAGE 11.90f` (`:991-998`, `:613`).

**The chip sleeps on its own rules too**, programmed at boot by `obd_init()`
(`wican-fw/main/obd.c:362-387`, reading `STSLCS\r`, reprogramming at `:293-317`):
`STSLVLW >13.50, 1` (wake), `STSLVLS <13.20, N` (sleep), `STSLVl off,off`, `STSLU off, off`,
`STSLUIT 1200`, `ATZ`. Voltage is read with `ATRV` (`wican-fw/main/obd.c:342-360`).
`STSLEEP0\r` at `wican-fw/main/elm327.c:2329` sleeps it on demand. Note the local variable names at
`wican-fw/main/obd.c:293-317` are **swapped** relative to the commands they build — harmless,
confusing.

Device sleep *control* is `wican-cli`'s job, a separate package. canair only reads state.

## Measured performance — the ELM327 ceiling

All measured 2026-08-09 over LAN (`10.0.2.86`), `canair repl --transport wican-ws --timings` with
commands piped on stdin (**`repl` is scriptable this way** — undocumented but the only practical
harness). Distinct command strings become distinct `--timings` rows, so A/B variants must differ
textually or run in separate sessions.

**The dominant cost is the adapter waiting to be sure no more frames are coming**, not the link and
not the ECU. `0x7A0` `22C00B` (3 frames), interleaved, n=8:

| request | mean | max |
|---|---|---|
| `22C00B` (what canair sends today) | 205.8 ms | 653.7 ms |
| `22C00B3` (ISO-TP frame-count digit) | **52.7 ms** | **60.0 ms** |

**3.9×**, and the variance collapses. Reproduced 3.3× on `0x770` `22BC01`. `STPX H:7A0, D:22C00B,
R:3` measured 65.9 ms vs `22C00B3`'s 68.0 ms — **statistically identical**, so the win is the
early-return mechanism, not `STPX`.

**The count digit is dangerous if wrong.** With the true count 3: `22C00B2` returned a **truncated**
2-frame payload; `22C00B1` returned **a leftover frame from the previous request**
(`3:570100AAAAAAAA`) — i.e. an undercount desynchronises the pipe and corrupts the *next* read,
exactly what `plans/2026-08-08-elm327-pipe-desync-recovery.md` exists to recover from. Overcounting
(`5`, `9`) is safe but forfeits most of the gain (~150 ms), so "always send 9" is not a shortcut.
Any implementation needs a learned, validated count, resync on mismatch, and a permanent opt-out for
variable-length responses. **Untested: behaviour above 9 frames** — `0x7EA:21F2` is 13.

Timing knobs, measured (n=6–8, `0x7A0` `22C00B`):

| setting | mean | max | verdict |
|---|---|---|---|
| `ATAT0` | 685.5 ms | 753.4 ms | worst; confirms `ATST96` ≈ 614 ms |
| `ATAT1` (unset default) | 217.3 ms | 681.9 ms | **already optimal** |
| `ATAT2` | 394.2 ms | 686.4 ms | *worse*, erratic |
| `ATST0A` (40 ms) | 120.1 ms | 160.3 ms | returned `NO DATA` in one run — marginal |
| `ATST19` (100 ms) | 134.2 ms | 174.7 ms | cautious tail cap |
| `ATST96` (600 ms, current) | 199.7 ms | 686.1 ms | current |

`STPX` needs **`ATCRA`**, not `ATSH`: it does *not* inherit `ATSH`'s receive filter. Isolated over
five probes — bare `STPX` → `NO DATA`, after `ATFCSH` → `NO DATA`, after `ATSH` → `NO DATA`, after
`ATCRA7A8` → works; `STCRA` is not a command (`?`); a 4-digit header (`H:07A0`) is rejected. It
therefore saves **one** command per ECU switch, not two. Multi-frame reassembly worked with no
`ATFCSH`, so the STN answers flow control from the received first frame's address.

> **Housekeeping rule, learned the hard way.** `ATCRA` and `ATAT` are **not in any profile init
> string** (the Ioniq's is `ATSP6;ATS0;ATAL;ATST96;`, `profiles/ioniq-2017/profile.yaml:46`), so
> they
> **persist across sessions** and silently corrupt later recordings — a stale `ATCRA` pins the
> receive filter to one ECU. Reset with `ATZ` when done, and remember `ATZ` also clears
> `ATSP6`/`ATS0`, so the next request may `NO DATA` during protocol re-detection.
>
> **Verify a reset by interleaving two ECUs on their own rx addresses.** A single-ECU check is
> worthless: `0x770` replies on `0x778`, which is exactly the filter you may have left set.

Absolute numbers are LAN-local; the config default `vpn` device measured ~320 ms for the same work.
**Ratios transfer, absolutes do not** — and a fix that removes a fixed wait helps *more* on a slow
link.

## Checklist before changing transport code

- [ ] `wican-fw` on `wican-pro`, and the tag diffed against the device's `fw_version`.
- [ ] Not reading the `!= WICAN_PRO` emulator block, or `wican-fw/main/ws_router.c`.
- [ ] Clear on which chip the change affects — MIC3624 (`wican-ws`, `elm327-tcp`) or ESP32 TWAI
      (`slcan-tcp`) — and whether it must work on both.
- [ ] New `send_uds` calls pass `expected_sid` **and** `expected_echo` (a desync is otherwise
      undetectable — see AGENTS.md).
- [ ] `set_header` + `send_uds` held inside one `terminal.transaction()`.
- [ ] Any adapter setting written outside the init string is reset, or added to the init string.
- [ ] Feature gated on a **chip** capability probe, not a WiCAN firmware version — the ESP32
      reflashes the chip independently.
- [ ] `elm327-tcp` clones are not STN parts: no `ST*` command may be unconditional.
