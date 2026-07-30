# XPeng G6 — Known Issues

## Cell-voltage / temperature DIDs return a fixed, 0xFF-padded buffer

**Not a bug — documented device behaviour.** The BMS answers the cell-voltage
DID `221122` (and the temperature DID `221123`) with a **fixed-size data field
padded with `0xFF`** past the real sensor count:

| DID      | Data field (ISO-TP) | Real bytes | Padding        |
|----------|--------------------:|-----------:|----------------|
| `221122` | 224 B (declared 227)|  **150**   | 74 × `0xFF`    |
| `221123` |  ~96 B              |   **36**   | rest × `0xFF`  |

Both are flagged `variable_length: true` so a shorter real read isn't mistaken
for a truncated ISO-TP response.

### Cell count: 150, not 192 (87.5 kWh / 150S variant)

The profile was seeded device-free from the upstream WiCAN community profile,
which defined **192** cells (`HV_C_V_001`…`HV_C_V_192`). The 2026-07-30 capture
disproves that:

- Only the **first 150** data bytes hold real cell voltages (~3.80–3.84 V while
  charging); bytes 151→ are `0xFF`. Decoding `0xFF` as a cell gives a bogus
  `(0xFF × 2)/100 = 5.10 V`, and `HV_C_V_151`…`192` either read that padding or
  index past the payload entirely.
- **Physics confirms 150S.** Pack voltage `221101` = **575.3 V**; at the measured
  ~3.835 V/cell that is exactly **150** cells in series. 192S would require an
  impossible **3.00 V/cell** at this pack voltage.

`HV_C_V_151`…`HV_C_V_192` were therefore **trimmed** — the profile now defines
**150 cells**, matching the car.

> **Variant note.** The XPeng G6 ships in **66 kWh** and **87.5 kWh** packs with
> different cell counts. This profile / capture is the **87.5 kWh (150S)**
> variant. A 66 kWh car will report a different cell count — build a separate
> profile (or a variant) for it rather than assuming 150.

### Temperature count: 35 defined, possibly 36

`221123` carries **36** real data bytes (all ~19–21 °C) before the `0xFF`
padding, but the profile defines **35** sensors (`HV_T_1`…`HV_T_35`). The 36th
byte is a plausible temperature — it may be a 36th sensor or an aggregate
(max/min/avg). Left at 35 pending verification; do not assume 36 without a
second reading and, ideally, a cross-check against a known pack/module count.

## Reading all 150 cells over the WiCAN device (AutoPID firmware)

Independent of the count above, reading the **full** `221122` response over the
WiCAN device's AutoPID firmware can still be truncated — a ~150-byte response is
~22 CAN frames and the firmware caps its receive window:

- **Receive window.** AutoPID hardcodes `ATST96` (`wican-fw/main/autopid.c`) →
  `0x96 × 4.096 ms ≈ 614 ms` per request, grabbed with a single 1 s queue read.
  Its own flow control sends STmin = 10 ms (`elm327.c`), so the frames cost
  ~220 ms of inter-frame spacing alone, plus text accumulation and MQTT publish.
  On a loaded ESP32 the tail frames may not arrive before the buffer is parsed.
- **Partial multi-frame support.** `elm327.c` carries a TODO noting large
  multi-frame flows aren't fully handled.

So on the device you may see values for only the first ~130 cells even though
150 are defined and present on the bus.

### What to do

1. **Verify over `slcan-tcp`**, which does full ISO-TP reassembly with its own
   flow control (this is how the 150-cell count above was captured):
   `uv run canair query BMS:221122 --wican <ip> --save --label "cell read"`.
2. **Firmware-side** (out of scope here): raising `ATST` and the queue-grab
   timeout in `wican-fw` widens the window, but requires reflashing the device.
