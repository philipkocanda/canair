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
        { "ecu": "0x7EC", "pid": "2101", "payload": "6101FFE0…", "time": "14:02:11.480" }
      ]
    }
  ]
}
```

- **`ecu`** is the CAN **response** address (RX = request TX + 8) as a hex string
  (`"broadcast"` for multi-ECU discovery scans); tools resolve it back to the
  short name via the profile's [`ecus/`](profiles.md) registry, so you still
  query by name.
- **`pid`** / **`payload`** are the request DID and the reassembled UDS response
  (SID-first, ISO-TP framing stripped). Decoded *values* are **not** stored —
  they're regenerated on demand from `payload` + the PID definitions, so a
  refined expression re-decodes old captures for free.
- Stored as JSON because it parses ~60× faster than YAML — the dominant cost of
  every history-consuming command (`ecu`, `coverage`, `decode`, `correlate`,
  `hunt`, `investigate`).

The **authoritative, machine-checked** schema (all fields, `scan_results`,
deprecated fields) is `canlib/schema/captures_schema.json`; `canair validate
captures` checks every file against it. A profile created before the JSON
cutover is converted once with `canair captures migrate`.


## Recording captures

Add `--save` to a read, with context flags:

```bash
canair query MyECU:2101 --save --label "highway" --state driving --notes "…"
```

`--save` works with `query`, `scan`, `discover`, and live `--monitor`.

## Journaling — you won't lose data

Saves are **journaled**: written to a write-ahead log under `captures/.journal/`
as they stream, and reconciled into the dated capture file when the session
exits. A killed, crashed, or disconnected session is therefore never lost:

```bash
canair captures uds --recover      # reconcile orphaned journals into capture files
canair captures uds --recover --discard   # or drop them unsaved
```

## Recording in the live monitor

`canair query … --monitor --save` records continuously: every poll cycle is
journaled as it arrives. The scrollable live view shows a blinking `● REC`
whenever a `--save` recording is active, and two keys control the session:

- **`s`** — set or edit the label / state / notes for the **current** session
  (the modal states which segment you're labelling). This only updates metadata;
  payloads are already being recorded. The vehicle state is auto-suggested from
  decoded values.
- **`n`** — close the current segment (save it to its own capture file) and start
  a **fresh** one, labelled via the same modal. One monitor run can thus produce
  several independently-labelled sessions — press `n` at each phase change (e.g.
  parked → driving → charging) rather than stopping and restarting.

## Vehicle states

A byte's meaning often only becomes clear *relative to what the car is doing*. A
value that's constant while parked but ramps while driving is a different kind of
signal from one that only flips when charging. The **state** you tag a capture
with (`driving`, `charging`, `ready`, `sleep`, …) is what powers state-aware
analysis like `decode --group-by state` and `investigate`'s discriminability
ranking.

States are defined per-profile in `states.yaml` — a canonical, ordered vocabulary
of power states, each with an optional predicate over decoded values. Because of
those predicates, canair can **auto-suggest** a capture's state from the data it
just read, so tagging is mostly automatic.

```bash
canair validate states     # check the vocabulary
```

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
