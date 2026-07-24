# 7. Define & verify

[Analysis](06-analyze.md) gave you a hypothesis confirmed by data — say,
*"byte 12 of `MyECU:2101` correlates with a known speed signal at r=0.99, ~1:1,
so it's speed in km/h."* Now you turn that into a named **parameter** stored in
the profile — the unit that makes `canair query MyECU` decode the value for
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

## Audit for gaps

`canair coverage` cross-references your parameter expressions against the longest
captured payload and flags what's still undecoded:

```bash
canair coverage MyECU          # unmapped bytes, partial bitfields, no-capture PIDs
canair validate all           # schema + duplicate-name checks
```

Unmapped bytes are your next research leads — loop back to
[capture](05-capture.md) and [analyze](06-analyze.md).

> **Verified a signal? Consider contributing it back.** You don't have to wait
> for a "finished" profile — a single verified parameter (or a corrected one) is
> a welcome pull request that helps everyone with your car. See
> [Share](08-share.md#contribute-your-profile-back).

---

Next: **[8. Share →](08-share.md)**
