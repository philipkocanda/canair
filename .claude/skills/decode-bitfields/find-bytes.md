# Step 1 — find the partially-decoded bytes

Scope: `coverage --bitfields`, support maps, and the blind spots of the coverage report.

## Step 1 — find the partially-decoded bytes

```
uv run canair --profile hyundai-ioniq-ae-ev coverage --bitfields
```

```
  IGPM 22BC03 (18p, 15 verified)  11 data bytes, 2026-04-15
      UNMAPPED B5,B6,B7,B13,B14,B15,B17
      BITS B11 have{0,1,2,5,6} missing{3,4,7}
      BITS B12 have{2,3,4,5} missing{0,1,6,7}
  VCU 2101 (…)
      BITS B10 have{0,1,2,3} missing{4,5,6,7} (also read whole)
```

A `BITS` line is exactly this skill's work item: `have{}` = bit indices some
param reads (0 = LSB), `missing{}` = the unknowns. A byte is listed when (a) ≥1
param reads it bit-wise (`Bn:k`/`Sn:k`) **or** a `type: bitmask` param labels
some of its bits, (b) it is a real data byte (PCI/SID/DID echo excluded), and
(c) fewer than 8 bits are covered (`src/canair/cli/commands/coverage/analysis.py::analyze_pid`).

**`(also read whole)`** means some param *additionally* reads the byte as a
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

### Coverage's remaining blind spot

`coverage --bitfields` used to under-report badly; two of the three historical
gaps are now fixed (a whole-byte read no longer suppresses the finding, and
`Sn:k` + `type: bitmask` maps are counted). One remains — cross-check before
concluding a byte is done:

**Mask-style expressions are invisible to it.** `(B09 & 0x04)` yields no bit
references at all, so a byte read only that way reports no gap even though seven
bits are undecoded. Always author bits as the `Bn:k` accessor, never an `&`
mask. (Zero `&` expressions exist in bundled profiles today; keep it that way.)

> Historical note, in case you meet an old profile or doc: before 2026-08-06 a
> byte vanished from this report entirely if *any* param read it whole. That hid
> the two most-captured bitfields in the bundled profile — VCU 2101 B10 (the
> PRND byte, 6365 captures) and BMS 2101 B14 (5997) — because both follow the
> `DEBUG_*_FLAGS` "raw byte + individual bits below" convention. See
> `plans/2026-08-06-bitfield-audit-and-gear-state.md`.
