# CAN test fixtures

Tiny, hand-trimmed raw-CAN log slices used by the unit tests. They are kept
minimal on purpose — the **full** third-party corpora are never committed to
this (public) repo; fetch them on demand with `scripts/fetch_can_corpus.py`
(destination `references/can/`, gitignored).

## `ioniq28_drive_slice.log`

Synthetic candump-format slice (fabricated IDs/data) for exercising the
candump reader.

## `ioniq28_gear_drive_slice.csv`

A **verbatim** excerpt of a real SavvyCAN **GVRET** drive log, trimmed to a
minimal fair-use slice for testing the GVRET reader and guarding the
reverse-engineered `powertrain` broadcast signals.

- **Source:** <https://github.com/uhi22/Ioniq28Investigations>
  (Hyundai Ioniq 28 kWh, PCAN internal-bus tap — the upstream repo ships no
  license, so we do not redistribute its logs; only this small excerpt is kept.)
- **File:** `IONIQ_PCAN_drive_fwd_neutral_drive_reverse_neutral.csv`
- **Contents:** only `0x354` and `0x386` frames, concatenated from four
  contiguous windows of the source log so the slice covers all four gear codes
  plus a stationary-to-moving transition:
  - `0x354` — gear lever; low nibble of the 6th data byte (D6) is `1=P 2=R 3=N 4=D`
  - `0x386` — `WHL_SPD11` wheel speeds (front-left = start_bit 0, 14-bit LE,
    scale 0.03125 km/h)

Reproduce with `scripts/fetch_can_corpus.py` then take the same frame subset.
