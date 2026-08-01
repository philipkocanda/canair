---
name: pid-cleanup
description: Guidelines for cleaning up already-defined PIDs/params in a canair vehicle profile — pruning dead signals, tightening notes to bare facts, and keeping names honest about what is actually known. Load this when asked to "clean up" PIDs/params (as done for the HVAC ECU), audit an ECU's existing definitions, or reconcile stale/speculative names and notes against the current evidence. NOT for discovering/decoding new signals (use reverse-engineer-signal) or submitting a profile upstream (use contributing-profiles).
---

# Cleaning up PIDs / params

This is the *maintenance* pass over params that already exist in a profile's
`ecus/` — not new reverse-engineering. The goal is a profile whose definitions
state **only what the evidence proves, as of the newest evidence**: dead signals
gone, notes reduced to facts, names honest about the unknowns.

Always run `canair` as `uv run canair …` from the repo root. Edit params **only
via `canair pids`** (surgical, validated, auto-reverted on failure) — never
hand-edit `ecus/` YAML. Pass `--profile NAME` explicitly on every mutation so
you clean up the car you mean, not whatever `default_profile` resolves to.

## The four rules

### 1. Newest evidence wins

When two findings conflict (an old note vs a fresh capture, an old label vs a
newer session), the **most recent** evidence is authoritative. Establish
recency with **git**, not vibes — check when a claim was committed:

```
git log --follow -p profiles/<profile>/ecus/<ecu>.yaml
git log -1 --format=%ci <commit>     # contribution date of a specific change
```

and check the age/coverage of the backing data with `canair captures uds
--sessions` / `canair decode … --stats`. A note that says "0x02 = PTC" written
months ago loses to a session last week that shows the byte is a 3-state enum.
When you supersede an old finding, **replace** it — don't stack a correction on
top of the stale claim.

### 2. Notes state facts only — no speculation

Terse, purely factual, present-tense. **Better to have no note than a wrong or
misleading one.** A wrong note is worse than an absent one because it sends the
next person down a false trail.

- **Keep:** measured facts — the byte, the observed value range, a correlation
  with n and r, the scale/unit, what it reads in a named state, the evidence
  session date.
- **Cut:** "possibly", "likely", "maybe", "could be", guesses about meaning,
  narrative of how you found it, TODO musings, restating the expression in
  prose. Speculation about *untested* regimes belongs in a `research:` entry
  (via `canair pids add-research`), not the param note.
- If a fact is genuinely uncertain, the *name* carries the uncertainty (rule 4)
  and the note simply omits the guess.

Prefer no note over a hedged one. A one-line fact ("B42, ~0.1 kW/LSB heating
power; r=0.956 vs BATTERY_POWER n=152; reads 0 when climate off") beats a
paragraph of maybes.

### 3. Prune signals proven static

A **param** (an individual signal — not a whole PID/DID) that is **constant
across a large, varied dataset** carries no information and should be
**removed** (`canair pids rm-param ECU PID NAME`). The bar is real coverage,
not a handful of captures:

- Enough captures, **and** enough variation of ECU/vehicle **state** (sleep /
  acc / ready / driving / charging, etc.) that the byte had every chance to
  move. Check with `canair decode ECU:PID --stats` (look at `distinct`) and
  scope across states (`--group-by state`, or `--state …`); confirm the state
  spread with `canair captures uds --sessions`.
- `canair coverage` and `canair investigate ECU PID` help spot constants; a
  byte triaged `constant` across a wide `--state` spread is a removal
  candidate.

Judgement: a byte that is constant only within one state, or across a thin
dataset, is **not** proven static — leave it (optionally note the observed
constancy as a fact, or file a `research:` lead to capture the missing state).
Removing it would just re-hide a signal. Only prune what the data has genuinely
exercised.

Do **not** delete a whole PID/DID for being static — an identity/placeholder
PID is fine. This rule is about individual params.

### 4. Names reflect only what is known — minimise speculation

The param name is a claim. Keep it honest:

- **Known meaning** → name it (`HVAC_HEAT_POWER`, `HVAC_COMPRESSOR_ON`).
- **Known byte, unknown meaning** → say so explicitly with the byte in the
  name, e.g. `HVAC_B19_UNKN` (or match the ECU's existing convention —
  `HVAC_UNKNOWN_B17` is also in use; be consistent within a file). The name
  must not imply a meaning you can't back.
- **A guessed meaning that later proves wrong** → rename
  (`canair pids rename-param ECU PID OLD NEW`) and **fix every cross-reference**
  to the old name (grep the profile: expressions, notes, research targets).
  A rename that leaves dangling references is a defect.
- Disambiguate same-signal mirrors / byte variants with a `_B<n>` suffix
  (`HVAC_COOLING_MODE_B25`, `HVAC_COMPRESSOR_ON_MIRROR_B18`) so it's clear which
  byte a name is about.

When you demote a name from a guess to `_UNKN`, also strip the now-unsupported
guess from the note (rule 2).

## Workflow

1. **Scope** — pick the ECU/PID(s). `canair ecu <ECU> pids` for the current
   state, `canair coverage <ECU>` for gaps/constants, `canair investigate ECU
   PID` for a per-byte triage.
2. **Gather evidence** — `canair decode ECU:PID --stats --group-by state`,
   `canair captures uds --sessions` for state coverage, and `git log --follow`
   for the provenance/date of each existing claim (rule 1).
3. **Decide per param** — remove (proven static, rule 3), rename (dishonest or
   superseded name, rule 4), and/or rewrite the note to bare facts (rule 2).
   Move any speculation worth keeping into a `research:` lead.
4. **Apply via `canair pids`** — `rm-param` / `rename-param` / `upsert-param`
   (with `--notes`, `--type`/`--value`/`--bit`), `add-research`. Never
   hand-edit YAML. Watch the `upsert-param` decoded-range echo as a sanity
   check on the offset.
5. **Fix cross-references** — after any rename, grep the profile and update
   expressions, notes, and research targets that named the old param.
6. **Validate** — `canair validate pids` (also catches duplicate shipped param
   names) and `canair coverage` to confirm nothing regressed. Commit with a
   message that states what was pruned/renamed and the evidence date.

## Definition of done

- Every remaining param's note is a bare fact or absent — zero speculation.
- No param that is proven-static across a well-varied dataset survives.
- No name implies knowledge the evidence doesn't support; unknowns say `_UNKN`.
- No dangling references to renamed/removed params.
- `canair validate pids` passes; docs/bundled-profile index regenerated if the
  cleanup changed shipped (verified/enabled) signals.
