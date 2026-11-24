# Steps 9–10: Verify and integrate

Scope: confirming a decode against reality, marking off worked research leads, and
integrating/shipping the result.

### 9. Verify — confirm against reality

```bash
canair decode MCU 2102 --param MCU_MOTOR_RPM   # ranges for the one signal you're verifying
canair decode MCU 2102 --stats --unverified    # distribution; validation focus
canair coverage MCU 2102                       # any bytes still unmapped?
canair validate pids                           # schema + PCI-boundary checks

canair pids upsert-param MCU 2102 MCU_MOTOR_RPM "[S10:S11]" --verified  # promote
canair pids set-status MCU 2102 done --type decode                      # close the lead
```

Promote to `--verified` only under the `verified` rule in `SKILL.md` (real data from this
vehicle as proof).

### Always mark off a worked lead (do not leave it open)

**Every time you touch a `research:` lead you MUST update its status before moving on**
— one left `pending`/`captured` is re-surfaced by `canair research` and re-worked from
scratch, wasting effort and risking re-probing the car.

```bash
canair pids set-status <ECU> "<target>" done     --type decode  # decoded, a signal exists
canair pids set-status <ECU> "<target>" captured --type verify  # candidate awaiting live check
canair pids set-status <ECU> "<target>" nrc      --type scan    # probed, ECU said no / silent
```

Every outcome counts, not just success:

- **Decoded / verified → `done`** (a real `parameters:` entry exists, promoted to
  `verified` where the rule allows).
- **Candidate still needs a live check → keep it `captured`** (a `verify`-type item)
  with the shipping, unverified signal in place, noting exactly what to test. The live check
  itself is a proposal to the user, never something to run on your own.
- **"Nothing to decode here" is also a result → `done`.** If analysis proves the
  unmapped bytes are constants/padding, counters, checksums or exact mirrors of a mapped
  signal, record that finding *with its evidence* in the lead's notes and close it — the
  negative result is the deliverable (e.g. MCU 2102 B52/B53 = counter/checksum, HVAC
  220100 FF-padding tail). Do NOT silently drop it.
- **Only part done → keep it open with a follow-up**: update `notes`/`what_to_test` so
  the next pass starts where you stopped instead of re-deriving it.

The free-text fields have their own editors, so a lead never needs hand-editing:
`pids set-research-result`, `set-research-notes` and `set-research-capture-protocol`
(omit the text to clear the field). Correct a stale capture recipe the moment the
tooling it names changes.

Rule of thumb: after any analysis or capture session, run `canair research --ecu <ECU>`
and confirm no lead you touched still shows its old status.

### 10. Integrate

```bash
canair wican autopid write   # regenerate out/[<variant>/]autopid-v{4,6}.json
canair wican autopid diff    # compare to device (optional; talks to the device: approval first)
uv run pytest -q             # keep the suite green (always `uv run` from the repo root)
```

Then consider contributing the profile upstream with **`canair contribute`** (alias
`share`) — it opens the PR via `gh` and runs a PII pre-flight first. Load the
**`contributing-profiles`** skill first: it covers the scrubbing that gates the PR and
the quality bar a shared profile must clear. For the bundled car, also consider an
upstream wican-fw PR (repo link: `ioniq-reverse-engineering/part-numbers-and-references.md`).


