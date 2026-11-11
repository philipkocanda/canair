# The WiCAN Pro hardware, its CAN buses and the co-processor link

Citations here are relative to the basis table in [`SKILL.md`](SKILL.md). Re-check that
basis before you trust a line number.

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
`wican-fw/main/elm327.c:2999-3002`, setting `device_type = 0x3624`.

> `OBDLink MX` is the *emulator's* `ATI` string (`wican-fw/main/elm327.c:71`), which is not
> compiled on Pro — never expect it from a WiCAN Pro. (canair's resync comment once claimed
> otherwise; fixed in `src/canair/transport/elm327/terminal.py`.)

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

**Methodology warning — this is how this skill previously got the count wrong.** A grep across
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
selection is **mutative and persists across sessions** (see the housekeeping rule in
[`elm327-performance.md`](elm327-performance.md)) — `ATDP`/`ATDPN`/`STPRS` are the read-only
probes, `ATSP`/`STP` are not.

Sheet 3 does show the ESP32's transceiver driving the *same* `HS_CAN_H`/`HS_CAN_L` nets that land on
the OBD connector, so the two silicon paths most likely share the HS-CAN pins — but confirming that
needs the MIC3624 sheet, so it remains unproven.

### Car side: several segments, bridged by a gateway, and the OBD port reaches only some

This is what canair's `can_buses.yaml` models. The Ioniq declares `ALL`, `B-CAN` (100 kbit/s),
`P-CAN`, `C-CAN`, `MM-CAN`, `H-CAN`, `D-CAN` (all 500 kbit/s) in
`profiles/hyundai-ioniq-ae-ev/can_buses.yaml`; 15 of 30 ECU files declare a `can_bus:` (5 `[B-CAN]`,
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
  anywhere in `src/canair/`). `can_bus:` is documentation for humans and for `canair bus`; nothing
  routes on it. Do not add code that assumes otherwise without designing it deliberately.

## The MIC3624 co-processor link

Three UARTs, all build-time gated:

| UART | Variant | Baud | Pins | Role |
|---|---|---|---|---|
| `UART_NUM_1` | Pro | 2 000 000 (fallback 115 200) | 16/15 | **the MIC3624** |
| `UART_NUM_2` | Pro | 2 000 000 | 17/18 | a **wired ELM327 port onto the same chip** |
| `UART_NUM_0` | USB variant | 4 000 000 | default | host serial |

UART1 setup: `wican-fw/main/elm327.c:3449-3467`, buffer `UART_BUF_SIZE = (18*1024)`
(`wican-fw/main/obd.h:21`). UART2 feeds the *same* `elm327_process_cmd`
(`wican-fw/main/wc_uart.c:181`) — it is not a second bus path.

**Baud negotiation** (`elm327_set_baudrate`, `wican-fw/main/elm327.c:2011-2094`): probe with
`VTVERS\r` at 2 Mbaud; if silent, drop to 115 200 and re-probe; then `STSBR 2000000\r` expecting
`OK`, switch the local port, and `STWBR\r` to **persist it in the chip**. Constants at
`wican-fw/main/elm327.c:1318-1329` (`UART_TIMEOUT_MS 1200`, `DESIRED_BAUD_RATE 2000000`,
`DEFAULT_BAUD_RATE 115200`, `ELM327_CMD_MUTEX_TIMOUT 10000`).

**The ESP32 owns the chip's firmware.** Expected versions `OBD_FW_VER_V18 "V2.3.18"` /
`OBD_FW_VER_V22 "V2.3.22"` / `OBD_FW_VER_V24 "V2.3.24"` (`wican-fw/main/elm327.h:27-29`); the
image is **embedded in the ESP32 binary** — `v4.52p` embeds `V2.3.24`
(`wican-fw/main/CMakeLists.txt:114`, symbols at `wican-fw/main/elm327.c:1373-1374`) and accepts
either `V2.3.22` or `V2.3.24` as current (`wican-fw/main/elm327.c:1583`, `:3003`). On any boot
where `VTVERS` reports neither it **silently reflashes** the MIC3624 (`elm327_update_obd`,
`wican-fw/main/elm327.c:3059`, entering the bootloader with `VTDLMIC3422\r` at `:3086`); it also
runs when the chip is stuck out of normal state, so it doubles as recovery. Forceable over HTTP
with `{"command":"force_update_obd"}` (`wican-fw/main/config_server.c:1383-1386`). Firmware v6
pins `V2.3.22` again and downgrades a `V2.3.24` chip on its first boot (Trap 4 in
[`firmware-source.md`](firmware-source.md)).

**Implication:** the ST command surface canair sees is pinned by the *ESP32* firmware version,
not by what the chip shipped with. A capability probe should key on the chip reply, not on the WiCAN
version.

Boot sequence: `wican-fw/main/elm327.c:3469-3495` — hard reset → `elm327_update_obd(false)` →
`elm327_powerpin_commands()` → poll `elm327_chip_get_status()` up to 10 × 200 ms → `uart_flush` →
`obd_init()`.

UART1 access is serialised by `xuart1_semaphore` (`wican-fw/main/elm327.c:3444`,
`elm327_lock()` at `:2096-2099`, taken in `elm327_run_command` at `:2539`) — **one mutex around the
whole command+response cycle**, and every command begins with
`uart_flush_input(UART_NUM_1); xQueueReset(uart1_queue);` (`wican-fw/main/elm327.c:2600-2601`).
Those two facts are why **pipelining is impossible on this path** regardless of client behaviour: a
second in-flight request's reply is destroyed by the next command's flush. Full argument in
`plans/2026-08-09-wican-ws-throughput-ceiling.md`.
