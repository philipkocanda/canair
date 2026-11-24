# Bitfield models: one signal per bit vs bitmask

Scope: the two ways canair represents a bitfield and which to use while discovering bits.

## Know which of the two models you are looking at

canair can represent a bitfield **two** ways, and they are handled by
completely separate code paths:

| | Model A — one signal per bit | Model B — one `type: bitmask` signal |
|---|---|---|
| Expression | `expression: B10:5` (×8 signals) | `expression: B10` + `bits: {5: door_drv}` |
| Analysis tooling sees it | **yes** — all of `--bits`, `coverage --bitfields`, `bix` overlay | partly — `coverage --bitfields` counts its `bits:` map, but `--bits` sweeps do not split it |
| Best for | signals decoded incrementally, mixed verified/unverified, per-bit notes/`category` | a byte that is *wholly* a known flag set (e.g. a weekday schedule mask) |

**Model A is the working default.** Prefer A while bits are still being discovered. It is the
only model the per-bit ranking sweeps (`--discriminate --bits`, `--find-mirrors --bits`,
`investigate --bits`) can attribute individually.

Model B's payoff is elsewhere: a single logical signal for
`investigate --events --field NAME` and categorical stats (Cramér's V). Reach
for it only once a byte is essentially fully understood.
