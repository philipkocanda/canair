# Capture file format and data quality

This page is for contributors and for anyone who needs to know what is inside a capture file. It
describes the file format, how canair merges capture files from several machines, and how a capture
records the transport and the quality of its data. To record, review or remove captures, see
[Captures & states](captures-and-states.md).

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
- **`variant`** (optional) names which of the profile's [variants](variants.md) the recorded
  car is (`"28kwh"`), and **`attribution`** how that was decided:
  `{"variant": "selected" | "alias" | "only" | "migrated" | "asserted"}`. A session recorded
  without the global `--variant` flag is untagged. See [Tagging captures with the
  car](variants.md#tagging-captures-with-the-car).
- Stored as JSON because it parses ~60× faster than YAML — the dominant cost of
  every history-consuming command (`ecu`, `coverage`, `decode`, `correlate`,
  `hunt`, `investigate`).

### Repeated polls are folded on disk

A long recording polls a stationary signal over and over and gets the same payload back each
time. On disk, a poll that repeats the previous payload of the same ECU and PID is not written
as a row of its own. It is folded into the run's first capture as a gap in milliseconds since
the previous poll of the run, and each capture sits on one line (so does each
[`state_spans`](vehicle-states.md#simultaneous-vs-sequential-overlap-state_spans) span):

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
deprecated fields) is `src/canair/schema/captures_schema.json`; `canair validate
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
  (`5.2.0+main.343b244`, plus `.dirty` for uncommitted edits). Older sessions omit it.
- **`transport`** — how the payloads were acquired: the transport label
  (`slcan-tcp` / `wican-ws` / `elm327-tcp`) for a device-recorded session, or `import` for a
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

### Gaps: `link_events`

A `monitor --save` session that loses its link keeps recording across the gap (the
monitor reconnects onto the same recording), so its timestamps show a hole. The
session's `link_events` list says what caused it, and also marks the stretches in which
one ECU or one PID was not being read:

```json
"link_events": [
  {"at": "14:02:11.400", "until": "14:03:20.900", "kind": "disconnect", "cause": "stale"},
  {"at": "14:10:05.000", "until": "14:10:41.200", "kind": "ecu_silent", "rx": "0x7EC"},
  {"at": "14:12:00.000", "until": "14:14:00.000", "kind": "backoff", "rx": "0x7EA", "pid": "22B002", "factor": 4},
  {"at": "14:15:00.020", "kind": "resync", "count": 2}
]
```

These are **gaps**: nothing was gathered for their scope, so no stored value is known to
have held across one.

- **`disconnect`** — no ECU answered coherently from `at` (the last answer before the
  drop) until `until` (the first answer after it). `cause` is `error` (the connection
  failed), `stale` (several poll cycles with no coherent answer) or `manual` (you asked
  the monitor to reconnect).
- **`paused`** — polling was paused (the space key, or a dialog that stops polling while
  it is open).
- **`ecu_silent`** — one ECU (`rx`, its response address) stopped answering for three
  polls in a row while the others still answered: usually the ECU going to sleep. It
  runs from that ECU's last answer to its next one.
- **`unpolled`** — a PID (`rx` + `pid`), or a whole ECU (`rx` alone), was taken out of
  the poll set with the monitor's `p` picker, until it was put back.

These are not gaps, but explain sparse data:

- **`backoff`** — a slow PID (`rx` + `pid`) was polled `factor` times less often (one
  event per factor). Sessions recorded before the backoff worked per PID carry `rx`
  alone: the whole ECU was slowed.
- **`resync`** — the transport had to realign a desynchronised ELM327 pipe at that
  moment (`count` when it happened several times in one poll).

An interval with no `until` lasted until the recording ended. Events are recorded live
and never derived from payloads, so they are absent on older sessions and on sessions
that had no trouble. They hold no free text, so no device address ends up in a shared
capture file.

Where they show up:

- `captures uds --sessions` adds a `gaps:` line per affected session (and `--json`
  carries the list). For a session that keeps every poll, it also adds an
  **inferred gaps** line for long holes in the timestamps that no event explains —
  typically an older recording. Inferred gaps are a guess, so they are only shown, never
  stored.
- `align`/`correlate`/`hunt`/`investigate` never carry a `keep:changes` value forward
  into a gap that covers its ECU and PID, and the fill note says how many values a gap
  stopped.
- `captures uds --step` says, in the frame header, which gaps fell between the previous
  frame and this one.
- The [profile explorer](../profiles/explorer.md) shades disconnects and pauses on its
  plots and never draws a line across one.

`validate captures` warns about a payload timed inside a closed disconnect, pause or
silence of its ECU: nothing answered then, so a clock or the event is wrong.

### What the recording switched: `actuations`

A `monitor --save --actuate` session drives outputs and runs routines from the actuator pane
([Safety](safety.md#actuating-from-the-monitor)). Its `actuations` list records each request
at the time it was sent, so a toggle can be lined up with the signal changes it caused:

```json
"actuations": [
  {"at": "14:02:11.400", "rx": "0x778", "kind": "iocontrol", "id": "BC0C", "action": "on", "request": "2FBC0C03", "outcome": "ok", "response": "6FBC0C03"},
  {"at": "14:05:02.010", "rx": "0x7A8", "kind": "routine", "id": "12A1", "action": "start", "request": "310112A1", "outcome": "nrc", "nrc": "0x22"}
]
```

- **`kind`** is `iocontrol` (an output, `id` its DID) or `routine` (`id` its RID). `rx` is the
  ECU's response address, as in a capture.
- **`action`** is `on`, `off`, `adjust` (a value), `release` (handed back on exit or after a
  reconnect), `start`, `stop` or `results`. `held` sends nothing: a new segment (`n`) starts
  with one for each output that was still on.
- **`outcome`** is `ok`, `nrc` (with the code) or `error` (no reply).

Like `link_events`, the list is recorded live, never derived, and holds no labels or free
text. It is absent on a session that actuated nothing.

Where they show up:

- `captures uds --sessions` adds an `actuated:` line per session (and `--json` carries the
  list).
- `captures uds --step` says, in the frame header, what was switched between the previous
  frame and this one, whatever ECU the frame shows (`--json` adds `actuations_before`).
- `investigate ECU PID --events` prints each actuation of the sessions in scope among the
  edges, in time order (`--json` adds `actuations`), so a bit that flips right after a toggle
  reads against it.


