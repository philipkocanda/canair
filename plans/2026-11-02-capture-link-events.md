# Link events: recording why a capture session has gaps

Status: **IN PROGRESS** — phases 1 and 2 built (2026-11-02); 3 and 4 open.

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
  {"at": "14:15:00.020", "kind": "resync", "count": 2}
]
```

- **`at`** / **`until`** — `HH:MM:SS[.fff]` on the session's own date, like a capture's `time`.
  `until` absent on an interval event means it lasted until the recording ended (the monitor gave
  up, the user stopped mid-reconnect, or the process died).
- **`kind`** — `disconnect` (an interval: no usable data from any ECU) or `resync` (an instant:
  the transport realigned a desynchronised ELM327 pipe; `count` when several landed in one poll).
- **`cause`** (disconnects) — `error` (the transport raised), `stale` (the liveness check tripped:
  N poll cycles with nothing coherent) or `manual` (the TUI's force-reconnect key).

**A disconnect spans the data gap, not the reconnect.** It starts at the last coherent answer
before the drop and ends at the first coherent answer after it. That is the interval a reader of
the data cares about: it includes the stale cycles before a liveness trip and a reconnect that
succeeded while nothing answered yet. An NRC counts as an answer, as it does for liveness.

**No free text.** Exception messages carry device addresses (`Connect call failed ('10.x.x.x',
…)`), and capture files are contributed upstream. Events carry enums and counts only.

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
  one. Resyncs come from the per-cycle `TransportStats` delta the poll already computes.
- **Segment rotation** (`n` in the TUI) with an event open reconciles the old segment with it
  open-ended and reopens it in the new journal at the rotation instant, so each segment says what
  it saw.

### Reading

- `flatten_session` denormalises the list onto each row as `session_link_events` (one shared list
  per session, so it costs a pointer per row).
- **Forward fill stops at a gap.** `hold_until_vector` takes the earliest disconnect starting at
  or after a row as one more bound on its window, alongside the next row, the session's end and
  `--max-hold`. Instants do not cut.
- **`captures uds --sessions`** prints a `gaps:` line (disconnect count, total duration, resyncs)
  and `--json` carries the list.
- **`validate captures`** warns about a timed payload strictly inside a closed disconnect — a clock
  or journal fault, since nothing answered then.

### Older recordings

Nothing is stored for them. `keep:all` gaps could be inferred (a gap far longer than the PID's
period), but an inferred event stored beside recorded ones would erode trust in both. `keep:changes`
gaps are unrecoverable. Inference, if built, is a read-time view only (phase 4).

## Phases

1. **Record and show** — schema, `CaptureSession`/`CaptureEntry` types, journal records and
   reconcile, the tracker in the monitor (`disconnect` with all three causes, `resync`),
   `--sessions` text and JSON, the `validate captures` check, docs. *Built.*
2. **Forward fill respects gaps** — `fill.py`. *Built.*
3. **Per-ECU and per-PID gaps** — an optional `rx` scope: `ecu_silent` from the per-ECU unanswered
   counts behind liveness, `backoff` from `EcuBackoff`, and an `unpolled` interval when the `p`
   picker drops a PID. Fill would honour a scoped gap for that ECU's rows only.
4. **Views** — read-time inferred gaps for `keep:all` sessions (marked as inferred, never stored),
   shading in the `--step` TUI and the explorer plots, and reporting how many fills a gap cut.
