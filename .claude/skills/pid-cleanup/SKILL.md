---
name: pid-cleanup
description: Guidelines for cleaning up already-defined PIDs/signals in a canair vehicle profile — pruning dead signals, tightening notes to bare facts, and keeping names honest about what is actually known. Load this when asked to "clean up" PIDs/signals (as done for the HVAC ECU), audit an ECU's existing definitions, or reconcile stale/speculative names and notes against the current evidence. NOT for discovering/decoding new signals (use reverse-engineer-signal) or submitting a profile upstream (use contributing-profiles).
---

# Cleaning up PIDs / signals

This is the *maintenance* pass over signals that already exist in a profile's
`ecus/` — not new reverse-engineering. The goal is a profile whose definitions
state **only what the evidence proves, as of the newest evidence**: dead signals
gone, notes reduced to facts, names honest about the unknowns.

Always run `canair` as `uv run canair …` from the repo root. Edit signals **only
via `canair pids`** (surgical, validated, auto-reverted on failure) — never
hand-edit `ecus/` YAML. Pass `--profile NAME` explicitly on every mutation so
you clean up the car you mean, not whatever `default_profile` resolves to.

## The four rules

### 1. Newest evidence wins

When two findings conflict (an old note vs a fresh capture, an old label vs a newer session),
the **most recent** evidence is authoritative. Establish recency with **git**
(`git log --follow -p profiles/<profile>/ecus/<ecu>.yaml`), and check the age and coverage of
the backing data with `canair captures uds --sessions` / `canair decode … --stats`. When you
supersede an old finding, **replace** it. Do not stack a correction on top of the stale claim.

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
  (via `canair pids add-research`), not the signal note.
- **Cut what repeats.** A note that recurs on many of a page's signals (the source, how
  it was checked, a byte order, a description restating the name) belongs once in the
  PID note (`set-pid-notes`), or nowhere.
- If a fact is genuinely uncertain, the *name* carries the uncertainty (rule 4)
  and the note simply omits the guess.

Prefer no note over a hedged one. A one-line fact ("B42, ~0.1 kW/LSB heating
power; r=0.956 vs BATTERY_POWER n=152; reads 0 when climate off") beats a
paragraph of maybes.

### 3. Prune signals proven static

A **signal** (an individual field, not a whole PID/DID) that is **constant
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
- A **support map** settles it faster: `canair investigate ECU --support-map`
  names the bytes (or bits, or fields) the map leaves unmarked. On this unit
  they are filler, so a signal defined there is a removal candidate, and
  `coverage` reports it as `OUTSIDE MAP` once the map is declared. Check the
  map's verdict first: a `REJECTED`/`BROKEN` fit, or a documented exception like
  BCM `22B005`, proves nothing.

Judgement: a byte that is constant only within one state, or across a thin
dataset, is **not** proven static — leave it (optionally note the observed
constancy as a fact, or file a `research:` lead to capture the missing state).
Removing it would just re-hide a signal. Only prune what the data has genuinely
exercised.

Do **not** delete a whole PID/DID for being static — an identity/placeholder
PID is fine. This rule is about individual signals.

### 4. Names reflect only what is known — minimise speculation

The signal name is a claim. Keep it honest:

- **Known meaning** → name it (`HVAC_COMPRESSOR_POWER`, `HVAC_HEAT_PUMP_ON`).
- **Meaning verified in one regime only** → name only what the test covered.
  A flag verified only against heat-pump heating is `HVAC_HEAT_PUMP_ON`, not
  `HVAC_COMPRESSOR_ON`.
- **Known byte, unknown meaning** → say so explicitly with the byte in the
  name, e.g. `HVAC_UNKNOWN_B19` (match the ECU's existing convention and be consistent within a
  file; add the page, e.g. `HVAC_A0_UNKNOWN_B23`, where the same byte number recurs across
  PIDs). The name must not imply a meaning you can't back.
- **A guessed meaning that later proves wrong** → rename
  (`canair pids rename-param ECU PID OLD NEW`) and **fix every cross-reference**
  to the old name (grep the profile: expressions, notes, research targets).
  A rename that leaves dangling references is a defect.
- Disambiguate same-signal mirrors / byte variants with a `_B<n>` suffix
  (`HVAC_AAT_MIRROR_B17`, `HVAC_HEAT_PUMP_ON_MIRROR_B18`) so it's clear which
  byte a name is about.

When you demote a name from a guess to an `_UNKNOWN_B<n>` name, also strip the now-unsupported
guess from the note (rule 2).

## Workflow

1. **Scope** — pick the ECU/PID(s). `canair ecu <ECU> pids` for the current state, `canair
   coverage <ECU>` for gaps/constants, `canair investigate <ECU>` (a bare ECU ranks every
   captured PID) for a per-byte triage.
2. **Gather evidence** — `canair decode ECU:PID --stats --group-by state`, `canair captures uds
   --sessions` for state coverage, and `git log --follow` for the date of each existing claim
   (rule 1). Check payload lengths per PID before you trust a range or correlation: a legacy
   capture with a dropped or duplicated ISO-TP frame shifts every later byte. Re-derive any byte
   number a note cites with `canair bix -a`, since older notes mix WiCAN and ISO-TP numbering.
3. **Decide per signal** — remove (rule 3), rename (rule 4), and/or rewrite the note (rule 2).
   Move speculation worth keeping into a `research:` lead.
4. **Apply via `canair pids`** — `rm-param` / `rename-param` / `upsert-param`, `add-research`,
   and `set-pid-notes <ECU> <PID> "…"` for the PID-level note (omit the text to clear it). That
   note ages fastest, since it describes what the page *is* while the decode underneath keeps
   changing. Watch the `upsert-param` decoded-range echo as a check on the offset. Run `canair
   pids hoist-defaults <ECU> --dry-run` to find `unit`/`min`/`max`/`verified`/`source`/
   `source_links` repeated across a page's signals. Drop `--dry-run` to state them once on the
   PID (nothing resolves differently).
5. **Fix cross-references** — after any rename, grep the profile and update expressions, notes
   and research targets that named the old signal.
6. **Validate** — `canair validate pids` (also catches duplicate shipped names) and `canair
   coverage` to confirm nothing regressed. Commit with a message that states what was
   pruned/renamed and the evidence date.

## Definition of done

- Every remaining signal's note is a bare fact or absent — zero speculation.
- No signal that is proven-static across a well-varied dataset survives.
- No name implies knowledge the evidence doesn't support; unknowns say `UNKNOWN_B<n>`.
- No dangling references to renamed/removed signals.
- `canair validate pids` passes; docs/bundled-profile index regenerated if the
  cleanup changed shipped (verified, not `ship: false`) signals.
