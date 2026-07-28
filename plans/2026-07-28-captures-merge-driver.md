# A git merge driver for append-only capture files

## The problem (a real conflict we hit)

Dated diagnostic capture files (`profiles/*/captures/YYYY-MM-DD.json`) are an
**append-only log**: `{"sessions": [ … ]}` where each session is a
self-contained block. Once written, a session is never rewritten —
`canlib.captures.save_session` only ever `.append()`s a new one (edits/removals
go through `--delete`).

Two machines that record on the same day therefore each **append a different
session to the tail of the same file**. Git's line-based 3-way merge can't
reconcile that: every session ends with identical boilerplate (`}` / `]` / `}`),
so the diff *misaligns* and splits the conflict **inside individual capture
records** — pairing one side's `ecu`/`pid` with the other side's `time`. Resolving
by hand is error-prone (you can silently corrupt a record) even though the
underlying operation is trivial: a union of two disjoint additions to a list.

A concrete instance: on `2026-07-28`, one machine had appended two sessions
(`OBC…`, `AAF…` at 12:18/12:20) while the repo copy had four short `21F2 clean
baseline` sessions (11:59). Six earlier sessions were shared byte-for-byte. The
correct resolution is the **union** of all twelve — but git produced six
interleaved conflict hunks split mid-record.

## Why a merge driver (vs. the alternatives)

Options considered:

1. **One file per session** (`captures/2026-07-28/HHMMSS-label.json`). Removes
   the conflict *by construction* — disjoint appends become disjoint new files.
   Cleanest long-term, but a layout migration touching every reader and all
   committed capture files.
2. **JSONL + `.gitattributes merge=union`**. Cheap, but `merge=union` on the
   single `{"sessions":[…]}` blob yields *invalid JSON*; it only works for
   line-oriented formats, so it forces a format change to JSONL and loses the
   pretty, reviewable diff.
3. **A custom merge driver** (this plan). Zero format change, zero migration,
   keeps all existing tooling and the readable pretty-JSON. Costs a one-time
   per-clone `git config` registration and a small amount of driver code.

We chose (3): it's the **lowest-blast-radius** fix and directly matches the data
model (disjoint list appends). It **fails safe** — a clone that hasn't
registered the driver just falls back to normal conflict markers, exactly like
today, so nothing breaks; you simply don't get auto-resolution until you run the
one-time install.

## How git merge drivers work (the parts that bite)

Three pieces:

- **`.gitattributes`** (committed) names *which files* use a driver:
  `profiles/*/captures/*.json merge=canair-captures`.
- **`.git/config`** (per-clone, **not** committed) maps that name to a command.
  Git deliberately refuses to read a driver *command* from a tracked file —
  otherwise cloning a repo could execute arbitrary commands. So **every clone
  must register the driver once**.
- **The driver command** is invoked as `… %O %A %B %P` (base / ours / theirs /
  pathname). It must write the merged result into `%A` and exit `0`, or exit
  non-zero to fall back to normal markers.

The per-clone registration is the only real cost, mitigated by a first-class
`canair captures merge-driver --install` and graceful fallback when absent.

## Design

- **`canlib/captures_merge.py`** — the pure union, no I/O. `merge_sessions(base,
  ours, theirs)` performs a standard 3-way merge keyed by **exact session
  content** (`json.dumps(sort_keys=True)`):
  - present in base and still on both sides → keep once (shared history
    collapses);
  - absent from base, present on one side → **addition**, keep;
  - in base, removed on one side and unchanged on the other → **deletion**,
    honoured (dropped);
  - a base session gone from **both** sides (each replaced differently) → a
    genuine divergent edit → `MergeConflict` (git shows markers).

  Content-keying is exactly right for an append-only log: the writer never
  mutates a session, so equal content = the same recording, any difference = a
  distinct one. Output is sorted by `(date, first-capture-time, label)`, which
  keeps the file chronological **and** makes the merge order-independent
  (`merge(A,B) == merge(B,A)`) so results are stable/re-mergeable.

- **`canlib/commands/captures_merge_driver.py`** — the `canair captures
  merge-driver` subcommand (a new kind of the `captures` group):
  - `merge-driver %O %A %B %P` runs the union, writing through the shared
    `capture_io.dump_capture_file` seam so the merged file is **byte-identical**
    to what `--save` writes (same indent/order/trailing newline);
  - `merge-driver --install` writes the `[merge "canair-captures"]` stanza into
    the local `.git/config` (idempotent; `--json` for scripting).
  - Kept in its own module rather than growing the already-oversized
    `commands/captures.py` (>1200 lines).

- **`.gitattributes`** — one rule routing `profiles/*/captures/*.json` to the
  driver, placed after the LFS block and before the `tests/fixtures/**`
  un-tracking rule (last-match-wins preserved). Disjoint from the existing
  `captures/can/**` LFS rule.

## Failure & fallback behaviour

- Driver **not registered** → git falls back to normal 3-way merge (markers).
- Unparseable input, or a genuine divergent edit (`MergeConflict`) → exit
  non-zero → git writes markers and a human decides. We never guess on a real
  clash (per the "fall back to markers" decision).

## Tests

`tests/test_captures_merge.py`:

- pure union: disjoint appends, shared-history de-dup, one-sided deletion,
  empty-base two-new-files, the divergent-edit conflict guard, determinism;
- the driver command: writes the union + exit 0, conflict → non-zero + `%A`
  left untouched, unreadable input → non-zero, byte-identical-to-`--save`
  output;
- **end-to-end**: a real `git merge` in a temp repo with the driver registered
  (two branches append different same-day sessions → clean union), plus a
  control proving the merge **conflicts without** the driver (so the value is
  demonstrated, not assumed).

Also validated against the real `2026-07-28` conflict: reconstructing the two
divergent sides and running the driver reproduces the correct 12-session union
byte-for-byte.

## Out of scope / future

- This handles the **same-day append** conflict, the common case. It does not
  attempt to auto-merge genuine concurrent *edits* of the same session (by
  design — those fall back to markers).
- The per-session-file layout (option 1) remains a possible future
  simplification if the file-count-per-day stays low; the driver does not
  preclude it.
