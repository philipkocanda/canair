# Captures & states

Captures are the raw evidence of your reverse-engineering. This page explains the
capture model, how it's kept safe, and how *vehicle states* make captures far
more useful.

## What a capture is

A capture is a recorded ECU response payload, tagged with context (when, which
ECU/PID, what the car was doing). They live under `captures/`, split by date
(e.g. `captures/2026-04-19.json`).

!!! warning "Never hand-edit capture files"
    Capture files are written by the tool (`--save`) and edited/removed via
    canair's own helpers. Hand-editing them corrupts the record. Add data via
    `canair … --save`; review it with `canair captures`.

!!! tip "Every save says where it landed"
    Any command that writes captures prints the **full path** of the file it
    wrote — `→ Saved 12 capture(s) to /…/profiles/<car>/captures/2026-04-19.json`
    — so you always know which profile got the data. In `canair monitor` the TUI
    owns the screen, so a save made while it's running (`s` without `--save`, or
    an `n` segment rotate) reports its path once the monitor exits and the
    terminal is yours again.

## File format

Each `captures/YYYY-MM-DD.json` holds one day, as sessions of captures:

```json
{
  "sessions": [
    {
      "date": "2026-04-19",
      "label": "highway pull",
      "vehicle_states": ["driving"],
      "captures": [
        { "rx": "0x7EC", "pid": "2101", "payload": "6101FFE0…", "time": "14:02:11.480", "elapsed_ms": 47 }
      ]
    }
  ]
}
```

- **`rx`** is the CAN **response** address as a hex string — the ECU's `rx_id`, else request
  TX + the profile's `addressing.rx_offset` (`+0x08` by default). Tools map it back to the
  ECU's short name via the profile's [`ecus/`](profiles.md) registry, so you still query by
  name. Multi-ECU discovery scans use `"broadcast"`, and each entry in `scan_results.responding`
  carries its own `tx` and `rx`. (Older files used the key `ecu`; readers still accept it, and
  `canair captures migrate-rx` renames it.)
- **`pid`** / **`payload`** are the request DID and the reassembled UDS response
  (SID-first, ISO-TP framing stripped). Decoded *values* are **not** stored —
  they're regenerated on demand from `payload` + the PID definitions, so a
  refined expression re-decodes old captures for free.
- **`elapsed_ms`** (optional) is the wall-clock UDS round-trip in milliseconds, including
  transport, WiCAN and any `responsePending` waits. Treat it as a *relative* speed signal,
  comparable only within the same session `transport`. It is recorded only for single per-DID
  reads on the live `read` path — not for batched multi-DID reads, `monitor --save`, scans or
  imports.
- Stored as JSON because it parses ~60× faster than YAML — the dominant cost of
  every history-consuming command (`ecu`, `coverage`, `decode`, `correlate`,
  `hunt`, `investigate`).

### Repeated polls are folded on disk

A long recording polls a stationary signal over and over and gets the same payload back each
time. On disk, a poll that repeats the previous payload of the same ECU and PID is not written
as a row of its own. It is folded into the run's first capture as a gap in milliseconds since
the previous poll of the run, and each capture sits on one line:

```json
      "captures": [
        {"rx":"0x7BB","pid":"220100","payload":"62010…","time":"00:00:36.314","repeat_ms":[38822,45093,17789]},
        {"rx":"0x7A8","pid":"22B001","payload":"62B00…","time":"00:00:36.402"}
      ]
```

The first row stands for four polls: 00:00:36.314, then 38.822 s later, then 45.093 s after
that, and so on. `repeat_ms` repeats are slotted back among the other PIDs' rows by their
time; `repeat_inline_ms` (in a session stored grouped by PID) follow their row directly.

Folding is **lossless**: every command loads the file with each repeat expanded back into
its own capture, in the original order, so nothing downstream knows about it. canair folds
only what it can restore exactly, which is why a capture with `elapsed_ms` or a note, or one
with an untimed or non-standard `time`, stays a row of its own. Every write uses this form,
and `canair captures compact` rewrites the files nothing has touched since (the bundled
Ioniq's largest day went from 9.0 MB to 2.0 MB). Read the files through canair; a plain JSON
parser sees the folded form.

The **authoritative, machine-checked** schema (all fields, `scan_results`,
deprecated fields) is `canlib/schema/captures_schema.json`; `canair validate
captures` checks every file against it. A profile created before the JSON
cutover is converted once with `canair captures migrate`.

## Sharing captures across machines (the merge driver)

Capture files are **append-only session logs**: new sessions go on the end, and a session is
only rewritten in place by the editors and repairs below, which never change what identifies
it. So when two machines record on the same day, each appends a *different* session to the
same `captures/YYYY-MM-DD.json`. Git's line-based merge can't reconcile that — every session
ends with the same `}`/`]`/`}`, so the conflict lands *inside* records — even though the data
is a trivially mergeable list.

canair ships a git **merge driver** that unions the two sides' session lists: shared history
collapses, disjoint appends are kept, an edit on one side beats the untouched other, and a
genuinely divergent edit falls back to normal conflict markers. `.gitattributes` wires it up
for `profiles/*/captures/*.json`, but git won't run a driver command from a tracked file, so
**each clone must register it once**:

```bash
canair captures merge-driver --install
```

Until then, merges fall back to conflict markers — nothing breaks, you just lose the
auto-union.

**How a session is recognised.** A session has no id. Two copies are the same when their
content is identical, or the same *recording, edited* when they share its date, label,
recording version and first capture's time, `rx` and PID — fields no editor or repair
rewrites. That second rule lets an edited session replace its original (in a merge, in
`canair contribute`, and across a layered profile's base and your layer) instead of being
counted twice. Deleting a session's first capture changes those fields, so the trimmed
session reads as a different recording.

## Provenance: transport & data quality

Each recorded session also carries **where its data came from** and **how clean
the connection was** — provenance for judging how much to trust it (multi-frame
ISO-TP payloads recorded over a flaky link were the reason historical captures
became suspect; see the note below):

```json
{
  "date": "2026-04-19",
  "label": "highway pull",
  "version": "1.8.1",
  "vehicle_states": ["driving"],
  "transport": "slcan-tcp",
  "quality": { "exchanges": 412, "drop": 0, "no_data": 3 },
  "captures": [ … ]
}
```

- **`version`** — the canair version that recorded the session, for tracing a capture issue to
  a release. Recorded from a git checkout, it also carries the branch and short commit
  (`1.15.0+main.343b244`, plus `.dirty` for uncommitted edits). Older sessions omit it.
- **`transport`** — how the payloads were acquired: the transport label
  (`slcan-tcp` / `wican-ws`) for a device-recorded session, or `import` for a
  device-free `canair import uds`.
- **`quality`** — the transport's exchange/error tally for the session:
  `exchanges` (total UDS round-trips) plus any **non-zero** error categories
  (`drop`/`stale` = dropped/mis-assembled ISO-TP frames, `no_data` = timeouts,
  `bus`, `decode`). A clean session records just `exchanges`; NRCs are legitimate ECU
  answers and are **not** counted. A non-zero `resyncs` counts times the transport had to
  realign a desynchronised ELM327 pipe — a recovery, not a lost payload, but several mark a
  marginal link.

`canair captures uds --sessions` shows all three per session (flagging any
drops), and every error is also written to the central log — inspect it with
`canair logs`.


## Recording captures

Add `--save` to a read, with context flags:

```bash
canair read MyECU:2101 --save --label "highway" --state driving --notes "…"
```

`--save` works with `read`, `monitor`, `scan`, and `discover`.

!!! note "Truncated reads are rejected, not stored"
    A multi-frame ISO-TP response is self-describing — its first frame declares
    the total length. If a consecutive frame is dropped (the ELM327 terminal
    occasionally returns a short read), the reassembled payload falls short of
    that declared length and every byte after the gap is misaligned. canair
    rejects such reads (`truncated ISO-TP: got N bytes, declared M`) so a
    corrupt payload is never saved. The check is generic — no per-PID length
    table — and needs no configuration.

## Removing captures

Delete captures with the same query mini-language used to view them, scoped by
the usual `--since`/`--state`/`--label` filters. Preview with `--dry-run`; the
delete confirms interactively unless you pass `--yes`:

```bash
canair captures uds OBC 2101 --delete --dry-run   # preview what would go
canair captures uds OBC 2101 --delete             # confirm, then delete
canair captures uds OBC 2101 --delete --yes        # non-interactive
```

`--delete` refuses to run without a QUERY (it never deletes everything). It
addresses captures through canair's own helpers, so the record stays consistent
— never hand-edit files to remove data.

On a [layered profile](profiles.md#layering-your-captures-over-someone-elses-definitions)
the base layer's captures are **read-only**: `--delete`, the `--set-*` editors
and `--backfill-states` refuse when a matched row belongs to the base, naming the
file. `--dry-run` still previews it, and rows in your own layer are freely
editable.

## Journaling — you won't lose data

Saves are **journaled**: written to a write-ahead log under `captures/.journal/`
as they stream, and reconciled into the dated capture file when the session
exits. A killed, crashed, or disconnected session is therefore never lost:

```bash
canair captures uds --recover      # reconcile orphaned journals into capture files
canair captures uds --recover --discard   # or drop them unsaved
```

## Recording in the live monitor

`canair monitor … --save` records continuously: every poll cycle is
journaled as it arrives and written to a capture file automatically when you
quit. Every poll is kept (`--keep-all`, the default), so the stored rows show the real
polling cadence; repeats cost little space because they are
[folded on disk](#repeated-polls-are-folded-on-disk). `--keep-changes` instead stores only
value-transitions per PID (run-length), and the session records `keep_mode: changes` so the
analysis tools know to [carry each value forward](analysis-commands.md#forward-fill-a-run-length-row-is-a-segment-not-a-sample). The live view
collapses each run of identical payloads into one row with its poll count (`×37`) and shows
the newest few (`--keep N` sets how many). The scrollable live view shows a blinking `● REC`
whenever a `--save` recording is active, and two keys control the session:

- **`s` (label recording)** — set or edit the label / state / notes of the **current**
  recording. Everything is already journaled, so this only *labels* the session; it doesn't
  write a separate file. The state field is free text (comma-separated), pre-filled with the
  **union of every state auto-suggested so far in the segment** — not just the one active as
  you press `s`, so a top-up that ended a minute ago is still offered. Accepting the
  pre-fill unchanged (or leaving it blank) keeps the state automatic, so states seen after
  the keypress still count; typing a different state pins it. (Without `--save`, `s`
  instead writes the payloads captured since the previous `s` to a new capture file,
  pre-filled with the union of states seen over that span.)
- **`n` (finish & start new)** — write the current session to its own capture file **now**
  and start a **fresh** recording, labelled via the same modal (pre-filled with the state
  active now, and likewise automatic unless you change it). Press it at each phase change
  (parked → driving → charging) to get several independently-labelled sessions from one run.
  Requires `--save`.
- **`i` (session info)** — a read-only overlay with the current segment's label / state /
  notes, the run-level counters (frames, cycles, poll interval and schedule, transport,
  elapsed time), the
  exact `.journal/*.jsonl` path `--recover` would read, and the `--save` segments already
  finished this run. The current segment name is always shown in the header bar.
- **`V` (view mode)** — cycle how much the live view shows: `ecus` (responding ECUs and a
  PID/signal count), `ranges` (each signal's captured min–max or distinct labels), `signals`
  (decoded values only), or `full` (signals plus raw payload bytes — the default).
- **`r` (byte ruler)** — number the payload's byte columns above the hex, and
  show each signal's byte reference next to its value, both in your preferred
  notation (see [Byte indexing](byte-indexing.md)).

Signals are listed in **payload order** (by the first byte each reads), so a value sits
directly above the hex byte it came from. The view never follows the tail — a repaint leaves
your scroll position alone, and `G`/`End` jumps to the newest output.

When a `--save` segment ends without an explicit state, canair back-fills it with the
**union of every state auto-suggested across the whole segment**, not just the one active
when it closed — so a segment that charged and then went idle still reconciles as
`charging`. If the car changed state *during* the segment, the session also carries a
[`state_spans` timeline](#simultaneous-vs-sequential-overlap-state_spans) so each capture
still answers for the state it was recorded in.

The **status bar** shows what the car is doing *now*, so its inputs expire: a decoded value
stops feeding the state predicates a few poll cycles after it was last read, and the signal
returns to "unknown". Otherwise a parked car kept reading `DRIVING` from the last speed
sample. Expiry counts **poll cycles, not seconds**, so it scales with how many ECUs you poll.

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
parameter that wasn't polled *abstains* (neither matches nor is falsified) rather
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

Edit it with `canair states` (never hand-edit the file):

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

## Reviewing captures

```bash
canair captures uds --sessions       # table of contents: date, state, label, ECUs
canair captures uds --summary        # stats per ECU/PID/date
canair captures uds MyECU:2101 --diff  # byte-level diff across captures
canair captures uds MyECU --latest    # most recent payload per PID
canair captures can                  # list imported raw broadcast-CAN frame logs
```

Scope any of these by date (`--since`/`--until`/`--date`) or by
`--state` token / `--label` substring — `--state driving` is the natural unit of drive
analysis.
