# Step 1 — find the partially-decoded bytes

Scope: `coverage --bitfields`, support maps, and the blind spots of the coverage report.

## Step 1 — find the partially-decoded bytes

```
uv run canair --profile hyundai-ioniq-ae-ev coverage --bitfields
```

```
  IGPM 22BC03 (20p, 16 verified)  8 data bytes, 2026-11-22
      MAP FD EE 3C 73 (bit) B5,B6,B7,B9
      BITS B13 have{1,2,3} missing{6,7} (+3 not signals)
```

A `BITS` line is exactly this skill's work item: `have{}` = bit indices some
signal reads (0 = LSB), `missing{}` = the unknowns. A byte is listed when (a) ≥1
signal reads it bit-wise (`Bn:k`/`Sn:k`) **or** a `type: bitmask` signal labels
some of its bits, (b) it is a real data byte (PCI/SID/DID echo excluded), and
(c) fewer than 8 bits are covered (`src/canair/cli/commands/coverage/analysis.py::analyze_pid`).

**`(also read whole)`** means some signal *additionally* reads the byte as a
whole (`Bn`), the `DEBUG_*_FLAGS` convention. That is not a decoding of the
individual bits, so the gap still counts — the flag just tells you the raw byte
is already exposed, and warns that on a byte which is really a discrete *code*
rather than independent flags the "gap" may be intentional (run Step 4.5 to
tell which).

Scope with `coverage IGPM 22BC03 --bitfields`; `--json` gives
`incomplete_bitfields: [{byte, have, missing, also_whole}]` for scripting. Note
`--bitfields` filters which **PIDs** print, not which lines — `UNMAPPED`/
`UNVERIFIED` still appear on a selected PID.

### Check for a support map first

Many Hyundai/Kia body DIDs lead with a 4-byte **support map** that marks which data bits are
signals at all (`reverse-engineer-signal`'s `hyundai-kia-uds-conventions.md`). Before you work a
`BITS` gap, run:

```
uv run canair --profile hyundai-ioniq-ae-ev investigate IGPM --support-map
```

- Only **marked** bits are work. `open, moving` are the cheapest (they already change in the
  captures); `open, never set` are bits nobody has exercised yet, so plan the narrated capture
  (Step 3) to trigger them.
- An **unmarked** bit is not a signal on this unit. If the map is declared (`pids
  set-support-map`, printed by the command), `coverage` already lists only marked bits as
  `missing{}` and counts the rest as `(+N not signals)`.
- A set bit directly above a mark is a **field continuation**: the mark starts a multi-bit field,
  such as the IGPM's 2-bit turn signal. Define it as one field, not as two flags.
- A *defined* bit outside the map (`OUTSIDE MAP` in `coverage`) deserves a second look. BCM
  `22B005`'s light-stalk bits are the documented exception, backed by a deliberate test.

### Coverage's blind spot

**Mask-style expressions are invisible to it.** `(B09 & 0x04)` yields no bit references, so a
byte read only that way reports no gap even though seven bits are undecoded. Always author bits
as the `Bn:k` accessor, never an `&` mask.
