# XPeng G6 — Known Issues

## Cell voltages / temperatures cut off partway (only ~130 of 192 cells read)

**Symptom:** On the WiCAN device (AutoPID firmware), the cell-voltage PID reads
data for only ~130 cells even though the profile defines 192 (`BMS` DID
`221122`, `HV_C_V_001`…`HV_C_V_192`). The device dashboard may *list* ~150
parameters while only ~130 actually show values; the rest read zero/blank.

This is a **WiCAN AutoPID firmware limitation for long multi-frame (ISO-TP)
responses** — not a canair bug and not a byte-mapping error in the profile.

### Why it happens

A 192-cell response is ~194 payload bytes ≈ **28 CAN frames**. The AutoPID
firmware caps that read:

- **Receive window.** AutoPID hardcodes its ELM init to `ATST96`
  (`wican-fw/main/autopid.c`), giving each request only `0x96 × 4.096 ms ≈
  614 ms`, and grabs the accumulated buffer with a single 1 s queue read
  (`autopid.c`, `xQueueReceive(..., pdMS_TO_TICKS(1000))`). The firmware's own
  flow control sends STmin = 10 ms (`elm327.c`), so ~28 frames cost ~280 ms of
  inter-frame spacing alone, plus per-frame text accumulation and MQTT
  publishing. On a loaded ESP32 the tail frames (cells ~130→192) don't arrive
  before the buffer is parsed.
- **Partial multi-frame support.** `elm327.c` carries an explicit TODO noting
  the firmware doesn't fully handle large multi-frame flows.

So the missing cells are simply **not in the response buffer** by the time the
expressions run — the byte offsets themselves are correct.

### Why it is NOT a mapping bug

AutoPID inits with `ATH1` (headers on) and `parse_elm327_response`
(`autopid.c`) keeps the ISO-TP PCI bytes as `data[0]` of each frame. The
expression evaluator (`expression_parser.c`) does a flat `data[index]`, so it
indexes a buffer that includes PCI at `B0/B1` (First Frame) and `B8/B16/…`
(Consecutive Frames) — the same WiCAN `Bnn` convention canair uses. The
PCI-skip pattern in the profile expressions (`B5..B13`, skip `B16`, `B17..B23`,
skip `B24`, …) is consistent all the way through, including at the 128→130 and
149→150 boundaries.

The three numbers in play:

| Count | Meaning |
|------:|---------|
| 192   | Cells defined in the profile (`221122`) |
| ~150  | Params the firmware lists (parsed/attempted) |
| ~130  | Cells whose byte landed in frames that arrived within the ~614 ms window |

### What to do

1. **Confirm the real pack cell count first.** XPeng G6 ships in 66 kWh and
   87.5 kWh variants with different cell counts. If the car genuinely has ~130
   cells, `HV_C_V_131`…`HV_C_V_192` are speculative padding — trim them and the
   read fits the window. (This whole profile is seeded device-free and
   unverified; see `profile.yaml`.)
2. **Verify with canair over `slcan-tcp`**, which does full ISO-TP reassembly
   with its own flow control:
   `uv run canair query BMS:221122 --wican <ip> --save --label "cell count check"`.
   Compare the declared First-Frame length to the pack's real cell count. If the
   declared length ≈ 194 but the device-read stops at ~130, it confirms the
   AutoPID window cap; if the declared length itself is small, the pack simply
   has fewer cells.
3. **If the pack really has 192 cells,** split `221122` (and the temperature DID
   `221123`) into smaller sub-reads if XPeng exposes paged/sub-DID access, so
   each request stays within the firmware's frame budget. This is a profile
   change (no firmware reflash) but needs a capture to confirm the paging
   scheme.
4. **Firmware-side** (out of scope for this profile): raising `ATST` in the
   AutoPID init and the queue-grab timeout in `wican-fw` would widen the window,
   but requires editing and reflashing the device.

The same reasoning applies to the temperature DID `221123` (`HV_T_1`…`HV_T_35`),
though its shorter response is far less likely to hit the window.
