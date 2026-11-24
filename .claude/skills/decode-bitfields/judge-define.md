# Steps 5-6 — judge the evidence and define the bits

Scope: evidence thresholds, mirror/superset pitfalls, and writing bit params via `canair pids`.

## Step 5 — judge the evidence

- **A bit and the param that maps it appear as duplicate adjacent rows with
  identical F** in `--discriminate`. That is expected and is the *confirmation*
  that a param really is that bit. Two *different* bits with identical F are
  either mirrors or co-driven — not independent findings.
- **Prefer a same-payload cross-tab to a time join.** Bits in the *same* PID
  response are perfectly aligned — cross-tabulating them has zero join error and
  is far stronger evidence than a correlation. This is how `VCU 2101 B23`'s
  nesting (`EV_READY ⊆ INVERTER_ENABLED ⊆ B23:4`) was proven over 6367 captures
  with no violations. Reach for `align`/`correlate` only across ECUs/PIDs.
- **Simultaneous edges are not one signal.** DRL, tail lights and low beam all
  switch on one stalk movement; three bits legitimately move together. Once
  Step 4.5 has ruled out an enum, separate them by finding a session where they
  *diverge* (a lighting mode that raises only some of them). Until then say so,
  or don't name them separately. Bits that never diverge across a large corpus
  are a **mirrored pair** — define one and record the other with `ship: false`
  as a mirror rather than inventing two meanings.

- **Mirror agreement must beat coincidence.** Two mostly-zero flags "agree" 99%
  of the time by construction; the matcher guards this with Cohen's κ
  (`src/canair/analysis/mirrors.py::_kappa`). Trust the reported κ, not the raw agreement.
- **A high correlation with a named signal may mean SUPERSET, not equality.**
  This is the single most productive check in the skill, because it catches
  *already-verified* mistakes. `VCU 2101 B26:3` shipped as `GEAR_DRIVE` on the
  strength of `r=+0.984` vs the drive-gear bit — but it is really "gear is not
  Park": it also reads 1 in Reverse and Neutral, which simply had not been
  sampled when the correlation was computed. 57 false positives, 0 false
  negatives, and 99.1% agreement, which is exactly what a superset looks like.
  A correlation cannot distinguish `A == B` from `A ⊇ B`; a cross-tab against
  *every* value of the reference can. Always tabulate the reference's full value
  set (`P/R/N/D`, not `D`/not-`D`) and read the **asymmetry** of the errors —
  all-false-positives-no-false-negatives means superset, and the honest name is
  the superset (`GEAR_PARK_INV`, later decoded from the firmware as part of
  `VCU_SBW_ACTUATOR_STATE`), not the special case.
- **A near-perfect correlation computed on a thin corpus expires.** Re-run the
  old evidence before trusting a note: the `r=+0.984` above was true over 773
  captures and false over 6365. When a note cites an `n`, check today's `n`.
- **One episode is not evidence — and a single sample can tell a coherent
  story.** Prefer ≥2–3 clean, independent actuation→edge pairs before naming a
  bit (`--dwell`'s `episodes`/`trans` columns are the count). The failure mode is
  not obvious noise: one `BMS 2101 B14 = 0x45` row produced a *physically
  sensible* three-part reading (two HV contactors plus a rapid-charge relay) that
  survived scrutiny until the capture itself was withdrawn as untrustworthy.
  Naming it `UNKNOWN_B14_*` per the rule below is what kept the profile honest.
- **Never-toggling bits are unknowable, not absent.** Constant bits are dropped
  from every `--bits` series by design (`src/canair/analysis/cross_signal.py::build_bit_series`). A bit
  in `missing{}` that never appears in `--events` simply wasn't exercised — file
  a `research:` lead naming the state/action to try, don't conclude "unused".
- **A constant-decoding param is a defect, whatever its `verified` flag.** Sweep
  for them (decode every bit param and check `len(set(values)) == 1`): the
  bundled profile had `1-B11:5` shipping a constant 1 under the name
  `VEHICLE_STATE_MAIN_RELAY_ON`, i.e. asserting "HV relay closed" in `SLEEP` and
  while charging. An inverted expression (`1-Bn:k`) is especially dangerous —
  it turns "bit never set" into a confident `true`. Distinguish *unexercised*
  (siblings vary; keep the claim, add a caveat + research lead) from *wrong*
  (the expression cannot be what the name says; rename to the position and
  disable).

## Step 6 — define the bits

One param per bit (Model A):

```
uv run canair --profile hyundai-ioniq-ae-ev pids upsert-param IGPM 22BC03 HOOD_OPEN B11:0 \
    --category opening --value 0=closed --value 1=open --verified \
    --source "timed event capture 2026-07-24" \
    --notes "B11:0 went 0->1 only when the hood was opened during a narrated event sequence."
```

- **Watch the decoded-range echo** `upsert-param` prints — a bit param that
  reads `constant 0` across captures means the offset or bit index is wrong.
- **Naming honesty** (same bar as `pid-cleanup` rule 4): known meaning → name
  it; known bit, unknown meaning → put the position in the name
  (`<ECU>_UNKNOWN_B10_6`; match the file's existing convention); a guess → suffix
  `?` in the name or leave it `--unverified`. A confirmed-but-unexplained bit
  is still worth defining as a placeholder — it stops the next person
  re-deriving it and makes the gap visible.
- Use `--unverified` until you have the repeated evidence from Step 5;
  `--no-ship` for a bit you want tracked but not shipped to the device.
- Speculation about untested states goes in `canair pids add-research`, not the
  note.
