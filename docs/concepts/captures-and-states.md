# Captures & states

Captures are the raw evidence of your reverse-engineering. This page explains the
capture model, how it's kept safe, and how *vehicle states* make captures far
more useful.

## What a capture is

A capture is a recorded ECU response payload, tagged with context (when, which
ECU/PID, what the car was doing). They live under `captures/`, split by date
(e.g. `captures/2026-04-19.yaml`).

!!! warning "Never hand-edit capture files"
    Capture files are written by the tool (`--save`) and edited/removed via
    canair's own helpers. Hand-editing them corrupts the record. Add data via
    `canair … --save`; review it with `canair captures`.

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
canair captures --recover      # reconcile orphaned journals into capture files
canair captures --recover --discard   # or drop them unsaved
```

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
canair captures --sessions       # table of contents: date, state, label, ECUs
canair captures MyECU --summary  # stats per PID
canair captures MyECU:2101 --diff  # byte-level diff across captures
canair captures MyECU --latest    # most recent payload per PID
```

Scope any of these by date (`--since`/`--until`/`--date`) or by
`--state`/`--label` substring — `--state driving` is the natural unit of drive
analysis.
