# Vehicle states

A *vehicle state* says what the car was doing when canair recorded a capture, for example `driving`
or `charging`. States make captures more useful, because a byte often shows its meaning only
relative to what the car does. This page explains the state vocabulary, how canair detects states,
and how to repair the states of old captures. For the capture model, see [Captures &
states](captures-and-states.md).

## Vehicle states

The meaning of a byte often becomes clear only *relative to what the car is doing*. A value that
is constant while parked but ramps while driving is a different kind of signal from one that
flips only when charging. The **state** that you tag a capture with (`DRIVING`, `CHARGING`,
`READY`, `SLEEP`, …) enables state-aware analysis. Examples are `decode --group-by state` and the
discriminability ranking of `investigate`.

You define states per profile in `vehicle_states.yaml`. It holds a canonical, ordered vocabulary
of power states. Each state has an optional predicate over decoded values. Because of those
predicates, canair can **auto-suggest** the state of a capture from the data it just read, so
tagging is mostly automatic.

A session is naturally **composite**: *every* predicate that matches contributes. A parked, ready
car therefore reads as `READY, PARKED`. Predicate order is display order, not priority. It only
breaks ties between states that are unrelated in the hierarchy below.

Predicates use **three-valued (Kleene) logic**. A predicate that depends on a signal that canair
did not poll *abstains*. It neither matches nor is falsified, and it does not read false. So
`BMS.BATTERY_CURRENT < -1 or OBC.OBC_DC_A > 0.5` still resolves to `CHARGING` from an OBC-only
read where canair did not query the BMS.

State names are an **UPPERCASE** controlled vocabulary. It contains:

- the make-neutral base: the ignition ladder `SLEEP/ACC/RUN/CRANK` (`RUN`/`SLEEP` rather than
  `ON`/`OFF`, which YAML reads as booleans)
- the states that the profile declares in `vehicle_states.yaml` (electric vehicle (EV) profiles
  add `PLUGGED`/`READY`/`CHARGING`)
- the meta-token **`ALL`**, which means "applicable in every state"

A profile can still use the word of the manufacturer for a state. The bundled Ioniq does this
with `ON` (the ON position of the Start/Stop button). In YAML you must quote it:
`vehicle_states: ["ON"]`. A bare `ON` reads as `true`, so `canair validate` rejects it. Every
`canair` editor writes the quotes for you. canair normalizes any casing that you type.

Tag an electronic control unit (ECU) or parameter identifier (PID) `vehicle_states: [ALL]` when it
answers in every state (the Ioniq's IGPM and BCM never sleep). This replaces a list of every
state. Inspect and edit the vocabulary with `canair states`:

```bash
canair states                          # list the vocabulary + usage counts
canair states add PRECONDITION -d "Cabin pre-conditioning"
canair states set-predicate CHARGING "BMS.BATTERY_CURRENT < -1"
canair validate states                 # check the vocabulary + its signal references
```

### Some states are more specific than others (`implies:`)

Composite is right for a *recording*. A segment that spanned a drive really was both `READY` and
`DRIVING`. A view with room for only one state has to choose, and "the first one declared" is the
wrong answer. The status bar of the monitor read `READY` while the car was driving.

A state therefore declares which broader states it **specializes**:

```yaml
  - name: DRIVING
    implies: [READY]           # driving is a *more specific* reading of READY
    when: ESC.REAL_SPEED_KMH > 0.5 or MCU.MCU_MOTOR_RPM > 100
```

This is entailment, not priority. DRIVING is not "more important" than READY. It *means* READY
plus motion. The hierarchy therefore needs no renumbering when you add a state, and it reads as
documentation.

The bundled Ioniq declares `DRIVING → READY → ON → ACC`, `CHARGING → PLUGGED` and
`TOPUP → PARKED`. READY, CHARGING and TOPUP each also imply `IG3` (the EV supply rail that the
battery-side ECUs run on).

Where it applies:

- **Single-state views narrow to the most specific match.** The monitor status bar shows
  `DRIVING`. It drops every state that another match implies, transitively: a match on DRIVING
  also suppresses ON and ACC. States that are unrelated in the hierarchy both survive, and file
  order decides between them.
- **Recordings keep every match.** The states of a `--save` segment, the pre-fill of the save
  dialog and `captures uds --backfill-states` are unchanged. They store `READY, DRIVING`, because
  the session genuinely was in both.
- **`--state` widens through it when filtering.** `--state ready` selects a `DRIVING` capture,
  because driving *is* a reading of ready. The converse does not hold: `--state driving` never
  returns a merely-`READY` capture.
- **ECU availability widens the same way.** An ECU tagged `vehicle_states: [IG3]` (readable
  whenever the IG3 rail is on) is listed by `canair states READY`, because READY implies IG3. An
  ECU tagged `[READY]` is not listed under `IG3`.
- **Analysis groups by the reduced set.** `decode --group-by state` and the discriminability
  ranking of `investigate` bucket `READY, ON, PARKED` as `READY, PARKED`. That is one bucket, not
  two near-duplicates. Without this, a nine-state vocabulary split the same data into 39 buckets.
  These buckets were thin enough to distort the F ranking.

Edit it with `canair states`, which also updates every reference when you rename a state:

```bash
canair states set-implies DRIVING READY      # declare / replace
canair states set-implies DRIVING            # clear
canair states add DRIVING --implies READY --when "ESC.REAL_SPEED_KMH > 0.5"
```

`canair validate states` (and every `canair states` edit) rejects these cases:

- a target that is not a declared state
- a state that implies itself
- an `implies: [ALL]` (`ALL` is the "readable in every state" meta-token, not a state to
  specialize)
- any cycle, because a cycle would make "more specific than" meaningless

Removing a state retargets the states that referenced it. Renaming one goes further. It rewrites
every reference in the profile: the `ecus/` tags, the states and timeline of each recorded
session, the diagnostic trouble code (DTC) log and the raw Controller Area Network (CAN) log
index. The rewrite is all or nothing:

```bash
canair states rename ACC2 ON --dry-run    # list every file and reference it rewrites
canair states rename ACC2 ON             # asks before rewriting recorded data
```

### Simultaneous vs sequential overlap (`state_spans`)

A session holds a *set* of states, but a recording holds a *span of time*. Those two facts fight
each other. Two very different things look identical on disk:

- **Simultaneous** — A parked, ready car is `READY, PARKED` for the whole session. Every capture
  in it really does answer for both. The union is exact.
- **Sequential** — You drove, parked, plugged in and charged, all in one recording. The session is
  `READY, DRIVING, PARKED, PLUGGED, CHARGING`, but no single instant was all of those.

Left alone, the second case corrupts filtering. `--state charging` returns the whole session,
driving captures included (17.7% of what `--state CHARGING` returned on the Ioniq's history).

So a session whose payloads show the car changing state carries a **timeline** alongside the
union:

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

A span records a *change*: one timestamp and the states that held from it until the next span.
There are no gaps to arbitrate. canair derives the timeline from the stored payloads of the
session. It is tiny (52 spans for a 6,872-capture drive).

Filtering and analysis then read the state **at the timestamp of each capture**. They fall back to
the union when there is no timeline. `--state charging` on that session returns only the charging
captures. canair keeps the session-level union too: `captures uds --sessions` still reports what
the whole recording covered.

**Declare what cannot co-occur (`excludes:`).** canair cannot tell simultaneous from sequential on
its own. `READY, PARKED` is fine, `DRIVING, PARKED` is not, and only the physics of the vehicle
says which. So a state declares its incompatibilities, the complement of `implies:`:

```bash
canair states set-excludes DRIVING PARKED,CHARGING   # symmetric — either side suffices
```

`canair validate captures` then warns on a session that is tagged with an exclusive pair but has
*no* timeline. This is the one provably wrong shape. The warning points at the fix.
`validate states` rejects a pair that is declared both exclusive and implied.

**Back-filling old recordings.** Sessions recorded before timelines existed have no timeline.
Derive one from their stored payloads:

```bash
canair captures uds --backfill-state-spans --dry-run   # preview every verdict
canair captures uds --backfill-state-spans             # write
```

Each session gets one of five verdicts:

- **timeline**: the states changed, and canair wrote spans
- **flat**: the states really were simultaneous, and the union is already exact
- **single-state**
- **no evidence**: too little decodable data to place a change
- **live**: canair observed the spans during recording, and leaves them alone unless you pass
  `--overwrite`

canair reports a capture that ends up with no timeline and more than one state as unresolved. It
does not silently trust it.

One limit: canair cannot *place* some states in time. A state with no `when:` predicate (like
`SLEEP`), or one that never matched in the payloads, goes into **every** span rather than being
dropped. Captures therefore never stop matching a state they used to match.

### Predicates are cross-checked against the signal registry

Kleene logic has a sharp edge. A predicate that reads a signal which *does not exist* abstains
exactly like one whose signal canair merely did not poll. A typo'd or renamed signal therefore
leaves the state permanently un-suggestable, with no error. So `canair validate states` reports an
error when a `when:` predicate names a signal that `ecus/` of the profile does not define. It also
says why:

```
vehicle_states.yaml: 1 errors
  - state 'PARKED': when: references VCU.GEAR_PARK — VCU defines no signal 'GEAR_PARK'
```

The reasons reported are:

- an unknown ECU
- an ECU **alias** instead of its canonical short name
- a lower-case ECU
- a case-mismatched signal name (canair suggests the real name)
- a signal defined on a *different* ECU
- a signal under a `status: ignored` PID (never polled)
- a signal that is not numeric (`ascii`/`date`/`struct` values never reach a predicate)

The `__no_response__` / `__responded__` sentinels are not signals, and canair never checks them.

The same check runs as a non-blocking warning where a reference can break:

- `canair states add --when …` / `canair states set-predicate …` warns if the new predicate cannot
  resolve.
- `canair pids rename-param` / `rm-param` warns which states read the signal that you just renamed
  or removed.
- `canair states` marks a dead predicate `✗` instead of `●` in the `AUTO` column and prints the
  reason under it (`--json` carries a per-state `broken` array).

### Back-filling states on old captures

Older sessions may have no state, or one that was recorded before the current predicates existed.
Most payloads decode cleanly today, so canair can **infer** the state of a session offline. It
re-decodes the captures and evaluates the same `vehicle_states.yaml` predicates:

```bash
canair captures uds --backfill-states --dry-run   # preview: report only, writes nothing
canair captures uds --backfill-states             # fill sessions that have no state
canair captures uds --backfill-states --overwrite # also correct recorded states that conflict
```

By default it only **fills** sessions with no recorded state. It reports a session whose state the
decoded evidence *provably contradicts* (a `conflict`), but leaves it alone unless you pass
`--overwrite`. It honors the usual scope filters, previews with `--dry-run`, emits `--json`, and
confirms before writing unless you pass `--yes`.

Cross-ECU predicates need signals seen at roughly one instant. canair therefore groups timed
captures into pseudo-cycles within `--cycle-tol` seconds (default 10). It counts untimed legacy
sessions as one cycle.

`--backfill-states` answers "*which* states was this session in?". Its companion
[`--backfill-state-spans`](#simultaneous-vs-sequential-overlap-state_spans) answers "*when* was it
in each of them?". Run the former first, because spans can only place states that the session is
tagged with.

Offline, canair cannot evaluate the `__no_response__` / `__responded__` sentinels (a stored
capture *is* a response). This is why `SLEEP` has no offline predicate.

Some sessions cannot be inferred at all. Examples are a body ECU read while the powertrain signals
were asleep, or a scan with no decodable payloads. When you *know* the state from the label or
context, set it manually:

```bash
canair captures uds --set-state ACC --label "ACC only" --dry-run   # preview
canair captures uds --set-state "CHARGING, PLUGGED" --date 2026-04-18
```

`--set-state` writes the given states to every **scope-selected** session. It therefore *requires*
a scope filter (`--label`/`--date`/`--since`/…) and refuses to relabel the whole history. Like the
other mutating modes, it supports `--dry-run`, `--json` and `--yes`. If a session is genuinely
ambiguous, leave it unlabelled. A guessed state is worse than none.

You can correct the **notes** of a session the same way. An example is when the notes claim that
an ECU answered but the data of the session shows it was silent:

```bash
canair captures uds --set-notes "Car off; only 0x758 answered." --label "discovery" --dry-run
canair captures uds --set-notes "" --date 2026-04-18 --yes   # '' clears the notes
```

`--set-notes` has the same scope requirement and flags as `--set-state`. With `--json`, stdout
carries only the JSON rows and the human report goes to stderr. The note editor of the `--step`
text user interface (TUI) does the same interactively.

`--set-transport NAME` corrects the recorded **transport** of a session the same way. It accepts
only a known transport label (or `''` to clear it). Older versions could record the wrong
transport for `discover`/`scan`/`raw` sessions. Current versions record the transport actually
used.

### Repairing legacy discover captures

Older discover captures stored only an `rx` per responder. canair computed it as TX + 0x08 for any
address not in the ECU registry, whatever the profile's `addressing:` said. This repair gives each
responder its probed `tx` and the `rx` that the addressing of your profile gives it:

```bash
canair --profile my-car captures migrate-discover --dry-run   # preview
canair --profile my-car captures migrate-discover
```

canair recovers the TX from how those captures were labelled. It reports a responder instead of
guessing when the ECU no longer resolves, is ambiguous, or has a label that is not a single ECU
name. The repair is idempotent and cannot recover the transport of the session (use
`--set-transport`). In a layered profile it resolves names against the definitions of the base and
repairs only your layer.

### Splitting multi-DID replies stored under one DID

An ECU that supports batching answers `22 D1 D2 …` with `62 D1 <data> D2 <data> …`. This starts
exactly like a single reply to `D1`. Older versions could mistake a late batch reply for the
answer to the next per-DID (data identifier) read of `D1`, and save it whole under `D1`. Current
versions discard such a reply and stop batching that ECU for the session.
`canair validate captures` warns about rows already stored. This repair replaces each with one
capture per DID at the same time, and keeps the readings:

```bash
canair --profile my-car captures split-batched --dry-run   # preview
canair --profile my-car captures split-batched
```

canair splits a row only when it walks exactly through DIDs of the same ECU at the established
length of each DID (taken from its own replies). A padded or merely long reply stays as it is. In
a `keep:changes` (or legacy `keep:unique`) session, canair leaves out a part that the session
would not have stored again. The repair is idempotent. In a layered profile, it reports rows in
the read-only base instead of rewriting them. It also reports overlay sessions that the base holds
identically (splitting only the overlay copy would turn one session into two).
