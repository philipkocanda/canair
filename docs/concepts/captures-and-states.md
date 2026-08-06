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
  legitimate ECU answers and are **not** counted.

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
car happened to stop charging just before you stopped recording.

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
`READY, PARKED`. Predicate order is display order only, not priority.

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
canair validate states                 # check the vocabulary
```

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
`--state`/`--label` substring — `--state driving` is the natural unit of drive
analysis.
