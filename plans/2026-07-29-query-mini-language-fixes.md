# Query mini-language fixes: boundary-anchored PID matching, heuristic consolidation, hex TX-id, doc drift

**Date:** 2026-07-29
**Status:** Implemented (2026-07-29)

## Motivation

An audit of the ECU/PID selection mini-language (`canlib/query.py`) and its consumers surfaced
one genuine correctness/UX footgun, two consistency/duplication issues, and some doc drift. The
parser itself is clean, centralised, and well-tested (`tests/test_query.py`); these are targeted
improvements, not a rewrite.

### Architecture recap (why the parser is central)

- **`canlib/query.py`** — single source of truth for the `ECU[:PIDLIST]` selector language
  (`parse_query`/`parse_selector`/`Query`/`Selector`). Consumed by `captures`, `decode`,
  `correlate` (all routing through `Selector.matches` → `matches_pid`).
- **`canlib/modes/multi_parse.py`** — the `query`/`monitor` "step" layer; its `query` verb
  delegates down to `canlib.query.parse_query` via `_query_selectors`.
- **`canlib/commands/_captures_query.py`** — alias-aware wrapper (`_parse_query`) + gathering.

A change to `Selector.matches_pid` is therefore covered centrally for every command.

## Issues and decisions

### 1. PID token matching is "substring anywhere", not boundary-anchored (footgun)

`Selector.matches_pid` (`canlib/query.py:76-81`) matches with `tok == p or tok in p` — a substring
**anywhere** in the PID. The docstring/examples imply prefix intent ("`22` matches every `22xxxx`
DID"), and `test_substring` (`tests/test_query.py:102`) only pins prefix-position cases, so the
loose middle-of-string behaviour is unpinned.

**Critical constraint discovered during the caller audit:** captures and PID definitions store the
**full DID** form (`22BC03`, `22B002`, `220100`, …). The substring behaviour is doing double duty
in this domain, and both uses are documented:

- **prefix** token `22` → "all `22xxxx` service-22 DIDs".
- **suffix** token `BC03` → "the `BC03` DID regardless of service byte". This is the tool's own
  documented shorthand — `query IGPM:BC03,BC06` is the usage hint printed by
  `multi_parse.py:130`, and it only works because `BC03` is a **suffix** of the stored `22BC03`.

A strict `startswith` change would break the documented `BC03` suffix shorthand (a regression).
The over-match footgun is really only arbitrary **middle-of-string** matches.

**Decision: boundary-anchored match (prefix OR suffix).** Preserves both documented uses while
dropping only middle-of-string surprises.

### 2. Two divergent "looks like a PID" heuristics (duplication)

Same concept implemented twice, disagreeing:

- `multi_parse._looks_like_pid` (`canlib/modes/multi_parse.py:36`): all-hex **and** contains a
  digit (strict).
- `_gather_query` empty-selector hint (`canlib/commands/_captures_query.py:273`):
  `any(c.isdigit())` (loose).

**Decision: consolidate** into one canonical helper in `canlib/query.py`, reused by both.

### 3. Bare hex TX-id rejected by the `query` step, accepted elsewhere

`resolve_tx_id` (`multi_parse.py:13`), `session`, and `raw` all accept a hex TX id (`770`,
`0x770`), but a bare `query 770` trips `_looks_like_pid` → rejected as "looks like a PID".
Inconsistent capability across verbs.

**Decision:** carve out a `0x`-prefixed bare token in `_query_selectors` so `query 0x770` is
accepted as a deliberate TX-id selector. A bare `770` stays rejected (genuinely ambiguous with a
PID). Keep the parser pure — the `0x` prefix is the unambiguous signal.

### 4. Doc drift

- `canlib/query.py` module docstring (lines 7-30, esp. 18-19, 27) describes matching as
  "substring"; must describe "prefix or suffix" after #1.
- `_captures_query.load_all_captures` docstring (`_captures_query.py:122-124`) still says the
  capture field is `ecu` (response address); it is now `rx`, read via `capture_io.capture_rx`.

## Non-issues (considered, not changing)

- `Query.filter` is O(records×selectors) and can't early-break (needs to mark every matched
  selector "used") — fine at this scale.
- `parse_query` join-then-resplit — intentional and correct.
- `hunt`/`investigate` bypassing the QUERY language (discrete `ecu`/`pid` positionals) is a
  *design* inconsistency, not a bug — out of scope, separate discussion.

## Implementation

### 1. Boundary-anchored PID matching — `canlib/query.py`

- Change `Selector.matches_pid` from `tok == p or tok in p` to
  `p == tok or p.startswith(tok) or p.endswith(tok)`.
- Update the module docstring (lines 7-30) to describe "prefix or suffix" precisely, replacing the
  "substring" wording (incl. the `22`→`22xxxx` example and adding a `BC03`→`22BC03` suffix example).
- Tests (`tests/test_query.py`):
  - Update `test_substring` (line 102) to assert prefix (`22`→`22BC03`) **and** suffix
    (`BC03`→`22BC03`).
  - Add a negative test pinning that a middle-only token no longer matches (e.g. `BC`→`22BC03`,
    which is neither prefix nor suffix).
  - Add a `Query.filter`-level test for the suffix shorthand (`IGPM:BC03`).

### 2. Consolidate the "looks like a PID" heuristic

- Add a canonical `looks_like_pid(token)` helper to `canlib/query.py` using the strict definition
  (all-hex **and** contains a digit).
- Rewire `multi_parse._query_selectors` (`multi_parse.py:67`) to use it (keep the local name as a
  thin re-export or replace call sites).
- Rewire the `_gather_query` empty-selector hint (`_captures_query.py:273`) to use it instead of
  the looser `any(c.isdigit())`.
- Add a unit test for the shared helper.

### 3. Explicit hex TX-id in `query` steps — `canlib/modes/multi_parse.py`

- In `_query_selectors` (lines 66-82), carve out a `0x`-prefixed bare token so `query 0x770` is
  accepted (skip the `looks_like_pid` rejection for it); a bare `770` still raises the
  space-vs-colon error.
- Test: `0x770` accepted, `770` still raises.

### 4. Doc-drift fixes

- `canlib/query.py` docstring matching wording (covered in #1).
- `_captures_query.load_all_captures` docstring (`_captures_query.py:122-124`): `ecu` → `rx` note.
- Grep `docs/` for any "substring" description of PID matching and align if present.

## Verification

- `uv run pytest tests/test_query.py` plus multi_parse/captures tests.
- Grep other `matches_pid`/`.matches(` consumers (`correlate`, `decode`, `captures`) — all route
  through `Selector.matches`, so #1 is central; spot-check `correlate.py` and `_gather_query`.
- `uv run canair captures IGPM:BC03 --limit 5` and parse-check `uv run canair query "IGPM:BC03,BC06"`
  to confirm the suffix shorthand still resolves (no device needed for parsing).
- `uv run canair validate all`.

## Docs impact

No user-facing command/flag changes. Only wording alignment where docs describe PID matching as
"substring". README stays untouched (high-level only).
