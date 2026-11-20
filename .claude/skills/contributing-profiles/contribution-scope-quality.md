# Contribution scope and the quality bar

What counts as a contribution, and how to prepare a profile before submitting.

## What a contribution is

- **A whole new car** — a `profiles/<your-car>/` bundle you built with the
  bring-your-own-car journey.
- **More of an existing profile** — newly-decoded params, a corrected byte
  offset, a fixed expression, added captures as evidence.
- **A partial lead** — even a handful of `--verified` signals, one corrected
  offset, or a new `research:` entry is welcome. You do *not* need a "finished"
  profile.

Whichever it is, the data lives in a **profile bundle** (`ecus/`, `profile.yaml`,
`captures/`, `vehicle_states.yaml`, `can_buses.yaml`, optional `signals/` +
`captures/can/`, `references/`, generated `out/`). Inspect the active one with
`canair profile show`.


## Prepare the profile — the quality bar

Before submitting, make the profile clean and honest:

- **`canair validate all` passes.** This covers schema + the duplicate
  shipped-signal-name check + soft warnings (out-of-vocabulary states, misfiled
  captures, non-hex payloads, degraded-transport sessions). Read the warnings —
  they often point at a real defect. Use `--strict` to also fail on untimed
  payload captures if you want the CI-grade gate.
- **Prefer verified params with a `--source`.** A verified parameter should carry
  the evidence that verified it (`--source`, `--notes`); an honest `--unverified`
  candidate is fine and welcome, just labelled as such. Author these with
  `canair pids upsert-param` (never hand-edit `ecus/`). Values shared by a whole page (`unit`,
  `min`, `max`, `verified`, `source`, `source_links`) go on the PID once
  (`set-pid-defaults`; `hoist-defaults` folds up repeats) — signals that set none of their own
  inherit them. A note true of the whole page goes in the PID note (`set-pid-notes`), not on
  every signal.
- **`car_model` pins down the vehicle** — model / year / market / battery — so
  another owner can tell whether the profile matches *their* car. Set it via the
  profile scaffold or `canair config`/profile tooling, not by hand-editing if a
  tool reaches it.
- **Include representative captures as evidence, not everything.** Captures back
  up the decode, but they can be large — a representative subset is enough.
  `canair contribute --no-captures` ships definitions only when the evidence is
  too bulky or sensitive to include.
- **Keep free-text technical.** `--label`/`--notes`, `research:` notes, DTC-log
  labels, and profile descriptions must stay technical — they're a common PII
  leak vector (see below).
