Status: **DONE** — shipped 2026-08-04. See the CHANGELOG `[Unreleased]` entry.

# Offline vehicle-state inference & back-fill

Many older Ioniq capture sessions have no `vehicle_states`, or one set before the
current predicates existed. Since most payloads decode cleanly today, we can
infer a session's state offline by re-decoding its captures and evaluating the
profile's `vehicle_states.yaml` predicates — the offline analogue of the live
monitor's span-aware back-fill.

Measured gap (223 Ioniq sessions): 24 with no state at all; ~18 of those carry
decodable *verified* discriminators (`VCU.VEHICLE_STATE_EV_READY`,
`VCU.GEAR_PARK`, `ESC.GEAR_SELECTOR`/`REAL_SPEED_KMH`, `MCU.MCU_MOTOR_RPM`,
`IGPM.ACC2_IGN_ON`, `BMS.BATTERY_CURRENT`, `OBC.OBC_DC_A`); 6 are undecidable
(IOControl / Service-21 scans with no decodable params).

## What shipped

1. **Three-valued (Kleene) predicate logic** (`canlib/states.py`). A predicate
   depending on an unpolled param now *abstains* (`UNKNOWN`) instead of aborting
   the whole rule — so `BMS.BATTERY_CURRENT < -1 or OBC.OBC_DC_A > 0.5` resolves
   to `CHARGING` from an OBC-only read (previously inferred nothing). Offline,
   `responded` is `None`, so `__no_response__`/`__responded__` abstain (a stored
   capture *is* a response).

2. **`suggest_states()` (plural)** — returns `(matched, definitely_false)`.
   Composites fall out naturally (`READY, PARKED`); `definitely_false` powers
   conflict detection without an explicit state-axis schema field. `suggest_state`
   kept as a first-match wrapper. Both live callers (monitor, `read --save`)
   migrated to the plural form, so the monitor now auto-suggests composites too.

3. **`canlib/state_infer.py`** — pure, device-free. Groups a session's captures
   into pseudo-cycles within `cycle_tol` (default 10s, matching the stepper's
   join tolerance) so cross-ECU predicates see co-polled signals as one instant;
   untimed legacy sessions collapse to one whole-session cycle. Returns a
   `SessionInference` (union of matched states, definitely-false, cycle/param
   counts, timed flag).

4. **`canair captures uds --backfill-states`** (`_captures_backfill.py` +
   `_captures_backfill_render.py`). Follows the `cmd_delete` mutating-mode
   pattern: operate on scope-filtered entries, report first, confirm on a TTY
   unless `--yes`, never write on `--dry-run`. Default **fills** empty sessions
   only; `conflict`/`extra` are reported but written only with `--overwrite`.
   Writes via the pre-existing `captures.set_session_states` (its first
   production caller).

5. **Ioniq predicates** authored from verified signals:
   `DRIVING`/`PARKED`/`PLUGGED`/`ACC2`. `SLEEP`/`ACC` stay predicate-less (no
   trustworthy positive offline signal). `OBC.OBC_CHARGE_STATE` deliberately not
   used for `PLUGGED` (all its enum values mean "connected").

6. **`canair captures uds --set-state STATES`** (`_captures_set_state.py`) — the
   manual counterpart, added after applying the back-fill left ~11 sessions
   uninferable. Some of those are body/low-power reads whose state is known from
   the label ("ACC only", "ACC + IGN1", "no ignition") but not from any decoded
   signal (the candidate body byte `BCM_B003_B12` does not correlate with power
   state — value 245 spans SLEEP/READY/CHARGING/ACC2 — so RE-ing a body predicate
   is a dead end). `--set-state` writes the given states to every scope-selected
   session; it requires a scope filter so it can't blanket-relabel the history.
   Six label-clear sessions were tagged (ACC/ACC2/SLEEP + one AC-charging scan);
   five genuinely ambiguous ones (data doesn't discriminate, label carries no
   state) are left unlabelled by design.

## Decisions

- **Plural matching, no `axis:` schema field** — a session is a set of tokens;
  contradictions are caught via `definitely_false`, not an exclusivity model.
- **Fill-empty by default; report conflicts; `--overwrite` to correct** — never
  silently clobber a human-set state.

## Deliberately out of scope (flagged, not fixed)

- `BMS.CHARGING_DC` looks mis-decoded (reads 1 while discharging at speed) — a
  definition defect for its own investigate/decode pass; no predicate uses it.
- `PARKED` fires alongside `READY`/`CHARGING` (the car *is* in P), producing more
  tokens than the old tagging habit. Faithful to physics; only affects
  `--overwrite`/`extra` rows, never the fill-only default.
- Casing of the 84 legacy lowercase sessions (cosmetic; `parse_states`
  uppercases on read).
