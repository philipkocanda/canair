# 7. Define & verify

[Analysis](06-analyze.md) gave you a hypothesis confirmed by data — say,
*"byte 12 of `MyECU:2101` correlates with a known speed signal at r=0.99, ~1:1,
so it's speed in km/h."* Now you turn that into a named **parameter** stored in
the profile — the unit that makes `canair read MyECU` decode the value for
anyone using your profile.

This is the heart of **contributing PIDs**: a raw byte becomes a documented,
validated, shareable signal.

## Write the parameter

Never hand-edit `ecus/`. Use `canair pids upsert-param`, which is surgical,
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
- `--source` — *why you believe it* (correlation, a reference car, a datasheet).
  Evidence, not vibes.
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

If you ever find yourself reaching for a text editor because no `canair pids`
subcommand covers a field, that's a [bug to report](../contributing/index.md) —
not a reason to hand-edit.

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

Unmapped bytes are your next research leads — loop back to
[capture](05-capture.md) and [analyze](06-analyze.md).

> **Verified a signal? Consider contributing it back.** You don't have to wait
> for a "finished" profile — a single verified parameter (or a corrected one) is
> a welcome pull request that helps everyone with your car. See
> [Share](08-share.md#contribute-your-profile-back).

---

Next: **[8. Share →](08-share.md)**
