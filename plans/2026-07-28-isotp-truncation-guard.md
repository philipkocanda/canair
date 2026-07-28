# ISO-TP reassembly hardening: truncation guard, frame contiguity, hex-counter wrap, stale-frame drain

**Date:** 2026-07-28
**Status:** Implemented (2026-07-28)

## Motivation

Six OBC `2101` captures decoded to an implausible `LDC_TEMP` of −35/−33 °C. Investigation
showed they were **truncated multi-frame reads**: a clean OBC `2101` response is 48 bytes, but
these were 41 bytes — exactly one 7-byte ISO-TP consecutive frame short, dropped *mid-stream*.
The drop shifts every byte after the gap left by 7, so `B17`/`B19` no longer land on
`OBC_DC_A`/`LDC_TEMP` and read garbage (`0x41`/`0x43` → −35/−33 °C).

The 6 were only the subset whose misalignment produced an *implausible* value. A length-based
scan of the whole corpus found **15 forty-one-byte** OBC `2101` captures total (6 deleted, **9
remaining** whose misalignment happens to read plausibly and thus silently poison OBC params),
plus a benign **44-byte** class (43 captures) that is merely missing trailing ISO-TP padding and
decodes correctly.

### Origin

Not an import — all were **live device reads** recorded via `--save`/`--monitor` during real
driving/charging/HVAC sessions (commits `c1664e5` 2026-07-19, `3ced4d3` 2026-07-26). The ELM327
adapter occasionally returns a short multi-frame response and `--save` stored it verbatim with no
length validation.

## Validation (live device, 2026-07-28)

Confirmed the fix is possible **generically** — no per-PID "known good" length table needed —
because ISO-TP is self-describing.

- **`slcan-tcp` path is safe by construction.** canair reassembles via `can-isotp`
  (`transport/uds_raw.py:88`), which enforces the FF-declared length internally: a dropped frame
  times out and returns nothing, never a silent truncation. A live raw TPMS `22C00B` read
  reassembled cleanly (20 data bytes). All corrupt captures came from the *other* path.

- **`wican-ws`/ELM327 path is the culprit and it exposes the length.** Raw wire format of a live
  multi-frame read:

  ```
  22C00B\r 017 \r 0:62C00BFFFF00 \r 1:00C84F0100C84E \r 2:0100C84D0100C6 \r 3:4D0100AAAAAAAA
  ```

  The standalone **`017`** line is the ISO-TP total length (`0x017` = 23 bytes). The `0:` data is
  already PCI-stripped. **`parse_uds_response` currently discards the `017` line** (`uds_parse.py`
  multi-frame branch: it isn't an `N:` line, so the regex drops it) — precisely why truncation
  goes undetected. The length is sitting in `data_lines[0]`, unused.

  → An **exact** check `reassembled_length == declared_length` is possible, generic across all
  PIDs/vehicles, working on the first-ever capture.

## Changes

### 1. ISO-TP declared-length guard (root-cause fix)

**File:** `canlib/uds_parse.py`, `parse_uds_response`.

- In the multi-frame branch, detect the bare-hex ELM total-length token (a `data_lines` entry
  matching `^0*[0-9A-Fa-f]{1,3}$` that is not an `N:` frame line) and parse it as `declared_len`.
- After reassembly, compare `len(response_bytes)` (excluding trailing ISO-TP `AA` padding beyond
  `declared_len`) to `declared_len`.
- On mismatch → `result["ok"] = False`, `result["error"] = "truncated ISO-TP: got N bytes,
  declared M"`. `capture_from_response` then stores it as an error, **not** a `payload` — so
  `--save`/`--monitor` never persist a truncated payload again. **Reject, not store-flagged**
  (matches existing NRC/echo-mismatch handling; a truncated multi-frame read has no analytical
  value and silently poisons downstream params).
- Add `result["isotp_declared_len"]` for diagnostics/tests.
- No-op for single-frame responses (no length line) and for `slcan-tcp` (no ELM length line; the
  library already guaranteed completeness).

### 2. `canair validate captures` short-frame lint (device-free backstop)

**Files:** `canlib/commands/validate/captures.py` (+ helper near `validate_capture_payload` in
`canlib/uds_parse.py`).

- Soft warning for a stored `payload` shorter than the max clean length seen for that ECU:PID in
  the corpus (the "48 vs 41 byte" signal). The original FF length can't be recovered from an
  already-stored payload, so this is an explicit heuristic lint, not the authoritative guard.
- Emitted like the existing echo-mismatch/non-hex/untimed warnings; `--strict` promotes to error.
- Catches the 9 already-stored 41-byte OBC captures without a device.

**DROPPED (2026-07-28).** Prototyped and abandoned: the max-observed-length proxy is far too
noisy. Many PIDs legitimately return variable-length responses (multi-DID batches, optional
trailing data), so "shorter than the longest seen" flagged **4,600+** captures even when requiring
a full 7-byte (one ISO-TP consecutive frame) shortfall — burying the real echo/non-hex warnings.
Without the authoritative FF-declared length (not stored on existing captures) the heuristic can't
tell a truncated read from normal content variation. The **live guard (#1) is the real fix** — it
prevents new truncated captures at the source; the cleanup (#3) removes the known-bad ones.

### 3. `canair captures uds --delete <query> [--dry-run]` + cleanup

**File:** `canlib/commands/captures.py`.

- Delete matching captures via `load_all_captures()` → `_session_idx`/`_capture_idx` →
  `captures.delete_capture` (reverse order per file). `--dry-run` previews.
- Using the resolved-name helper avoids the earlier process failure (a hand-rolled scan keyed on
  the ECU short name instead of the stored RX address).
- Use it to remove the **9 remaining 41-byte OBC 2101 captures**: `2026-04-21` ×2, `2026-07-19`
  ×3, `2026-07-26` ×4. **Keep the 44-byte class** (padding-only, decodes fine).

**DONE (2026-07-28).** The command shipped (`cmd_delete` in `canlib/commands/captures.py`;
`--delete`/`--dry-run`/`--yes` flags; refuses a bare `--delete`; `--json` for dry-run). The
cleanup removed **15** truncated 41-byte OBC 2101 captures (the tree held 21 originally — the 6
deleted during the initial investigation had the implausible-value signature; the remaining 15
were truncated too but read plausibly). Done via a one-off targeted script using
`load_all_captures()` (correct RX-address resolution) + `delete_capture`, since the length
predicate isn't expressible as a QUERY. The 44-byte class (43 captures, padding-only) was kept.
Result: OBC 2101 length distribution is now `{44: 43, 48: 2521}`; `LDC_TEMP` reads a clean
17–70 °C.

## Docs

- `docs/concepts/captures-and-states.md` — truncation guard; truncated reads rejected.
- `AGENTS.md` — `canair captures uds --delete` in the captures section; ISO-TP length guard note.
- `README.md` — only if the command map line changes (keep terse).
- `.claude/skills/reverse-engineer-signal/SKILL.md` — RX-address footgun + `load_all_captures()`
  as the scripting entry point.

## Tests

- `parse_uds_response`: complete multi-frame (passes), truncated with real `017`-style length line
  (→ `ok=False`, truncated error), single-frame (guard skipped), padding-only 44-byte (accepted).
- `captures uds --delete --dry-run` selects the right rows.

## Device housekeeping

WiCAN was left in ELM327 mode by the validation reads; restored to AutoPID at the start of build
(`wican mode set auto_pid --yes`).

---

## Follow-on (2026-07-28): reassembly interleaving audit — drain, contiguity, hex-counter wrap

After the truncation guard shipped, an audit of both transports' ISO-TP reassembly for
interleaving/corruption under fast back-to-back multi-frame responses surfaced three more issues in
the **`wican-ws`/ELM327** path (the `slcan-tcp` path is structurally safe — one `can-isotp` stack
per ECU over a single `Notifier` reader thread, and each stack drops frames whose arbitration ID
isn't its `rxid` via `address.is_for_me`, so cross-ECU misrouting cannot happen).

### 4. Stale-frame drain before each ELM327 command (root cause)

**File:** `canlib/terminal.py`.

- `_send_command_locked` never drained stale WebSocket frames before sending, and `self._buffer`
  was dead code. A command that timed out mid-multiframe left the ECU's late frames buffered; they
  leaked into the *next* command's `response_parts`. The existing `expected_sid`/`expected_echo`
  validation only caught this downstream.
- Replaced dead `self._buffer` with `self._pipe_dirty`, set `= not clean_exit` after each command
  (`clean_exit` is True only when the read loop consumed the ELM `>` prompt with no unresolved
  ResponsePending). When the next command starts with a dirty pipe it drains first. Zero latency in
  the common (clean-prompt) case; the drain only fires after a timeout.
- Hardened `_drain()` with an overall `max_seconds` budget so a continuous stream can't hang it;
  existing callers unchanged (defaults preserved); `connect()` clears the flag.

### 5. Multi-frame index-contiguity guard

**File:** `canlib/uds_parse.py`, `parse_uds_response`.

- After parsing the numbered frame lines, verify the counters form the expected sequence; a
  missing, duplicate, or out-of-order counter → `ok=False`, `error="non-contiguous ISO-TP
  frames: …"` (reject, matching the truncation guard). A gap can be masked from the declared-length
  guard by trailing padding, so contiguity is checked independently.

### 6. ELM327 hex frame-counter wrap fix (latent bug)

**File:** `canlib/uds_parse.py`, `parse_uds_response`.

- The ELM327 numbers multi-frame lines with a **single hex digit** that wraps `0..F` then repeats,
  but the line regex matched only decimal digits (`^\d+:`) and the code *sorted* by the printed
  index. Effect: for responses with 16+ frames, lines `A:`–`F:` were silently dropped and the
  wrapped `0..9` tail mis-ordered — any multi-frame UDS response ≥11 frames over `wican-ws` failed
  (caught only as "truncated", never completing).
- Fix: broadened the regex to a single hex digit (`^[0-9A-Fa-f]:`) and replaced sort-by-index with
  **arrival-order reassembly + wrapping-counter unwrap** — ELM327 emits frames in transmission
  order (confirmed on-car), so arrival order is authoritative; each line's counter must equal
  `expected_counter & 0xF` as the counter increments past `F`. Subsumes the contiguity guard (#5).
- Behavior change: reordered frame lines are now rejected as corruption (previously sorted +
  accepted). Real ELM327 output is always in order, so reordering genuinely indicates a
  stale/dropped frame.

### Tests

- `tests/test_uds_parse.py`: `TestIsoTpContiguityGuard` (missing/duplicate/out-of-order/gap-masked)
  and `TestIsoTpFrameCounterWrap` (18-frame wrap `0..F,0,1`, exact 16-frame boundary, `A`–`F`
  counters parsed, broken-after-wrap rejected).
- `tests/test_terminal.py`: `TestStaleFrameDrain` (timeout marks pipe dirty, clean prompt stays
  clean, stale frames drained before next command, no drain when clean).
- Full suite: 2505 passed; `ruff` clean; `canair validate all` OK.

### Live stress test (on-car, READY mode, 2026-07-28)

Verified on real hardware — the fix works end-to-end, not just in synthetic tests.

- **Smoking gun:** SKM `22B002` (178 B) returned with counters `0,1,…,E,F,0,1,…,9` — **26 frames,
  counter wraps past `F`** — and reassembled correctly to exactly 178 B, matching the declared
  length token `0B2` (=178) and the historical capture. `A:`–`F:` frames (dropped by the old
  regex) and the wrapped `0:`–`9:` tail all landed. `7F2278` ResponsePending handled.
- **Known large wrapping stress PIDs on this car:** SKM `22B002` (178 B, ~26 frames), BMS `21F2`
  (123 B, ~18 frames). VCU `21F2` (86 B, ~13 frames) is just under the wrap boundary — a
  non-wrapping control.
- **Rapid interleave:** 20 back-to-back reads (10× SKM 178 B interleaved with 10× BMS 21F2 123 B) —
  every response exact length, no interleaving/corruption/truncation. Exercised both reassembly and
  the inter-command drain.
- **Parity:** the same 20 interleaved reads over `slcan-tcp` (client-side `can-isotp`) also returned
  correct lengths — both transports handle large wrapping responses.
- 5 captures saved to `2026-07-28.json` (SKM/BMS/VCU/MCU), `validate captures` OK. Device restored
  to `auto_pid`.

### Docs

No user-facing surface changed (subcommands/flags/defaults unchanged) — the wrap behavior is
documented in the `uds_parse.py` comment; no README/`docs/` update required.
