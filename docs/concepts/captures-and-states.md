# Captures & states

Captures are the raw evidence of your reverse engineering. This page explains the capture model,
how to record, review and remove captures, and how canair keeps your data safe.

Two related pages go deeper:

- [Vehicle states](vehicle-states.md) explains how a *vehicle state* (such as `driving` or
  `charging`) makes captures more useful.
- [Capture file format and data quality](capture-format.md) describes the files on disk, merging
  between machines, and how a capture records link problems.

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

## Recording captures

Add `--save` to a read, with context flags:

```bash
canair read MyECU:2101 --save --label "highway" --state driving --notes "…"
```

`--save` works with `read`, `monitor`, `scan`, `discover`, `raw`, `dtc` and `action run`.

!!! note "Truncated reads are rejected, not stored"
    A multi-frame ISO-TP response is self-describing — its first frame declares
    the total length. If a consecutive frame is dropped (the ELM327 terminal
    occasionally returns a short read), the reassembled payload falls short of
    that declared length and every byte after the gap is misaligned. canair
    rejects such reads (`truncated ISO-TP: got N bytes, declared M`) so a
    corrupt payload is never saved. The check is generic — no per-PID length
    table — and needs no configuration.

## Recording in the live monitor

`canair monitor … --save` records continuously: every poll cycle is
journaled as it arrives and written to a capture file automatically when you
quit. Every poll is kept (`--keep-all`, the default), so the stored rows show the real
polling cadence; repeats cost little space because they are
[folded on disk](capture-format.md#repeated-polls-are-folded-on-disk). `--keep-changes` instead stores only
value-transitions per PID (run-length), and the session records `keep_mode: changes` so the
analysis tools know to [carry each value forward](analysis-techniques.md#forward-fill-a-run-length-row-is-a-segment-not-a-sample). The live view
collapses each run of identical payloads into one row with its poll count (`×37`) and shows
the newest few (`--keep N` sets how many). The scrollable live view shows a blinking `● REC`
whenever a `--save` recording is active, and these keys control the session:

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
[`state_spans` timeline](vehicle-states.md#simultaneous-vs-sequential-overlap-state_spans) so each capture
still answers for the state it was recorded in.

The **status bar** shows what the car is doing *now*, so its inputs expire: a decoded value
stops feeding the state predicates a few poll cycles after it was last read, and the signal
returns to "unknown". Otherwise a parked car kept reading `DRIVING` from the last speed
sample. Expiry counts **poll cycles, not seconds**, so it scales with how many ECUs you poll.

## Journaling — you won't lose data

Saves are **journaled**: written to a write-ahead log under `captures/.journal/`
as they stream, and reconciled into the dated capture file when the session
exits. A killed, crashed, or disconnected session is therefore never lost:

```bash
canair captures uds --recover      # reconcile orphaned journals into capture files
canair captures uds --recover --discard   # or drop them unsaved
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
`--state` token / `--label` substring — `--state driving` is the natural unit of drive
analysis.
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

## Sensitive PIDs and signals

Some readings identify the car or its owner the way a VIN does. The Ioniq's CCM
`22F102`, for example, is the MAC address the car sends a CCS charger as its
EVCCID, and chargers that offer Autocharge bill against it. Mark such a PID, or
just one of its signals, as sensitive:

```bash
canair pids set-sensitive CCM 22F102 true                 # the whole reply
canair pids set-sensitive ECU PID true --param SIGNAL     # one signal
```

What a sensitive reading gets:

- **It is masked on screen.** Every read view (`read`, `monitor`, `raw`,
  `identity`, `scan`) shows `*****` instead of the value. A sensitive PID hides
  its whole raw reply. In `read` and `monitor`, a sensitive signal hides only the
  bytes it reads, each shown as `**`, so the rest of the reply stays readable. If
  the reply is too short to hold those bytes, the whole reply is hidden. The other
  views hide the whole reply. To see it once, run
  `canair read CCM:22F102 --reveal`. This only changes the display and can't be
  combined with `--save`.
- **It is never saved.** A `--save` session leaves the reply out of the journal
  and out of `captures/`, and a `⚠ Not saved` line says so. A range scan still
  records the hit, but not its bytes.
- **It is never shared.** `contribute` strips it from the copy it stages, and so
  do the blind-test strip and the docs-site explorer. The WiCAN AutoPID export
  never ships the signal, and `validate pids` rejects a `send_to:` on it.

A PID you mark sensitive after recording it still has history on disk. Every
analysis leaves that history out (with a one-line note), and `validate captures`
reports it as an error. Remove it with:

```bash
canair captures purge-sensitive --dry-run   # preview
canair captures purge-sensitive             # confirm, then rewrite
```

If those files were already committed or pushed, the data stays in git history
until the history is rewritten.

A text row someone already redacted by hand is not a reply, so none of this
applies to it. For now only one exact mask counts as redacted: the Ioniq VIN
placeholder `KMHCXXXXXXXXXXXXX` (`REDACTED_VALUES` in `src/canair/privacy/sensitive.py`). A row
that also stores payload bytes is still sensitive.

