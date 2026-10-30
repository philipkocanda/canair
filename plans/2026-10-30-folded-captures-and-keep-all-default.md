# Folded capture files, and keep-all as the recording default

Status: **DONE** (2026-10-30).

## Problem

`monitor --save --keep-all` records every poll, and a stationary signal answers the same payload
poll after poll. The bundled Ioniq's 2026-10-25 charge log held 48,696 rows, 37,060 of them (76%)
identical to the previous row of their PID except for `time`, and the file was 9.0 MB, a third of
it `indent=2` whitespace. That cost is why `--keep-changes` (run-length) was the default, at the
price of rows that measure volatility rather than sampling and joins that need forward fill.

## Design

**Fold on disk, expand on load, at the one format seam** (`canlib/captures/io.py`). Nothing above
it changes: every reader still sees one row per poll.

- A poll identical to the open run's first row of the same `(rx, pid)` in every field and field
  order except `time` (canonical `HH:MM:SS[.mmm]`, same precision) becomes one entry of that row's
  gap list, the milliseconds since the run's previous poll (`canlib/captures/folding.py`).
- **Order is preserved exactly**, which rules out "sort by time": real sessions are not time-ordered
  (reconnects, merged journals) and session identity is content, so a reorder would read as an
  edited session to the merge driver and `contribute`. Two encodings cover the two session shapes:
  - `repeat_ms` — repeats interleaved with other PIDs (journaled recordings). Expansion walks the
    stored rows in order and merges repeats back in by `(time, stored position, repeat number)`.
  - `repeat_inline_ms` — repeats that directly follow their row (sessions stored grouped by PID).
- The folder simulates the expansion and folds only rows it is guaranteed to restore; a fold that
  would not round-trip is never produced, and a final round-trip check stores the session unfolded
  if it ever did. Equal-timestamp cycles (legacy cycle-stamped sessions) fold poorly; accepted.
- **One capture per line**, the session metadata still `indent=2`, so diffs stay line-local.
- `canair captures compact` rewrites existing files; every write applies the form anyway.

Measured: the 2026-10-25 file 9.0 → 2.0 MB; the whole Ioniq store 32.8 → 16.9 MB (much of it was
already run-length). Loading the whole store costs ~50 ms more.

## Keep-all default

With the storage cost gone, `monitor` records every poll by default (`KEEP_ALL`); `--keep-changes`
stays opt-in and `--keep-unique` is removed. `keep_mode: unique` remains readable provenance
(`PersistedKeepMode`), so the analysis caveats on old sessions still fire; a legacy journal that
recorded it reconciles with no dedup claimed. The live view no longer stores display rows by keep
mode: it keeps runs of identical payloads (`canlib/modes/_monitor_history.py`) and renders the
newest few with a `×N` poll count, while the on-demand save draws on `save_history`, which applies
the keep mode itself — so a `--keep-changes` on-demand save is now honestly `keep_mode: changes`.
