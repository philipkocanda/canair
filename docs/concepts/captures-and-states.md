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

- **`rx`** is the CAN **response** address (RX = request TX + 8) as a hex string
  (`"broadcast"` for multi-ECU discovery scans); tools resolve it back to the
  short name via the profile's [`ecus/`](profiles.md) registry, so you still
  query by name. (This field was named `ecu` before it was renamed to `rx` to
  make clear it holds an address, not an ECU name; readers still accept the old
  key, and `canair captures migrate-rx` renames it in existing files.)
- **`pid`** / **`payload`** are the request DID and the reassembled UDS response
  (SID-first, ISO-TP framing stripped). Decoded *values* are **not** stored —
  they're regenerated on demand from `payload` + the PID definitions, so a
  refined expression re-decodes old captures for free.
- **`elapsed_ms`** (optional) is the wall-clock UDS round-trip in milliseconds —
  a *relative* speed signal for the ECU/PID, not pure ECU processing time (it
  includes transport, WiCAN, and any ISO-TP/`responsePending` round-trips). It's
  recorded only for **single per-DID reads** on the live `query` path; batched
  multi-DID reads (one round-trip answers several PIDs), `monitor --save`, scans,
  and imports omit it. Because transports have very different fixed overhead, an
  `elapsed_ms` is comparable only **within the same session `transport`**.
- Stored as JSON because it parses ~60× faster than YAML — the dominant cost of
  every history-consuming command (`ecu`, `coverage`, `decode`, `correlate`,
  `hunt`, `investigate`).

The **authoritative, machine-checked** schema (all fields, `scan_results`,
deprecated fields) is `canlib/schema/captures_schema.json`; `canair validate
captures` checks every file against it. A profile created before the JSON
cutover is converted once with `canair captures migrate`.

## Sharing captures across machines (the merge driver)

Capture files are **append-only session logs** — a session, once written, is
never rewritten; new sessions are appended to the tail. So when two machines
both record on the same day, they each append a *different* session to the same
`captures/YYYY-MM-DD.json`. Git's line-based merge can't reconcile that (every
session ends with the same `}`/`]`/`}`, so the diff misaligns and splits the
conflict *inside* individual records) — even though the data is a trivially
mergeable list.

canair ships a git **merge driver** that resolves this automatically by unioning
the two sides' session lists (shared history collapses; disjoint appends are
kept; a genuine divergent edit falls back to normal conflict markers). It's
wired for `profiles/*/captures/*.json` via `.gitattributes`, but git will not
run a driver command it finds in a tracked file (a security measure), so **each
clone must register it once**:

```bash
canair captures merge-driver --install
```

Until a clone runs this, merges simply fall back to conflict markers — nothing
breaks, you just don't get the auto-union. (The driver itself is
`canair captures merge-driver %O %A %B %P`, invoked by git; you never call that
form by hand.)

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

- **`version`** — the canair version that recorded the session, stamped at save
  time. Provenance for debugging a capture issue traced to a specific release.
  Recorded from a git checkout (`uv run canair` in a clone) rather than an
  installed release, it also carries that checkout's branch and short commit —
  `1.15.0+main.343b244`, with `.dirty` appended when tracked files had uncommitted
  edits — so a suspect reading points at the exact code that produced it, not just
  the release it happened to sit near. Sessions recorded before version stamping
  was added simply omit the field.
- **`transport`** — how the payloads were acquired: the transport label
  (`slcan-tcp` / `wican-ws`) for a device-recorded session, or `import` for a
  device-free `canair import uds`.
- **`quality`** — the transport's exchange/error tally for the session:
  `exchanges` (total UDS round-trips) plus any **non-zero** error categories
  (`drop`/`stale` = dropped/mis-assembled ISO-TP frames, `no_data` = timeouts,
  `bus`, `decode`). A clean session records just `exchanges`; NRCs are
  legitimate ECU answers and are **not** counted. `resyncs` rides along in the
  same block when non-zero: it counts the times the transport had to realign a
  desynchronised ELM327 pipe — a *recovery* rather than a lost payload, but
  several of them mark a marginal link.

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
the base layer's captures are **read-only**: `--delete`, `--set-state` and
`--backfill-states` refuse when a matched row belongs to the base, naming the file.
`--dry-run` still previews it, and rows in your own layer are freely editable.

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
quit. The scrollable live view shows a blinking `● REC` whenever a `--save`
recording is active, and two keys control the session:

- **`s` (label recording)** — set or edit the label / state / notes for the
  **current** recording. Because every payload is already journaled and saved on
  exit, this only *labels* the session — it doesn't write a separate file (the
  modal and confirmation say so). The state field is **free text**
  (comma-separated) pre-filled with the auto-suggested state; leaving it blank is
  fine — the modal says so (`no state set — will auto-detect from data on save`),
  and the state is filled in on save. (Without `--save`, `s` instead performs a
  one-off write of the payloads captured so far to a new capture file.)
- **`n` (finish & start new)** — write the current session to its own capture
  file **now**, then start a **fresh** recording, labelled via the same modal.
  One monitor run can thus produce several independently-labelled sessions —
  press `n` at each phase change (e.g. parked → driving → charging) rather than
  stopping and restarting. (`n` requires `--save`; without it there's no
  recording to finish.)
- **`i` (session info)** — open a read-only overlay summarising the run: the
  current segment's label / state / notes and start time, the run-level counters
  (frames captured / unique, cycles, retain mode, poll interval, transport, run
  start / elapsed), where captures and the **write-ahead journal** are being
  written (the exact `.journal/*.jsonl` path `--recover` would read), and the
  history of the `--save` segments already finished this run (each with its label,
  states, time span, frame count, and the file it was written to). The current
  segment name also shows in the header bar at all times; use `s` to
  rename/relabel it.
- **`V` (view mode)** — cycle how much of each signal the live view shows:
  `ecus` (just the responding ECUs and a PID/signal count), `ranges` (each
  signal's captured value *span* — numeric min–max or distinct labels, the way
  `canair investigate`/`decode` report a range), `signals` (the decoded values
  only), and `full` (signals plus the raw byte payloads — the default).
- **`r` (byte ruler)** — number the payload's byte columns above the hex, and
  show each signal's byte reference next to its value, both in your preferred
  notation (see [Byte indexing](byte-indexing.md)).

Signals are always listed in **payload order** — sorted by the byte each one
reads first — so a value reads directly above the hex byte it came from. The view
never follows the tail: a repaint leaves your scroll position exactly where it
was, so you can read a byte while data keeps arriving (`G`/`End` jumps to the
newest output when you want it).

When a `--save` segment ends without an explicit state, canair back-fills it with
the **union of every state auto-suggested across that segment's whole span** — not
just the state active at the instant it closed. So a segment that charged and then
went idle still reconciles as `charging`, rather than losing the label because the
car happened to stop charging just before you stopped recording. If the car changed
state *during* the segment, the saved session also carries a
[`state_spans` timeline](#simultaneous-vs-sequential-overlap-state_spans) so each
capture still answers for the state it was actually recorded in.

The **status bar** answers the opposite question — what the car is doing *now* —
so its inputs expire: a decoded value stops feeding the state predicates a few
poll cycles after it was last read, which returns that signal to "unknown"
instead of leaving it asserted forever. Without that, a parked car kept reading
`DRIVING` from the last speed sample of the drive. Expiry is counted in **poll
cycles, not seconds**, so it scales with the sweep: polling a dozen ECUs takes far
longer per cycle than polling two, and a fixed wall-clock window would expire the
wide sweep's values before it could refresh them.

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


State names are an **UPPERCASE** controlled vocabulary (like the CAN-bus segment
codes) — the make-neutral base is the ignition-switch ladder `SLEEP/ACC/RUN/CRANK`
(the universal OFF/ACC/ON/START positions; `RUN`/`SLEEP` because `ON`/`OFF` are
YAML booleans, and `RUN` avoids the "which IGN level?" ambiguity of vendors that
number them, e.g. Hyundai IGN0-3), plus any states a profile declares in its
`vehicle_states.yaml` (EV profiles add `PLUGGED`/`READY`/`CHARGING`; the bundled
Ioniq does), any composites, and the meta-token **`ALL`** ("applicable in every
state"). Input is normalized to uppercase, so any casing you type is accepted.
Inspect and edit the vocabulary with `canair states`:

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
`DRIVING → READY → ACC2 → ACC` and `CHARGING → PLUGGED`.

Where it applies:

- **Single-state views narrow to the most specific match** — the monitor status
  bar shows `DRIVING`, dropping every state another match implies (transitively:
  a match on DRIVING also suppresses ACC2 and ACC). States unrelated in the
  hierarchy both survive, and file order decides between them.
- **Recordings keep every match.** A `--save` segment's states, the save
  dialog's pre-fill and `captures uds --backfill-states` are unchanged: they
  store `READY, DRIVING`, because the session genuinely was in both.
- **`--state` widens through it when filtering.** `--state ready` selects a
  `DRIVING` capture, because driving *is* a reading of ready. The converse does
  not hold: `--state driving` never returns a merely-`READY` capture.
- **Analysis groups by the reduced set.** `decode --group-by state` and
  `investigate`'s discriminability ranking bucket `READY, ACC2, PARKED` as
  `READY, PARKED` — one bucket, not two near-duplicates. Before this, a nine-state
  vocabulary produced **39** distinct buckets from the same data, splitting each
  population thin enough to distort the F ranking (and, where a split bucket held
  two identical samples, to manufacture a bogus perfect separation).

Edit it with `canair states` (never hand-edit the file):

```bash
canair states set-implies DRIVING READY      # declare / replace
canair states set-implies DRIVING            # clear
canair states add DRIVING --implies READY --when "ESC.REAL_SPEED_KMH > 0.5"
```

`canair validate states` (and every `canair states` edit) rejects a target that
isn't a declared state, a state implying itself, an `implies: [ALL]` (`ALL` is
the "readable in every state" meta-token, not a state to specialize), and any
cycle — a cycle would make "more specific than" meaningless. Removing or
renaming a state retargets the states that referenced it.

### Simultaneous vs sequential overlap (`state_spans`)

A session holds a *set* of states, but a recording holds a *span of time* — and
those two facts fight each other. Two very different things look identical on disk:

- **Simultaneous** — a parked, ready car is `READY, PARKED` for the whole session.
  Every capture in it really does answer for both. The union is exact.
- **Sequential** — you drove, parked, plugged in and charged, all in one recording.
  The session is `READY, DRIVING, PARKED, PLUGGED, CHARGING`, but no single instant
  was all of those.

Left alone, the second case corrupts filtering: `--state charging` returns the
whole session, driving captures included. On the bundled Ioniq's own history that
was **17.7% of everything `--state CHARGING` returned**, including 9,162 captures
recorded at speed.

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

A span records a *change*, not an interval — one timestamp and the states that
held from it until the next span. Half-open intervals would need arbitration for
the gap left by a poll cycle that decoded nothing; a change point simply has no
gaps. The timeline is derived from the session's own stored payloads, so it costs
one write and no new measurement (52 spans for a 6,872-capture drive: 2.4 KB in a
4.2 MB file).

Filtering and analysis then read the state **at each capture's own timestamp**,
with the union as a fallback when there is no timeline. `--state charging` on that
session returns only the charging captures. Corpus-wide, the provably-wrong share
of filtered rows fell from ~18–21% to **0.01%**.

Nothing about the session-level union is lost — `captures uds --sessions` still
reports what the whole recording covered, because "what did this session span?"
and "what was the car doing when *this* byte was read?" are different questions.

**Declare what cannot co-occur (`excludes:`).** canair cannot tell simultaneous
from sequential on its own — `READY, PARKED` is fine, `DRIVING, PARKED` is not, and
only the vehicle's physics says which. So a state declares its incompatibilities,
the complement of `implies:`:

```bash
canair states set-excludes DRIVING PARKED,CHARGING   # symmetric — either side suffices
```

`canair validate captures` then warns on a session tagged with an exclusive pair
but *no* timeline — the one shape that is provably wrong — and points at the fix.
Declaring the pairs is what keeps that warning specific instead of firing on every
multi-state session until you learn to ignore it. `validate states` rejects a pair
declared both exclusive and implied, since `implies:` says the two always hold
together and `excludes:` says they never can.

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

One limit worth knowing: a state canair cannot *place* in time — one with no
`when:` predicate, like `SLEEP`, or one that never matched the stored payloads —
is carried into **every** span rather than dropped. Losing it would make captures
stop matching a state they used to match, trading a precision problem for a data-loss
one.

### Predicates are cross-checked against the signal registry

Kleene logic has one sharp edge: a predicate that reads a signal which *does not
exist* abstains exactly like one whose signal merely wasn't polled. Both are
UNKNOWN, so a typo'd or renamed signal name leaves the state permanently
un-suggestable with nothing to see — no error, no warning, no failed decode.

So the reference is resolved statically instead. `canair validate states` errors
when a `when:` predicate names a signal the profile's `ecus/` doesn't define,
reporting the specific reason:

```
vehicle_states.yaml: 1 errors
  - state 'PARKED': when: references VCU.GEAR_PARK — VCU defines no signal 'GEAR_PARK'
```

A reference must match how the decoded value map is actually keyed at evaluation
time, so each way of missing is reported distinctly: an unknown ECU, an ECU
**alias** instead of its canonical short name, a lower-case ECU (the map is keyed
UPPERCASE), a case-mismatched signal name (matched case-sensitively, with the real
name suggested), a signal defined on a *different* ECU, a signal under a
`status: ignored` PID (never polled), and a signal that doesn't decode to a
number — `ascii`/`date`/`struct` render as text, and only numeric values reach a
predicate. The `__no_response__` / `__responded__` sentinels are not signals and
are never checked.

The same check runs at the moments a reference can break, as a non-blocking
warning (authoring a predicate before its signal exists stays allowed):

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

By default it only **fills** sessions that have no recorded state; a session
whose recorded state is *provably contradicted* by the decoded evidence (a
`conflict`) is reported but left untouched unless you pass `--overwrite`. It
honors the usual scope filters (`--since`/`--state`/`--label`/`--last-session`),
previews with `--dry-run`, emits `--json`, and confirms before writing unless
`--yes`. Cross-ECU predicates need co-polled signals seen at roughly one instant,
so timed captures are grouped into pseudo-cycles within `--cycle-tol` seconds
(default 10s); untimed legacy sessions collapse to one whole-session cycle.

`--backfill-states` answers "*which* states was this session in?"; its companion
[`--backfill-state-spans`](#simultaneous-vs-sequential-overlap-state_spans) answers
"*when* was it in each of them?". Run the former first — spans can only place states
the session is tagged with.

Offline, a capture existing *is* a response, so the `__no_response__` /
`__responded__` sentinels can't be evaluated (a predicate using them abstains) —
which is why `SLEEP` has no offline predicate.

Some sessions can't be inferred at all — a body/low-power ECU read taken while
the powertrain state signals were asleep, or a scan with no decodable payloads.
When you *know* the state from the session's label or context (e.g. an "ACC only"
bench read), set it manually:

```bash
canair captures uds --set-state ACC --label "ACC only" --dry-run   # preview
canair captures uds --set-state "CHARGING, PLUGGED" --date 2026-04-18
```

`--set-state` writes the given states to every **scope-selected** session
(`--label`/`--date`/`--since`/…), so it *requires* a scope filter — it refuses a
bare invocation that would relabel the whole history. Like the other mutating
modes it previews with `--dry-run`, emits `--json`, and confirms unless `--yes`.
A genuinely ambiguous session (the data doesn't discriminate and the label
carries no state) is best left unlabelled — a guessed state is worse than none.

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
