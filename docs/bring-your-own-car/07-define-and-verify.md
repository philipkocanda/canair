# 7. Define & verify

[Analysis](06-analyze.md) gave you a hypothesis confirmed by data — say,
*"byte 12 of `MyECU:2101` correlates with a known speed signal at r=0.99, ~1:1,
so it's speed in km/h."* Now you turn that into a named **parameter** stored in
the profile — the unit that makes `canair read MyECU` decode the value for
anyone using your profile.

This is the heart of **contributing PIDs**: a raw byte becomes a documented,
validated, shareable signal.

## Write the parameter

Use `canair pids upsert-param`, which is surgical,
comment-preserving, and schema-validated (and auto-reverts if the edit would
break validation):

```bash
canair pids upsert-param MyECU 2101 SPEED_KMH "[B12]" \
    --unit km/h --min 0 --max 200 \
    --source "hunt vs GPS speed, r=0.99" --unverified
```

- `SPEED_KMH` — the parameter name (must be unique across the profile's shipped
  signals).
- `"[B12]"` — the [expression](../concepts/byte-indexing.md) that extracts the
  value from the payload. Byte indexing matters — confirm the offset with
  `canair bix --annotate`.
- `--unit`, `--min`, `--max` — metadata that documents and bounds the signal.
- `--category` — what the signal is, **only where the unit doesn't already say it**: a `%`
  that is a `state_of_charge`, `state_of_health` or `humidity`, or a two-state signal that is a
  `door`, `opening`, `lock`, `lamp`, `plug` or `charging`. A two-state one also gets
  `--value 0=closed --value 1=open` (or its own labels), which records which value means which.
  A `V` signal is a voltage without saying so; stating it anyway fails validation.
- `--source` — *why you believe it* (correlation, a reference car, a datasheet).
  Evidence, not vibes. When the signals on a page share fields — a group of cell voltages with
  the same unit, range and evidence — state them once on the PID instead:
  `canair pids set-pid-defaults ECU PID --unit V --min 0 --max 5 --verified`
  (also `--source`, `--source-link`). Each signal that sets none of its own inherits them, and
  one that does keeps its value. `canair pids hoist-defaults` moves repetition you already have
  up to the PID, changing no signal.
- `--unverified` — **start here.** A hypothesis is a hypothesis until proven.

## Verify before you trust it

Mark a parameter `--verified` only once you've confirmed it holds up — the value
tracks a known reference, stays physically plausible across vehicle states, and
survives more captures:

```bash
canair decode MyECU 2101 --param SPEED_KMH --stats   # sane range/distribution?
canair pids upsert-param MyECU 2101 SPEED_KMH "[B12]" --verified   # promote
```

The discipline: **read and capture freely, define conservatively.** Optimistic
guesses promoted to "fact" are how bad profiles spread.

## Fixing and renaming

Got a byte offset wrong, mis-keyed a PID, or need to drop a dead one? Use the
surgical `canair pids` editors rather than touching the YAML — every edit is
comment-preserving and schema-validated (auto-reverted on failure):

```bash
canair pids rename-pid   MyECU B002 22B002   # re-key a PID (e.g. add the 22 service prefix)
canair pids rename-param MyECU 2101 OLD NEW   # rename a parameter (fields preserved)
canair pids rm-param     MyECU 2101 NAME      # remove one parameter
canair pids rm-pid       MyECU 22B002         # remove a whole PID (drops pids: if it was the last)
```

If no `canair pids` subcommand covers a field you need, edit the file directly, run
`canair validate pids`, and [report the gap](../contributing/index.md). It catches the usual
hand-editing slip too: a key written twice (two signals of the same name in one PID, say),
which YAML would otherwise load as the later one without a word. Every other `canair validate`
target makes the same check on its file (`profile.yaml`, `variants.yaml`, `vehicle_states.yaml`,
`groups.yaml`, `can_buses.yaml`, `actions.yaml`, `signals/`).

## Audit for gaps

`canair coverage` cross-references your parameter expressions against the payload
length from each PID's newest session and flags what's still undecoded:

```bash
canair coverage MyECU          # unmapped bytes, partial bitfields, no-capture PIDs
canair validate all           # schema + duplicate-name checks
```

What it reports per PID:

| Finding | Meaning |
|---|---|
| `UNMAPPED` | a data byte no expression reads |
| `UNVERIFIED` | a data byte read only by unverified signals |
| `BITS` | a byte read bit by bit with some bits still undecoded |
| `PAST END` | a signal reading beyond the end of the payload — a short reply, or a wrong offset |
| `MAP` | the PID declares a [support map](../concepts/analysis-commands.md#support-maps-which-bits-are-signals-at-all): its own bytes, and any covered bytes that carry no signal, are not data to decode |
| `OUTSIDE MAP` | a signal reads a bit or byte the declared support map does not mark (a bit just above a mark is shown dimmed as `FIELD CONTINUATION`) |
| `NO CAPTURE` | no positive reply recorded (`in scope` when a scope flag such as `--until` is set) |
| `NEGATIVE RESPONSE ONLY` | the ECU only ever answered with a negative response; its NRC is shown |

Which reply sets the length:

- The **newest session** that read the PID, ordered by date and then by its first
  capture's time, and within it the most common length (the longer one on a tie).
- Only replies that echo the PID count, so a misfiled reply (another PID's answer stored
  under this one) or a negative response never sets the length.
- A PID flagged `variable_length:` (`canair pids set-pid-variable-length`) has no single
  length, so it is audited against its **longest** reply in scope instead.
- `may include trailing frame padding` marks a length that could be padding. Captures
  made before sessions recorded a canair `version`, and anything brought in with `canair
  import uds`, may store the last CAN frame padded out to 8 bytes. The flag is raised
  only when the length is a whole number of frames and every reply of that length ends
  in the same fill byte (`00`, `55`, `AA`, `CC` or `FF`). Re-read the PID to settle it.

On a body or climate ECU whose DIDs are rows of bit flags, run
`canair investigate <ECU> --support-map` first. If a DID leads with a constant support map,
declare it with `canair pids set-support-map` and `coverage` stops counting bits that are
not signals as gaps: `BITS … missing{…}` then lists only bits the map marks, with the rest
as `(+N not signals)`.

Unmapped bytes are your next research leads — loop back to
[capture](05-capture.md) and [analyze](06-analyze.md).

> **Verified a signal? Consider contributing it back.** You don't have to wait
> for a "finished" profile — a single verified parameter (or a corrected one) is
> a welcome pull request that helps everyone with your car. See
> [Share](08-share.md#contribute-your-profile-back).

---

Next: **[8. Share →](08-share.md)**
