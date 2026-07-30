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

The `221122` response is ~227 bytes ≈ **33 CAN frames**. Read over **canair's
`slcan-tcp`** path this reassembles fully (that is how the 150-cell capture was
made — canair does its own ISO-TP with proper flow control). Read on the
**WiCAN device's own AutoPID firmware** (the device dashboard / MQTT), only the
first ~130 cells tend to show values; the rest read zero/blank.

### What actually causes the ~130-cell cut-off

Verified against the firmware source (`wican-fw/main/autopid.c`, `elm327.c`).
There are three independent caps; which one bites depends on the car's
addressing width:

| Cap | Value | Firmware site | Tunable from profile? |
|-----|-------|---------------|-----------------------|
| Inter-frame timeout (`ATST`) | `req_timeout × 4.096 ms`; `ATST96` ⇒ ~614 ms; **max `ATSTFF` ⇒ ~1044 ms** | `elm327.c` (`xtimeout`), reset per frame | **Yes** — `init` |
| AutoPID poll queue read | **1000 ms, hardcoded** | `autopid.c` (`xQueueReceive(..., pdMS_TO_TICKS(1000))`) | **No — reflash** |
| ASCII accumulation buffer | **1024 bytes, hardcoded** | `autopid.h` (`BUFFER_SIZE`), `autopid.c` (`append_to_buffer` silently drops the overflow) | **No — reflash** |

**For this car (11-bit) the buffer is NOT the limit; timing is.** AutoPID always
runs `ath1` (headers on) + `ats1` (spaces on), and the firmware *filters out*
`ATH0`/`ATS0`/`ATE1` from any init string, so you can't shrink the ASCII
footprint. With 11-bit headers (`7EC`) each frame renders to ~28 ASCII chars, so
the full 33-frame response is only **~924 chars** — it fits the 1024 buffer with
margin. The cut-off is therefore the **timing race**: a per-frame gap exceeding
the `ATST` window, or the whole 33-frame exchange (+ ESP32 text accumulation +
MQTT publish) not finishing inside the hardcoded **1000 ms** poll read.

> A **29-bit** car is different: 8-char headers push each frame to ~33 chars, so
> ~33 frames ≈ 1090 chars and the **1024-byte buffer** becomes the hard cap
> (overflow silently dropped). That is a reflash-only fix.

### Profile mitigations (applied; UNVERIFIED without a device)

`profile.yaml` `init` now carries, in addition to the neutral protocol bits:

```
ATSTFF;ATFCSM1;ATFCSD300000;
```

- `ATSTFF` — widen the per-frame timeout from ~614 ms to the ~1044 ms max, so a
  slow/jittery frame doesn't end the read early (the timer resets each frame).
- `ATFCSM1;ATFCSD300000` — supply flow control explicitly: FS=0 (clear-to-send),
  **BS=0** (ECU sends all frames without pausing), **STmin=0** (no inter-frame
  gap), so all 33 frames burst as fast as the bus allows and are more likely to
  land inside the 1000 ms poll window. (The firmware default is BS=0 / STmin=10 ms.)

Also keep the request hex **even-length** (`221122` is): an odd-length PID string
makes the firmware treat the last nibble as an expected-frame count and stop
early, capped at 9 frames.

### What still requires a firmware reflash

The **1000 ms poll timeout** and the **1024-byte ASCII buffer** are hardcoded and
un-tunable. If the exchange can't complete in 1 s (or, on a 29-bit car, the ASCII
overflows 1024), the tail is lost regardless of `init`. The clean firmware fix is
to raise those limits (and/or drop header echo in the AutoPID default init) —
out of scope for this profile.

None of this affects canair's own `slcan-tcp` reads, which already return all
150 cells.
