# Vehicle states

A *vehicle state* says what the car was doing when canair recorded a capture, for example `driving`
or `charging`. States make captures far more useful, because a byte often shows its meaning only
relative to what the car does. This page explains the state vocabulary, how canair detects states,
and how to repair the states of old captures. For the capture model, see [Captures &
states](captures-and-states.md).

## Vehicle states

A byte's meaning often only becomes clear *relative to what the car is doing*. A
value that's constant while parked but ramps while driving is a different kind of
signal from one that only flips when charging. The **state** you tag a capture
with (`DRIVING`, `CHARGING`, `READY`, `SLEEP`, …) is what powers state-aware
analysis like `decode --group-by state` and `investigate`'s discriminability
ranking.

States are defined per-profile in `vehicle_states.yaml` — a canonical, ordered vocabulary
of power states, each with an optional predicate over decoded values. Because of
those predicates, canair can **auto-suggest** a capture's state from the data it
just read, so tagging is mostly automatic. A session is naturally **composite** —
*every* predicate that matches contributes, so a parked, ready car reads as
`READY, PARKED`. Predicate order is display order, not priority: it only
tie-breaks states unrelated in the hierarchy below.

Predicates use **three-valued (Kleene) logic**: a predicate that depends on a
signal that wasn't polled *abstains* (neither matches nor is falsified) rather
than reading false. So `BMS.BATTERY_CURRENT < -1 or OBC.OBC_DC_A > 0.5` still
resolves to `CHARGING` from an OBC-only read where the BMS wasn't queried.


State names are an **UPPERCASE** controlled vocabulary. The make-neutral base is the
ignition ladder `SLEEP/ACC/RUN/CRANK` (`RUN`/`SLEEP` rather than `ON`/`OFF`, which YAML reads as
booleans), plus whatever states the profile declares in `vehicle_states.yaml` (EV profiles add
`PLUGGED`/`READY`/`CHARGING`) and the meta-token **`ALL`**, meaning "applicable in every state".
A profile can still use the manufacturer's word for a state, as the bundled Ioniq does with `ON`
(the Start/Stop button's ON position), but in YAML it has to be quoted: `vehicle_states: ["ON"]`.
A bare `ON` reads as `true`, so `canair validate` rejects it, and every `canair` editor writes
the quotes for you.
Any casing you type is normalized. Tag an ECU or PID `vehicle_states: [ALL]` when it answers in
every state (the Ioniq's IGPM and BCM never sleep) instead of listing each one. Inspect and edit
the vocabulary with `canair states`:

```bash
canair states                          # list the vocabulary + usage counts
canair states add PRECONDITION -d "Cabin pre-conditioning"
canair states set-predicate CHARGING "BMS.BATTERY_CURRENT < -1"
canair validate states                 # check the vocabulary + its signal references
```

### Some states are more specific than others (`implies:`)

Composite is right for a *recording* — a segment that spanned a drive really was
both `READY` and `DRIVING` — but a view with room for one state has to choose,
and "the first one declared" is the wrong answer: the monitor's status bar read
`READY` while the car was driving.

A state therefore declares which broader states it **specializes**:

```yaml
  - name: DRIVING
    implies: [READY]           # driving is a *more specific* reading of READY
    when: ESC.REAL_SPEED_KMH > 0.5 or MCU.MCU_MOTOR_RPM > 100
```

This is entailment, not priority — DRIVING isn't "more important" than READY, it
*means* READY plus motion. So the hierarchy needs no renumbering when you add a
state, and it reads as documentation. The bundled Ioniq declares
`DRIVING → READY → ON → ACC`, `CHARGING → PLUGGED`, `TOPUP → PARKED`, and READY, CHARGING
and TOPUP each `→ IG3` (the EV supply rail the battery-side ECUs run on).

Where it applies:

- **Single-state views narrow to the most specific match** — the monitor status
  bar shows `DRIVING`, dropping every state another match implies (transitively:
  a match on DRIVING also suppresses ON and ACC). States unrelated in the
  hierarchy both survive, and file order decides between them.
- **Recordings keep every match.** A `--save` segment's states, the save
  dialog's pre-fill and `captures uds --backfill-states` are unchanged: they
  store `READY, DRIVING`, because the session genuinely was in both.
- **`--state` widens through it when filtering.** `--state ready` selects a
  `DRIVING` capture, because driving *is* a reading of ready. The converse does
  not hold: `--state driving` never returns a merely-`READY` capture.
- **ECU availability widens the same way.** An ECU tagged `vehicle_states: [IG3]`
  (readable whenever the IG3 rail is on) is listed by `canair states READY`,
  because READY implies IG3; one tagged `[READY]` is not listed under `IG3`.
- **Analysis groups by the reduced set.** `decode --group-by state` and
  `investigate`'s discriminability ranking bucket `READY, ON, PARKED` as
  `READY, PARKED` — one bucket, not two near-duplicates. Without it, a nine-state vocabulary
  split the same data into 39 buckets, thin enough to distort the F ranking.

Edit it with `canair states`, which also updates every reference when you rename a state:

```bash
canair states set-implies DRIVING READY      # declare / replace
canair states set-implies DRIVING            # clear
canair states add DRIVING --implies READY --when "ESC.REAL_SPEED_KMH > 0.5"
```

`canair validate states` (and every `canair states` edit) rejects a target that
isn't a declared state, a state implying itself, an `implies: [ALL]` (`ALL` is
the "readable in every state" meta-token, not a state to specialize), and any
cycle — a cycle would make "more specific than" meaningless. Removing a state
retargets the states that referenced it. Renaming one goes further and rewrites
every reference in the profile — the `ecus/` tags, each recorded session's states
and timeline, the DTC log and the raw-CAN log index — all or nothing:

```bash
canair states rename ACC2 ON --dry-run    # list every file and reference it rewrites
canair states rename ACC2 ON             # asks before rewriting recorded data
```

### Simultaneous vs sequential overlap (`state_spans`)

A session holds a *set* of states, but a recording holds a *span of time* — and
those two facts fight each other. Two very different things look identical on disk:

- **Simultaneous** — a parked, ready car is `READY, PARKED` for the whole session.
  Every capture in it really does answer for both. The union is exact.
- **Sequential** — you drove, parked, plugged in and charged, all in one recording.
  The session is `READY, DRIVING, PARKED, PLUGGED, CHARGING`, but no single instant
  was all of those.

Left alone, the second case corrupts filtering: `--state charging` returns the whole session,
driving captures included (17.7% of what `--state CHARGING` returned on the Ioniq's history).

So a session whose payloads show the car changing state carries a **timeline**
alongside the union:

```json
"vehicle_states": ["READY", "DRIVING", "PARKED", "CHARGING"],
"state_spans": {
  "source": "record",
  "spans": [
    {"at": "15:47:56.192", "states": ["READY", "DRIVING"]},
    {"at": "16:31:12.044", "states": ["READY", "PARKED"]},
    {"at": "16:31:44.901", "states": ["CHARGING", "PLUGGED", "PARKED"]}
  ]
}
```

A span records a *change*: one timestamp and the states that held from it until the next
span, so there are no gaps to arbitrate. The timeline is derived from the session's own stored
payloads and is tiny (52 spans for a 6,872-capture drive).

Filtering and analysis then read the state **at each capture's own timestamp**, falling back to
the union when there is no timeline. `--state charging` on that session returns only the
charging captures. The session-level union is kept too: `captures uds --sessions` still reports
what the whole recording covered.

**Declare what cannot co-occur (`excludes:`).** canair cannot tell simultaneous
from sequential on its own — `READY, PARKED` is fine, `DRIVING, PARKED` is not, and
only the vehicle's physics says which. So a state declares its incompatibilities,
the complement of `implies:`:

```bash
canair states set-excludes DRIVING PARKED,CHARGING   # symmetric — either side suffices
```

`canair validate captures` then warns on a session tagged with an exclusive pair but *no*
timeline — the one provably wrong shape — and points at the fix. `validate states` rejects a
pair declared both exclusive and implied.

**Back-filling old recordings.** Sessions recorded before this existed have no
timeline. Derive one from their stored payloads:

```bash
canair captures uds --backfill-state-spans --dry-run   # preview every verdict
canair captures uds --backfill-state-spans             # write
```

Each session gets one of five verdicts: **timeline** (states changed — spans
written), **flat** (the states really were simultaneous, union already exact),
**single-state**, **no evidence** (too little decodable data to place a change),
and **live** (spans observed during recording, left alone unless `--overwrite`).
A capture that ends up with no timeline and more than one state is reported as
unresolved rather than silently trusted.

One limit: a state canair cannot *place* in time (no `when:` predicate, like `SLEEP`, or never
matched in the payloads) is carried into **every** span rather than dropped, so captures never
stop matching a state they used to match.

### Predicates are cross-checked against the signal registry

Kleene logic has a sharp edge: a predicate that reads a signal which *does not exist*
abstains exactly like one whose signal merely wasn't polled. A typo'd or renamed signal
therefore leaves the state permanently un-suggestable, with no error. So `canair validate
states` errors when a `when:` predicate names a signal the profile's `ecus/` doesn't define,
and says why:

```
vehicle_states.yaml: 1 errors
  - state 'PARKED': when: references VCU.GEAR_PARK — VCU defines no signal 'GEAR_PARK'
```

The reasons reported are: an unknown ECU, an ECU **alias** instead of its canonical short
name, a lower-case ECU, a case-mismatched signal name (the real name is suggested), a signal
defined on a *different* ECU, a signal under a `status: ignored` PID (never polled), and a
signal that isn't numeric (`ascii`/`date`/`struct` values never reach a predicate). The
`__no_response__` / `__responded__` sentinels are not signals and are never checked.

The same check runs as a non-blocking warning where a reference can break:

- `canair states add --when …` / `canair states set-predicate …` — warns if the
  new predicate can't resolve.
- `canair pids rename-param` / `rm-param` — warns which states read the signal
  you just renamed or removed.
- `canair states` marks a dead predicate `✗` instead of `●` in the `AUTO` column
  and prints the reason under it (`--json` carries a per-state `broken` array).

### Back-filling states on old captures

Older sessions may have no state, or one recorded before the current predicates
existed. Because most payloads decode cleanly today, canair can **infer** a
session's state offline by re-decoding its captures and evaluating the same
`vehicle_states.yaml` predicates:

```bash
canair captures uds --backfill-states --dry-run   # preview: report only, writes nothing
canair captures uds --backfill-states             # fill sessions that have no state
canair captures uds --backfill-states --overwrite # also correct recorded states that conflict
```

By default it only **fills** sessions with no recorded state; one whose state is *provably
contradicted* by the decoded evidence (a `conflict`) is reported but left alone unless you pass
`--overwrite`. It honors the usual scope filters, previews with `--dry-run`, emits `--json`, and
confirms before writing unless `--yes`. Cross-ECU predicates need signals seen at roughly one
instant, so timed captures are grouped into pseudo-cycles within `--cycle-tol` seconds (default
10); untimed legacy sessions count as one cycle.

`--backfill-states` answers "*which* states was this session in?"; its companion
[`--backfill-state-spans`](#simultaneous-vs-sequential-overlap-state_spans) answers
"*when* was it in each of them?". Run the former first — spans can only place states
the session is tagged with.

Offline, the `__no_response__` / `__responded__` sentinels can't be evaluated (a stored capture
*is* a response), which is why `SLEEP` has no offline predicate.

Some sessions can't be inferred at all — say a body ECU read while the powertrain signals were
asleep, or a scan with no decodable payloads. When you *know* the state from the label or
context, set it manually:

```bash
canair captures uds --set-state ACC --label "ACC only" --dry-run   # preview
canair captures uds --set-state "CHARGING, PLUGGED" --date 2026-04-18
```

`--set-state` writes the given states to every **scope-selected** session, so it *requires* a
scope filter (`--label`/`--date`/`--since`/…) and refuses to relabel the whole history. Like the
other mutating modes it supports `--dry-run`, `--json` and `--yes`. If a session is genuinely
ambiguous, leave it unlabelled — a guessed state is worse than none.

A session's **notes** can be corrected the same way, e.g. when they claim an ECU answered that
the session's own data shows was silent:

```bash
canair captures uds --set-notes "Car off; only 0x758 answered." --label "discovery" --dry-run
canair captures uds --set-notes "" --date 2026-04-18 --yes   # '' clears the notes
```

`--set-notes` has the same scope requirement and flags as `--set-state`; with `--json`, stdout
carries only the JSON rows and the human report goes to stderr. The `--step` TUI's note editor
does the same interactively.

`--set-transport NAME` corrects a session's recorded **transport** the same way, accepting only
a known transport label (or `''` to clear it). Older versions could record the wrong transport
for `discover`/`scan`/`raw` sessions; current ones record the transport actually used.

### Repairing legacy discover captures

Older discover captures stored only an `rx` per responder, computed as TX + 0x08 for any
address not in the ECU registry, whatever the profile's `addressing:` said. This repair gives
each responder its probed `tx` and the `rx` your profile's addressing gives it:

```bash
canair --profile my-car captures migrate-discover --dry-run   # preview
canair --profile my-car captures migrate-discover
```

The TX is recovered from how those captures were labelled; a responder whose ECU no longer
resolves, is ambiguous, or has a label that isn't a single ECU name is reported rather than
guessed. The repair is idempotent and can't recover the session's transport (use
`--set-transport`). In a layered profile it resolves names against the base's definitions and
repairs only your layer.

### Splitting multi-DID replies stored under one DID

An ECU that supports batching answers `22 D1 D2 …` with `62 D1 <data> D2 <data> …`, which starts
exactly like a single reply to `D1`. Older versions could mistake a late batch reply for the
answer to the next per-DID read of `D1` and save it whole under `D1`. Current versions discard
such a reply and stop batching that ECU for the session, and `canair validate captures` warns
about rows already stored. This repair replaces each with one capture per DID at the same
time, keeping the readings:

```bash
canair --profile my-car captures split-batched --dry-run   # preview
canair --profile my-car captures split-batched
```

A row is split only when it walks exactly through DIDs of the same ECU at each DID's
established length (taken from its own replies), so a padded or merely long reply is left
alone. In a `keep:changes` (or legacy `keep:unique`) session, a part the session wouldn't have
stored again is left out. The repair is idempotent. In a layered profile it reports rows in the
read-only base instead of rewriting them, and also overlay sessions the base holds identically
(splitting only the overlay copy would turn one session into two).

