# Capture file format and data quality

This page is for contributors and for anyone who needs to know what is inside a capture file. It
describes the file format. It also describes how canair merges capture files from several
machines, and how a capture records the transport and the quality of its data. To record, review
or remove captures, see [Captures & states](captures-and-states.md).

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

- **`rx`** is the Controller Area Network (CAN) **response** address as a hex string. It is the
  `rx_id` of the electronic control unit (ECU). If the ECU has no `rx_id`, it is the request TX
  address plus the `addressing.rx_offset` of the profile (`+0x08` by default). The tools map it
  back to the short name of the ECU via the [`ecus/`](profiles.md) registry of the profile, so you
  still query by name. Multi-ECU discovery scans use `"broadcast"`. Each entry in
  `scan_results.responding` carries its own `tx` and `rx`. Older files used the key `ecu`. Readers
  still accept it, and `canair captures migrate-rx` renames it.
- **`pid`** / **`payload`** are the request DID (data identifier) and the reassembled response of
  Unified Diagnostic Services (UDS). The payload has the service identifier (SID) first, and
  canair removed the ISO-TP (ISO 15765-2 transport protocol) framing. canair does **not** store
  decoded *values*. It regenerates them on demand from `payload` and the PID definitions, so a
  refined expression re-decodes old captures at no cost.
- **`elapsed_ms`** (optional) is the wall-clock UDS round-trip in milliseconds. It includes the
  transport, WiCAN and any `responsePending` waits. Treat it as a *relative* speed signal. Compare
  it only within the same session `transport`. canair records it only for single per-DID reads on
  the live `read` path. It is not recorded for batched multi-DID reads, `monitor --save`, scans or
  imports.
- **`variant`** (optional) names which of the profile's [variants](variants.md) the recorded car
  is (`"28kwh"`). **`attribution`** says how canair decided that:
  `{"variant": "selected" | "alias" | "only" | "migrated" | "asserted"}`. A session recorded
  without the global `--variant` flag is untagged. See [Tagging captures with the
  car](variants.md#tagging-captures-with-the-car).
- canair stores captures as JSON because JSON parses about 60 times faster than YAML. Parsing is
  the dominant cost of every command that consumes history (`ecu`, `coverage`, `decode`,
  `correlate`, `hunt`, `investigate`).

### Repeated polls are folded on disk

A long recording polls a stationary signal over and over and gets the same payload back each
time. On disk, canair does not write a poll that repeats the previous payload of the same ECU and
PID as a row of its own. It folds the poll into the first capture of the run, as a gap in
milliseconds since the previous poll of the run. Each capture sits on one line. So does each
[`state_spans`](vehicle-states.md#simultaneous-vs-sequential-overlap-state_spans) span:

```json
      "captures": [
        {"rx":"0x7BB","pid":"220100","payload":"62010…","time":"00:00:36.314","repeat_ms":[38822,45093,17789]},
        {"rx":"0x7A8","pid":"22B001","payload":"62B00…","time":"00:00:36.402"}
      ]
```

The first row stands for four polls: 00:00:36.314, then 38.822 s later, then 45.093 s after that,
and so on. canair slots `repeat_ms` repeats back among the rows of the other PIDs by their time.
`repeat_inline_ms` repeats (in a session stored grouped by PID) follow their row directly.

Folding is **lossless**. Every command loads the file with each repeat expanded back into its own
capture, in the original order, so nothing downstream knows about it. canair folds only what it
can restore exactly. A capture with `elapsed_ms` or a note therefore stays a row of its own. So
does a capture with an untimed or non-standard `time`.

Every write uses this form. `canair captures compact` rewrites the files that nothing has touched
since (the bundled Ioniq's largest day went from 9.0 MB to 2.0 MB). Read the files through canair.
A plain JSON parser sees the folded form.

The **authoritative, machine-checked** schema (all fields, `scan_results`, deprecated fields) is
`src/canair/schema/captures_schema.json`. `canair validate captures` checks every file against it.
Run `canair captures migrate` once to convert a profile that you created before the JSON cutover.

## Sharing captures across machines (the merge driver)

Capture files are **append-only session logs**. New sessions go on the end. Only the editors and
repairs below rewrite a session in place, and they never change what identifies it. So when two
machines record on the same day, each appends a *different* session to the same
`captures/YYYY-MM-DD.json`.

The line-based merge of git cannot reconcile that. Every session ends with the same `}`/`]`/`}`,
so the conflict lands *inside* records. The data is still a trivially mergeable list.

canair ships a git **merge driver** that unions the session lists of the two sides:

- Shared history collapses.
- Disjoint appends stay.
- An edit on one side beats the untouched other side.
- A genuinely divergent edit falls back to normal conflict markers.

`.gitattributes` wires up the driver for `profiles/*/captures/*.json`. Git does not run a driver
command from a tracked file, so **each clone must register it once**:

```bash
canair captures merge-driver --install
```

Until then, merges fall back to conflict markers. Nothing breaks, but you lose the auto-union.

**How canair identifies a session.** A session has no id. Two copies are the same when their
content is identical. They are also the same *recording, edited* when they share the date, the
label, the recording version, and the time, `rx` and PID of the first capture. No editor or repair
rewrites these fields.

The second rule lets an edited session replace its original instead of counting twice. It applies
in a merge, in `canair contribute`, and across the base and your layer of a layered profile.
Deleting the first capture of a session changes those fields, so canair reads the trimmed session
as a different recording.

## Provenance: transport & data quality

Each recorded session also carries **where its data came from** and **how clean the connection
was**. This provenance helps you judge how much to trust the session. Multi-frame ISO-TP payloads
recorded over a flaky link are the reason that historical captures became suspect.

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

- **`version`** — The canair version that recorded the session. Use it to trace a capture issue
  to a release. A session recorded from a git checkout also carries the branch and short commit
  (`5.2.0+main.343b244`, plus `.dirty` for uncommitted edits). Older sessions omit it.
- **`transport`** — How canair acquired the payloads. For a device-recorded session, it is the
  transport label (`slcan-tcp` / `wican-ws` / `elm327-tcp`). For a device-free
  `canair import uds`, it is `import`.
- **`quality`** — The exchange and error tally of the transport for the session. `exchanges` is
  the total number of UDS round-trips. canair adds any **non-zero** error categories:

    - `drop` / `stale`: dropped or mis-assembled ISO-TP frames
    - `no_data`: timeouts
    - `bus`, `decode`

    A clean session records just `exchanges`. canair does **not** count negative response codes
    (NRC), because they are legitimate ECU answers. A non-zero `resyncs` counts the times the
    transport had to realign a desynchronised ELM327 pipe. A resync is a recovery, not a lost
    payload. Several resyncs mark a marginal link.

`canair captures uds --sessions` shows all three per session (flagging any drops). canair also
writes every error to the central log. Inspect it with `canair logs`.

### Gaps: `link_events`

A `monitor --save` session that loses its link keeps recording across the gap. The monitor
reconnects onto the same recording, so the timestamps show a hole. The `link_events` list of the
session says what caused the hole. It also marks the stretches in which canair did not read one
ECU or one PID:

```json
"link_events": [
  {"at": "14:02:11.400", "until": "14:03:20.900", "kind": "disconnect", "cause": "stale"},
  {"at": "14:10:05.000", "until": "14:10:41.200", "kind": "ecu_silent", "rx": "0x7EC"},
  {"at": "14:12:00.000", "until": "14:14:00.000", "kind": "backoff", "rx": "0x7EA", "pid": "22B002", "factor": 4},
  {"at": "14:15:00.020", "kind": "resync", "count": 2}
]
```

These are **gaps**. Nothing was gathered for their scope, so no stored value is known to have held
across one.

- **`disconnect`** — No ECU answered coherently from `at` (the last answer before the drop) until
  `until` (the first answer after it). `cause` is `error` (the connection failed), `stale`
  (several poll cycles with no coherent answer) or `manual` (you asked the monitor to reconnect).
- **`paused`** — Polling was paused (the space key, or a dialog that stops polling while it is
  open).
- **`ecu_silent`** — One ECU (`rx`, its response address) stopped answering for three polls in a
  row while the others still answered. This is usually the ECU going to sleep. The gap runs from
  the last answer of that ECU to its next one.
- **`unpolled`** — You took a PID (`rx` + `pid`), or a whole ECU (`rx` alone), out of the poll set
  with the `p` picker of the monitor. The gap lasts until you put it back.

These are not gaps, but they explain sparse data:

- **`backoff`** — canair polled a slow PID (`rx` + `pid`) `factor` times less often (one event per
  factor). Sessions recorded before backoff worked per PID carry `rx` alone. canair slowed the
  whole ECU then.
- **`resync`** — The transport had to realign a desynchronised ELM327 pipe at that moment (`count`
  when it happened several times in one poll).

An interval with no `until` lasted until the recording ended. canair records events live and never
derives them from payloads. They are therefore absent on older sessions and on sessions that had
no trouble. They hold no free text, so no device address ends up in a shared capture file.

Where they show up:

- `captures uds --sessions` adds a `gaps:` line per affected session (and `--json` carries the
  list). For a session that keeps every poll, it also adds an **inferred gaps** line. This line
  shows long holes in the timestamps that no event explains, typically in an older recording.
  Inferred gaps are a guess, so canair only shows them and never stores them.
- `align`/`correlate`/`hunt`/`investigate` never carry a `keep:changes` value forward into a gap
  that covers its ECU and PID. The fill note says how many values a gap stopped.
- `captures uds --step` says in the frame header which gaps fell between the previous frame and
  this one.
- The [profile explorer](../profiles/explorer.md) shades disconnects and pauses on its plots. It
  never draws a line across one.

`validate captures` warns about a payload timed inside a closed disconnect, pause or silence of its
ECU. Nothing answered then, so either a clock or the event is wrong.

### What the recording switched: `actuations`

A `monitor --save --actuate` session drives outputs and runs routines from the actuator pane
([Safety](safety.md#actuating-from-the-monitor)). Its `actuations` list records each request at
the time canair sent it. You can then line up a toggle with the signal changes it caused:

```json
"actuations": [
  {"at": "14:02:11.400", "rx": "0x778", "kind": "iocontrol", "id": "BC0C", "action": "on", "request": "2FBC0C03", "outcome": "ok", "response": "6FBC0C03"},
  {"at": "14:05:02.010", "rx": "0x7A8", "kind": "routine", "id": "12A1", "action": "start", "request": "310112A1", "outcome": "nrc", "nrc": "0x22"}
]
```

- **`kind`** is `iocontrol` (an output, `id` is its DID) or `routine` (`id` is its routine
  identifier, RID). `rx` is the response address of the ECU, as in a capture.
- **`action`** is `on`, `off`, `adjust` (a value), `release` (handed back on exit or after a
  reconnect), `start`, `stop` or `results`. `held` sends nothing. A new segment (`n`) starts with
  one `held` entry for each output that was still on.
- **`outcome`** is `ok`, `nrc` (with the code) or `error` (no reply).

Like `link_events`, canair records the list live and never derives it. It holds no labels or free
text. It is absent on a session that actuated nothing.

Where they show up:

- `captures uds --sessions` adds an `actuated:` line per session (and `--json` carries the list).
- `captures uds --step` says in the frame header what was switched between the previous frame and
  this one, whatever ECU the frame shows (`--json` adds `actuations_before`).
- `investigate ECU PID --events` prints each actuation of the sessions in scope among the edges, in
  time order (`--json` adds `actuations`). A bit that flips right after a toggle then reads against
  it.
