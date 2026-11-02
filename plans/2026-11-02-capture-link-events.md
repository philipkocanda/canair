# Link events: recording why a capture session has gaps

Status: **DONE** (2026-11-02).

## Problem

A `monitor --save` session that loses its link keeps recording across the gap: the reconnector
re-homes the session onto the same journal and "the gap shows in the timestamps". Nothing records
*why* there is a gap, so a reader cannot tell three things apart:

- the link was down (a disconnect, or a desynchronised pipe that answered nothing coherently);
- the value did not change (a run-length `keep:changes` session stores no row for that);
- the PID simply was not due (the per-PID schedule polls a slow PID far less often).

The analysis tools cannot tell them apart either. `canlib/analysis/fill.py` carries a
`keep:changes` value forward until the next stored row, the session's end or `--max-hold`, so a
disconnect inside a run-length session reads as a minute of a steady value that nobody observed.
`quality` counts the trouble, but not *when* it happened.

## Why not `state_spans`

The first idea was to log disconnects into `state_spans`. Rejected, for three reasons:

1. **The shape cannot hold a gap.** A span carries only its start (half-open intervals), chosen
   precisely so it can express neither a gap nor an overlap. A disconnect is a gap, and it overlaps
   a vehicle state: the car stays `READY` while the WiCAN drops.
2. **Spans are derived, events are observed.** `--backfill-state-spans` and the save-time
   derivation (`source: record`) rebuild spans from stored payloads. A disconnect leaves no
   payload, so a rebuild would erase it. A link event exists only because the recorder saw it, and
   must never be recomputed.
3. **States are a vocabulary.** They feed `--state` filters, `vehicle_states.yaml`, `states
   rename`, the out-of-vocabulary check and `states_resolved`. A `DISCONNECTED` pseudo-state would
   leak into all of them. "The car was charging" and "canair lost the link" are different facts.

## Design

A session-level **`link_events`** list, a sibling of `state_spans` and `quality`, one event per
line on disk (`canlib/captures/io.py::_ONE_LINE_ITEMS`):

```json
"link_events": [
  {"at": "14:02:11.400", "until": "14:03:20.900", "kind": "disconnect", "cause": "stale"},
  {"at": "14:10:05.000", "until": "14:10:41.200", "kind": "ecu_silent", "rx": "0x7EC"},
  {"at": "14:15:00.020", "kind": "resync", "count": 2}
]
```

- **`at`** / **`until`** — `HH:MM:SS[.fff]` on the session's own date, like a capture's `time`.
  `until` absent on an interval event means it lasted until the recording ended (the monitor gave
  up, the user stopped mid-reconnect, or the process died).
- **Gaps** (nothing was gathered for their scope; they bound forward fill):
  - `disconnect` — no ECU answered coherently. `cause`: `error` (the transport raised), `stale`
    (the liveness check tripped: N poll cycles with nothing coherent) or `manual` (the TUI's
    force-reconnect key).
  - `paused` — polling was paused (space, or a modal dialog). Recorded by the TUI poll loop when
    polling actually stops and resumes, not where `paused` is set, so a cycle in flight is not
    counted inside it.
  - `ecu_silent` — one ECU (`rx`) missed three polls in a row while another ECU answered in each
    (`ECU_SILENT_POLLS`). A tick in which nothing answered is a disconnect's business and counts
    neither way.
  - `unpolled` — the `p` picker took a PID (`rx` + `pid`) out of the poll set, or a whole ECU
    (`rx` alone: one event, not one per PID; re-adding some of its PIDs splits it).
- **Not gaps**: `backoff` (`rx`, `factor`: one interval per slowdown factor `EcuBackoff` chose)
  and `resync` (an instant: the transport realigned a desynchronised ELM327 pipe; `count` when
  several landed in one poll).
- **Scope**: `rx` is the response address a capture of that ECU is filed under; an event without
  one covers the session. An ECU canair cannot resolve to an address gets no event.

**A disconnect spans the data gap, not the reconnect.** It starts at the last coherent answer
before the drop and ends at the first coherent answer after it (an `ecu_silent` likewise, for its
ECU). That is the interval a reader of
the data cares about: it includes the stale cycles before a liveness trip and a reconnect that
succeeded while nothing answered yet. An NRC counts as an answer, as it does for liveness.

**No free text.** Exception messages carry device addresses (`Connect call failed ('10.x.x.x',
…)`), and capture files are contributed upstream. Events carry enums, ECU addresses, PIDs and
counts only.

**Diagnostic only.** An ECU going quiet while others answer usually means the ECU went to sleep,
which is evidence about the car. It stays out of state inference for now (decided 2026-11-02).

### Recording

- `canlib/captures/link_events.py` — the pure leaf: the `LinkEvent` type, the kind/cause
  vocabulary, journal records → per-day event lists, and the interval queries the readers share.
- The journal gains two record types, written straight to disk because a crash mid-disconnect is
  the likeliest way a session ends: `{"type": "event", "id": n, "kind": …, "date": …, "at": …}` and
  `{"type": "event_end", "id": n, "date": …, "at": …}`. An instant event has no `id`. Reconcile
  pairs them; an unmatched start stays open (`until` absent), an unmatched end is ignored.
- A session crossing midnight is split as its captures are: the event lands on its start day
  open-ended, and the next day gets a copy from `00:00:00.000` to its `until`.
- `canlib/modes/_monitor_link_events.py::LinkEventTracker` is the monitor collaborator.
  `MonitorController.mark_disconnected(cause)` replaces every bare `disconnected = True` that means
  a link failure; the tracker opens the event at the last coherent answer and closes it on the next
  one. Every answer passes through `_displayify` with its ECU; `_observe_ecu_answers` feeds the
  per-ECU outcome from the `PollCost`s both poll backends already leave in `ecu_costs`;
  `_apply_backoff` reports each factor change; `MonitorSelectors.apply` reports the poll-set
  change. Resyncs come from the per-cycle `TransportStats` delta the poll already computes.
- **Segment rotation** (`n` in the TUI) with events open reconciles the old segment with them
  open-ended and reopens them in the new journal at the rotation instant, so each segment says what
  it saw.

### Reading

- `flatten_session` denormalises the list onto each row as `session_link_events` (one shared list
  per session, so it costs a pointer per row).
- **Forward fill stops at a gap.** `fill.next_gap_starts` gives each row the earliest gap covering
  its ECU and PID at or after it, one more bound on its window alongside the next row, the
  session's end and `--max-hold`. `backoff` and `resync` do not cut. The fill note
  (`commands/_join.py`) counts the carries a gap shortened by more than the join window, from a
  second, gap-blind hold vector (`hold_until_vector(..., respect_gaps=False)`).
- **`captures uds --sessions`** prints a `gaps:` line per kind (ECUs named) and `--json` carries the
  list.
- **`captures uds --step`** names, in the frame header (and `gaps_before` in `--json`), the gaps
  that began or ended since the previous frame and cover the frame's ECUs and PIDs
  (`commands/captures/step_gaps.py`). Stacked views only.
- **The explorer** exports the session-wide gaps (`disconnect`, `paused`) as offsets in the session
  document, shades them, and never holds a value across one: `crossesGap` in `analysis.js`, used by
  the plot alignment, the cursor readout and the discover grids.
- **`validate captures`** warns about a timed payload strictly inside a closed disconnect, pause or
  silence of its ECU — a clock or journal fault, since nothing answered then. `unpolled` is left
  out: it is bounded by the picker, and a poll already in flight may still land a reply inside it.

### Older recordings

Nothing is stored for them: an inferred event stored beside recorded ones would erode trust in
both. `canlib/captures/inferred_gaps.py` infers, for display only, the holes in a session that keeps
every poll: a stretch with no stored row from any PID longer than both 30 s and ten times the
session's median spacing, not overlapped by a recorded session-wide gap. `--sessions` prints them
as `inferred gaps:`; on the bundled Ioniq history that flags 6 of 316 sessions. `keep:changes`
gaps are unrecoverable, so run-length sessions infer nothing.

## Phases

1. **Record and show** — schema, `CaptureSession`/`CaptureEntry` types, journal records and
   reconcile, the tracker in the monitor (`disconnect` with all three causes, `resync`),
   `--sessions` text and JSON, the `validate captures` check, docs. *Built.*
2. **Forward fill respects gaps** — `fill.py`. *Built.*
3. **Per-ECU and per-PID gaps** — `rx`/`pid` scope, `ecu_silent`, `unpolled`, `backoff`, and
   `paused` (not in the first cut of the plan: a pause is as much a gap as a disconnect). Fill
   honours a scoped gap for that ECU's (and PID's) rows only. *Built.*
4. **Views** — inferred gaps in `--sessions`, the `--step` header, explorer shading, and the
   fill note's cut count. *Built.* Not done: `align`'s own `+N filled` report (`JoinStats`) does
   not count cuts, and the interleaved `--step` view names no gaps.
