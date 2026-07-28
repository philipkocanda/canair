# ISO-TP truncation guard, short-frame lint, and capture deletion

**Date:** 2026-07-28
**Status:** in progress

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
