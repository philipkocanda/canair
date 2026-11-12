# Captures & states

Captures are the raw evidence of your reverse engineering. This page explains the capture model.
It also shows how to record, review and remove captures, and how canair keeps your data safe.

Two related pages go deeper:

- [Vehicle states](vehicle-states.md) explains how a *vehicle state* (such as `driving` or
  `charging`) makes captures more useful.
- [Capture file format and data quality](capture-format.md) describes the files on disk, merging
  between machines, and how a capture records link problems.

## What a capture is

A capture is a recorded response payload from an electronic control unit (ECU). It carries
context: when canair recorded it, which ECU and parameter identifier (PID), and what the car was
doing. Captures live under `captures/`, split by date (for example `captures/2026-04-19.json`).

!!! warning "Never hand-edit capture files"
    The tool writes capture files (`--save`). canair's own helpers edit and remove them.
    Hand edits corrupt the record. Add data with `canair … --save`. Review it with
    `canair captures`.

!!! tip "Every save says where it landed"
    Any command that writes captures prints the **full path** of the file it wrote:
    `→ Saved 12 capture(s) to /…/profiles/<car>/captures/2026-04-19.json`.
    This tells you which profile got the data. In `canair monitor`, the text user interface
    (TUI) owns the screen. A save made while the monitor runs (`s` without `--save`, or an `n`
    segment rotate) reports its path after the monitor exits. The terminal is yours again then.

## Recording captures

Add `--save` to a read, with context flags:

```bash
canair read MyECU:2101 --save --label "highway" --state driving --notes "…"
```

`--save` works with `read`, `monitor`, `scan`, `discover`, `raw`, `dtc` and `action run`.

!!! note "Truncated reads are rejected, not stored"
    A multi-frame ISO-TP (ISO 15765-2 transport protocol) response describes itself. Its first
    frame declares the total length. If a consecutive frame drops (the ELM327 terminal
    occasionally returns a short read), the reassembled payload is shorter than the declared
    length. Every byte after the gap is then misaligned. canair rejects such reads
    (`truncated ISO-TP: got N bytes, declared M`), so it never saves a corrupt payload. The
    check is generic. It uses no per-PID length table and needs no configuration.

## Recording in the live monitor

`canair monitor … --save` records continuously. canair journals every poll cycle as it arrives.
It writes a capture file automatically when you quit.

canair keeps every poll (`--keep-all`, the default), so the stored rows show the real polling
cadence. Repeats cost little space because canair
[folds them on disk](capture-format.md#repeated-polls-are-folded-on-disk).

`--keep-changes` stores only the value transitions per PID (run-length). The session then records
`keep_mode: changes`, so the analysis tools know to
[carry each value forward](analysis-techniques.md#forward-fill-a-run-length-row-is-a-segment-not-a-sample).

The live view collapses each run of identical payloads into one row with its poll count (`×37`).
It shows the newest few rows (`--keep N` sets how many). The scrollable live view shows a
blinking `● REC` whenever a recording is active. These keys control the session:

- **`s` (edit current session)** — Change the label, state and notes of the **current**
  recording. The dialog opens with the session's existing label and notes, and it says that it
  does not start a new session. The new values apply to everything in the session, before and
  after the keypress, and replace the old ones. canair already journaled everything, so this
  does not write a separate file. Press `n` to start a new session instead.

    The state field is free text (comma-separated). canair pre-fills it with the **union of every
    state auto-suggested so far in the segment**. This is not only the state that is active when
    you press `s`, so a top-up that ended a minute ago is still offered. If you accept the
    pre-fill unchanged (or leave it blank), the state stays automatic, so states seen after the
    keypress still count. If you type a different state, canair pins it.

    While nothing records (no `--save`, and no recording started with `n`), `s` is a **one-off
    snapshot**: the dialog says so. canair writes the payloads captured since the previous
    snapshot (or since the monitor started) to a new capture file, once. Nothing is recorded
    after that, and anything not snapshotted is lost when you quit. canair pre-fills the state
    with the union of states seen over that span.

- **`n` (finish & start new)** — Write the current session to its own capture file **now**, and
  start a **fresh** recording. You label it through the same modal. canair pre-fills the state
  that is active now, and the state stays automatic unless you change it. Press `n` at each
  phase change (parked → driving → charging) to get several independently-labelled sessions from
  one run.

    Without `--save`, `n` is **start recording**. The recording begins at the keypress and holds
    only the polls after it; what the monitor captured earlier is not in it (snapshot it first
    with `s`). canair journals it like a `--save` recording and writes it when you quit, so
    nothing is written if you never press `n`. After that, `s` and `n` act as they do with
    `--save`.
- **`i` (session info)** — A read-only overlay. It shows these items:
    - the label, state and notes of the current segment
    - the run-level counters (frames, cycles, poll interval and schedule, transport, elapsed
      time)
    - the exact `.journal/*.jsonl` path that `--recover` would read
    - the `--save` segments that already finished in this run

    The header bar always shows the current segment name.

- **`V` (view mode)** — Cycle how much the live view shows:
    - `ecus`: responding ECUs and a PID/signal count
    - `ranges`: each signal's captured min–max or distinct labels
    - `signals`: decoded values only
    - `full`: signals plus raw payload bytes (the default)

- **`r` (byte ruler)** — Number the byte columns of the payload above the hex. Show the byte
  reference of each signal next to its value. Both use your preferred notation (see
  [Byte indexing](byte-indexing.md)).

The view lists signals in **payload order** (by the first byte each reads), so a value sits
directly above the hex byte it came from. The view never follows the tail. A repaint leaves your
scroll position alone, and `G`/`End` jumps to the newest output.

When a `--save` segment ends without an explicit state, canair back-fills it with the **union of
every state auto-suggested across the whole segment**. This is not only the state that is active
when the segment closed. A segment that charged and then went idle therefore still reconciles as
`charging`. If the car changed state *during* the segment, the session also carries a
[`state_spans` timeline](vehicle-states.md#simultaneous-vs-sequential-overlap-state_spans). Each
capture then still answers for the state it was recorded in.

The **status bar** shows what the car is doing *now*, so its inputs expire. A decoded value stops
feeding the state predicates a few poll cycles after canair last read it, and the signal returns
to "unknown". Without expiry, a parked car would keep reading `DRIVING` from the last speed
sample. Expiry counts **poll cycles, not seconds**, so it scales with how many ECUs you poll.

## Journaling — you won't lose data

Saves are **journaled**. canair writes them to a write-ahead log under `captures/.journal/` as
they stream. It reconciles them into the dated capture file when the session exits. A killed,
crashed or disconnected session is therefore never lost:

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

Scope any of these by date (`--since`/`--until`/`--date`). You can also scope by `--state` token
or `--label` substring. `--state driving` is the natural unit of drive analysis.

## Removing captures

Delete captures with the same query mini-language that you use to view them. Scope the delete
with the usual `--since`/`--state`/`--label` filters. Preview with `--dry-run`. The delete asks
for confirmation unless you pass `--yes`:

```bash
canair captures uds OBC 2101 --delete --dry-run   # preview what would go
canair captures uds OBC 2101 --delete             # confirm, then delete
canair captures uds OBC 2101 --delete --yes        # non-interactive
```

`--delete` refuses to run without a QUERY, so it never deletes everything. It addresses captures
through canair's own helpers, so the record stays consistent. Never hand-edit files to remove
data.

On a [layered profile](profiles.md#layering-your-captures-over-someone-elses-definitions), the
base layer's captures are **read-only**. `--delete`, the `--set-*` editors and
`--backfill-states` refuse to run when a matched row belongs to the base. They name the file.
`--dry-run` still previews it. You can edit rows in your own layer freely.

## Sensitive PIDs and signals

Some readings identify the car or its owner, as a vehicle identification number (VIN) does. For
example, the Ioniq's CCM `22F102` is the media access control (MAC) address that the car sends to
a Combined Charging System (CCS) charger as its EVCCID (electric vehicle communication controller
ID). Chargers that offer Autocharge bill against it. Mark such a PID, or only one of its signals,
as sensitive:

```bash
canair pids set-sensitive CCM 22F102 true                 # the whole reply
canair pids set-sensitive ECU PID true --param SIGNAL     # one signal
```

A sensitive reading gets this treatment:

- **It is masked on screen.** Every read view (`read`, `monitor`, `raw`, `identity`, `scan`)
  shows `*****` instead of the value. A sensitive PID hides its whole raw reply.

    In `read` and `monitor`, a sensitive signal hides only the bytes it reads, and each byte
    shows as `**`. The rest of the reply stays readable. If the reply is too short to hold those
    bytes, canair hides the whole reply. The other views hide the whole reply.

    To see it once, run `canair read CCM:22F102 --reveal`. This changes only the display. You
    cannot combine it with `--save`.

- **It is never saved.** A `--save` session leaves the reply out of the journal and out of
  `captures/`. A `⚠ Not saved` line says so. A range scan still records the hit, but not its
  bytes.
- **It is never shared.** `contribute` removes it from the copy that it stages. The blind-test
  strip and the docs-site explorer do the same. The WiCAN AutoPID export never ships the signal.
  `validate pids` rejects a `send_to:` on it.

A PID that you mark sensitive after you record it still has history on disk. Every analysis
leaves that history out (with a one-line note). `validate captures` reports it as an error.
Remove it with:

```bash
canair captures purge-sensitive --dry-run   # preview
canair captures purge-sensitive             # confirm, then rewrite
```

If you already committed or pushed those files, the data stays in git history until someone
rewrites the history.

A text row that someone already redacted by hand is not a reply, so none of this applies to it.
For now, only one exact mask counts as redacted: the Ioniq VIN placeholder `KMHCXXXXXXXXXXXXX`
(`REDACTED_VALUES` in `src/canair/privacy/sensitive.py`). A row that also stores payload bytes is
still sensitive.
