# Step 7, pitfalls and definition of done

Scope: verifying a bit edit, the symptom/cause pitfalls table, and the completion checklist.

## Step 7 — verify

```
uv run canair --profile hyundai-ioniq-ae-ev validate pids
uv run canair --profile hyundai-ioniq-ae-ev coverage IGPM --bitfields
uv run canair --profile hyundai-ioniq-ae-ev bix -a <payload> --ecu IGPM --pid 22BC03
```

`have{}` must have grown by exactly the bits you defined and `missing{}` shrunk
to match. If the byte *disappeared* from the report, you wrote an `&`-mask
expression and silently claimed the whole byte — go back to Step 1's blind spot.
(A plain whole-byte `Bn` no longer hides the gap; it just adds
`(also read whole)`.)

## Pitfalls

| Symptom | Cause |
|---|---|
| Byte vanished from `coverage --bitfields` after an edit | An `&`-mask expression now claims it (a plain `Bn` only adds `(also read whole)`) |
| New bit param reads `constant` in the upsert echo | Wrong byte offset (WiCAN `Bnn` includes ISO-TP PCI) or wrong bit index |
| `bix -a` roles shifted by one | Annotating a `22xxxx` payload with no `--pid` and no `-2` |
| `--dwell` classes all `unknown` | Session is legacy `keep:unique`; falling edges dropped |
| Bit never appears in `--events` | Constant in scope — never exercised, not "unused" |
| Several bits look like one signal | Enum code (check distinct byte values, Step 4.5) or co-driven by one action |
| A bit toggles hundreds of times | Low bit of an analog/counter byte, not a flag (Step 4.5) |

## Definition of done

- Every bit you claim has ≥2 independent actuation→edge pairs, or is explicitly
  `--unverified`/named for its position.
- The byte's distinct values were enumerated — an enum was not filed as flags,
  and analog-byte bits were not filed as flags.
- `coverage --bitfields` shows the byte's `have{}` grown and `missing{}`
  shrunk — the byte did not disappear.
- No `&`-mask expression was introduced on a partially-decoded byte.
- Every bit claimed against a *named* reference was cross-tabbed over the
  reference's full value set, not just correlated — supersets were ruled out.
- No param decodes a constant under a name that asserts a state.
- Unexercised bits have a `research:` lead naming the state/action needed.
- `canair validate pids` passes; notes are bare facts.
