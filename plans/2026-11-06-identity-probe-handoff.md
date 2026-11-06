# Hand-off: `canair identity`, probe and read-loop robustness (review items 1–3)

Written 2026-11-06 for an agent that has not seen the session that wrote it. It continues
`plans/2026-11-05-kwp2000-protocol-aware-lookups.md` (read its "Phase 0c" section first) and
fixes the first three findings of a review of the identity code. Each item says what is true
today, what to change, and how to test it. Where a choice is still open, it is marked
**Decide** with a recommendation; ask the user if the recommendation does not hold up.

## Read first

1. `AGENTS.md`, especially "Running the CLI", "Formatting", "Code structure" and the WiCAN
   rules. Then the **contributing-code** and **cli-tui-standards** skills: this work changes
   output, hints and `--json`.
2. `plans/2026-11-05-kwp2000-protocol-aware-lookups.md`, sections "Phase 0c" and "Phase 5".
3. The code, in this order: `src/canair/live/identity/decode.py`,
   `src/canair/live/identity/read.py`, `src/canair/cli/live/handlers/reads.py`
   (`handle_identity`, `offer_protocol_record`), `src/canair/uds/nrc.py` (the NRC names and
   `NOT_IN_ACTIVE_SESSION_NRCS`), `src/canair/uds/response.py` (`UdsResponse`, the
   `CAT_*` categories).
4. The tests: `tests/live/identity/test_identity.py`, `test_identity_decode.py`,
   `test_identity_protocol.py`, and `tests/cli/commands/test_identity_protocol_record.py`.
   `tests/support/fakes.py::FakeTerminal` scripts replies by request; `ok()`, `nrc()` and
   `NO_DATA` build them.

## State of the tree

- The last commits on `main` at hand-off time are `33575cd1` (Python 3.15, another session)
  and `b0b8d28a` (phase 0b).
- **Phase 0c is not committed yet** unless the user has since done it. Its files are listed
  in the plan's "Phase 0c" section; `git status` shows them as modified or untracked under
  `src/canair/live/identity/`, `src/canair/cli/live/handlers/`, `src/canair/cli/commands/`,
  `src/canair/uds/protocol.py`, `src/canair/yaml_edit/echo.py`, `docs/`, `tests/` and the plan.
  If they are still uncommitted, ask the user before you start: commit them first, as their
  own change, and do not mix them into this work's commits.
- The first step of item 1 is already in that uncommitted change (see "Item 1, done").
- Other sessions work in the same checkout. Files you did not touch (for example
  `plans/2026-08-06-signal-naming-convention.md`) are theirs: never stage, revert or format
  them.

## Rules of engagement

- Run everything as `uv run …` from the repo root, and pass `workdir` on every shell call.
- Use `agent-git.sh` instead of `git` for every command (`agent-git.sh status`,
  `agent-git.sh commit -o -- <paths>`). Never mention it in repo content: commit messages,
  code and docs say `git`. Never `add -A`/`add .`; stage only your own new files, right before
  `commit -o -- <your paths>`. Commit only when the user asks.
- Hard-wrap every file at 100 columns, prose included.
- **Do not add a module** that the parser build loads (anything under `cli/` or imported by it,
  which includes `live/identity/`). `tests/contracts/test_structure.py::
  test_parser_build_does_not_load_more_modules` holds the count at its ceiling, with no room.
  Put the code in `decode.py` and `read.py`.
- Messages, hints and warnings go to **stderr** through `canair.render.messages`
  (`messages.hint(...)`, `messages.warning(...)`). Under `--json`, stdout is one JSON document
  and nothing else.
- Gates before you report done: `uv run ruff check . && uv run ruff format --check .`,
  `uv run ty check`, `make structure`, `uv run pytest -q`, and
  `uv run python scripts/gen/gen_cli_reference.py --check` (regenerate with the same script
  without `--check` if you change any `--help`).
- No live device. Test with the fake terminal only; say in your report what you did not test
  on a car.

## Background: how `canair identity` works today

`mode_identity` (`read.py`) runs inside `held_session` (a session only with `--session`):

1. `resolve_protocol_hint` reads `--protocol`, else the ECU's `identity.id_protocol`. An
   unknown protocol (missing, `none`, `unknown`) returns `None`.
2. Only when it is `None`, `_probe_protocol` sends `22 F190` and then `1A 90`, maps each reply
   to a `ProbeState` with `decode.probe_state`, and `decode.decide_protocol` picks the protocol.
3. It reads every record of that protocol's table (`records.py::PROTOCOLS`: 22 UDS DIDs, 19
   KWP2000 records) one after the other, counting `n_ok`, `n_nrc` and `n_nodata`.
4. It prints a summary, or one JSON document under `--json`, and returns a `ProtocolFinding`
   when the probe proved the protocol. `handle_identity` then offers to record it.

Every read is echo-validated (`_read`), so a late reply to another request comes back as
`ok: False` with `error_kind: "stale"`, not as data.

The `ProbeState` values (`decode.py`):

| State | Meaning |
|---|---|
| `positive` | a positive reply |
| `supported` | an NRC that does not refuse the service (for example `0x31` requestOutOfRange) |
| `session` | `0x7E`, `0x7F` or `0x80`: refused in the active session only |
| `refused` | `0x11` serviceNotSupported, which holds in every session |
| `silent` | no NRC and no positive reply |

## Item 1: "not in this session" is not "no such service"

### Done (in the uncommitted phase 0c change)

- `probe_state` returns `session` for `0x7E`/`0x7F`/`0x80` instead of `refused`.
- `_finding` proves a protocol only when the other service drew `0x11`. A `session` reply no
  longer counts, because that service may exist in another session. Example: a KWP2000 ECU that
  answers `22 F190` and wants a session for `1A` is no longer recorded as UDS.
- `decide_protocol` treats `session` like `refused` (both sides refused or session-refused
  returns `None`). Tests: `test_identity_protocol.py::test_probe_states`,
  `test_a_refusal_for_this_session_only_proves_nothing`, and the two `session` rows of
  `test_the_decision`.

### Still wrong

Verified with the fake terminal: a UDS ECU that wants an extended session for `22 F1xx`
answers `22 F190` with `0x7F` and `1A 90` with `0x11`. `decide_protocol` returns `None`, and
the user reads "neither UDS 22 nor KWP2000 1A supported (NRC 0x11, or 0x7F/0x80 refused in the
active session)", with no hint to add `--session`. The reading stops there.

The NRC itself says more than the state does:

- `0x7E` and `0x7F` exist only in UDS (ISO 14229-1). `uds/nrc.py::_KWP_SHARED_CODES` leaves
  them out on purpose.
- `0x80` is KWP2000's serviceNotSupportedInActiveDiagnosticSession. ISO 14229-1 does not
  define `0x80`.

So a `0x7F` to `22 F190` says "a UDS ECU that has service 0x22, in another session".

### Change

1. Keep the NRC with the state: give `ProbeResult` (`read.py`) the two NRC codes
   (`uds_nrc: int | None`, `kwp_nrc: int | None`), or let `probe_state` return the code with
   the state. `decide_protocol` then takes them.
2. New rows in `decide_protocol`, after the positive and `supported` rows:
   - `uds` is `session` with `0x7E`/`0x7F`, and `kwp` is not `positive`/`supported` →
     `UDS`, reason "UDS service 22 exists but is refused in this session (0x7F)".
   - `kwp` is `session` with `0x80`, and `uds` is not `positive`/`supported` → `KWP`,
     reason "KWP2000 service 1A exists but is refused in this session (0x80)".
   - Anything else stays as today.
3. `mode_identity` then reads the records of that protocol. With no session they draw the
   same NRC, so `n_ok` is 0 and item 3's hint fires. **Decide:** read them anyway (a few
   DIDs may answer outside the session; costs 22 quick NRCs), or skip the loop and print the
   hint at once. Recommendation: skip the loop when the probe's own DID drew the session NRC
   and `--session` was not given, because the answer is known; read it when `--session` was
   given.
4. `_finding` does not change: proof still needs `0x11` from the other service.
   **Decide** whether a `0x7F` reply to `1A 90` (a UDS-only code from the KWP2000 probe)
   should count as proof of UDS. Recommendation: not in this change. It is strong evidence,
   but the rule "proof needs `0x11`" is simple to explain, and the user can still record the
   protocol by hand.
5. The `--json` `probe` object (`_probe_json`) gains the two codes, for example
   `{"uds": "session", "uds_nrc": "0x7F", "kwp": "refused", "kwp_nrc": "0x11"}`.

### Tests (`tests/live/identity/test_identity_protocol.py`)

- `22 F190` → `0x7F`, `1A 90` → `0x11`: protocol `uds`, reason names the session, no finding.
- `22 F190` → `0x11`, `1A 90` → `0x80`: protocol `kwp`, no finding.
- `22 F190` → `0x80` (a KWP2000 ECU with common identifiers), `1A 90` positive: `kwp` (this
  already holds; pin it).
- The JSON `probe` object carries the codes.
- The existing rows of `test_the_decision` keep their results.

## Item 2: an asleep ECU costs the whole sweep

### Today

When the profile names the protocol, no probe runs, so nothing finds out that the ECU is
asleep. The loop sends all 22 UDS reads (verified: 22 requests to a silent fake terminal), and
each waits its full reply budget. On the ELM327 transports that budget is the adapter's
`ATST` (the Ioniq profile sets `response_timeout_ms: 614`); on `slcan-tcp` it is
`RawUdsClient._budget`. Measure before you quote a figure anywhere.

### Change

1. In the record loop, count replies in a row that are silent: `not response["ok"]`, no
   `nrc`, and an `error_kind` that is not `stale` or `decode` (a stale or undecodable reply
   means something answered). `tests/support/fakes.py::NO_DATA` carries no `error_kind`, so
   treat a missing one as silent.
2. Stop after `SILENT_READS_BEFORE_STOP = 3` such replies **only while nothing has answered
   yet** (`n_ok == 0 and n_nrc == 0`). Once any record answered, never stop: some ECUs ignore
   a DID they do not have instead of sending an NRC, and an early stop would cut their
   reading short.
3. Report it:
   - text: the existing "No response — ECU may be asleep or unpowered." line, then a hint
     (stderr) "try --session/--wake, or power the ECU (ACC/ignition on)". The summary line
     says how many records were not asked, for example `0 field(s) returned, 0 rejected,
     3 no-response, 19 not asked (of 22).`
   - `--json`: `summary` gains `"not_asked": 19`.
4. The probe path already stops when both probes are silent (`decide_protocol` returns
   `None`), so this only changes the path with a known protocol, and `--protocol`.

### Tests (`tests/live/identity/test_identity.py` or a new class there)

- Known protocol, silent terminal: exactly 3 requests sent, the JSON summary has
  `not_asked == 19`, the hint is on stderr, stdout under `--json` parses as JSON.
- The first record answers, the rest are silent: every record is asked (no early stop).
- The first two are silent, the third answers with an NRC, the rest are silent: every record
  is asked.
- A stale reply (`{"ok": False, "error": "…", "error_kind": "stale"}`) does not count toward
  the stop.

## Item 3: no hint when every record draws an NRC

### Today

`read.py` prints "No identity fields returned (all DIDs rejected/unsupported)." whenever
`n_ok == 0` and some NRC came back, whatever the NRC. Only the all-silent case gets a hint, and
that hint is printed to **stdout** with `print("  Try --session/--wake, …")`, which breaks the
rule that hints go to stderr. The NRC codes are not kept, so nothing can say which hint fits.

### Change

1. Count the NRCs by code in the loop (`collections.Counter` of `response["nrc"]`).
2. After the loop, when `n_ok == 0`, pick one hint (stderr, `messages.hint`):
   - mostly `0x7E`/`0x7F`/`0x80` and no `--session`: "these records need a diagnostic
     session: try --session".
   - the same codes **with** `--session`: "refused in the session canair opened (10 03)". On
     a KWP2000 ECU, note that it may need `10 81`, which `canair identity` cannot open yet
     (the plan's phase 5). Do not add a `--mode` flag in this change.
   - mostly `0x33` securityAccessDenied: "these records need security access, which canair
     does not unlock". Never suggest how to get a key (contributing-code, "Never share
     security keys").
   - mostly `0x31`/`0x12`: keep "this ECU answers none of the standard identity records".
3. When `n_ok > 0` but some records drew `0x7F`/`0x7E` without `--session`, add the hint
   "--session may return more fields".
4. Move the two existing `print("  Try --session/--wake, …")` lines to `messages.hint`.
5. `--json`: `summary` gains `"nrc_codes": {"0x7F": 20, "0x31": 2}`. Keep the existing keys.
6. The probe-only failure path (`resolved is None`) gets the same treatment: when a probe
   drew a session NRC, hint `--session` (item 1 makes this case rarer, but both probes can
   still be session-refused).

### Tests

- 22 records all `0x7F`, no `--session`: the `--session` hint on stderr, not on stdout.
- The same with `session=True`: the "session canair opened" hint.
- All `0x33`: the security-access hint, and nothing about keys.
- Mixed: 3 positive, 19 `0x7F`, no `--session`: the "may return more fields" hint.
- `--json`: `nrc_codes` counts are right, and stdout is exactly one JSON document.
- The old all-silent hint now appears on stderr.

## Docs

- `docs/concepts/ecu-protocols.md`, section "When the profile records no protocol": one
  sentence that a probe refused "in this session" points at `--session`.
- If `canair identity --help` changes, regenerate `docs/reference/cli/` with
  `uv run python scripts/gen/gen_cli_reference.py`. Since `33575cd1` the repo develops on
  Python 3.15, whose argparse renders some flags differently; commit only the pages your change
  touches.
- Record the outcome in the plan as a short "Phase 0d" section (what changed, the tests), in
  the style of phases 0–0c. Update the status line.

## Out of scope (later review items)

Leave these for a later change, and list them in your report if you notice more. The record
labels and missing records have their own plan, `plans/2026-11-06-identity-record-labels.md`.

- The UDS table lacks `F189` (the first record `live/identity/software.py` reads), `F180`–
  `F185`, `F18E` and `F199`. Some labels look wrong (`F188` "ECU Part Number"; KWP2000 `8C`,
  `98`). Check them against ISO 14229-1 and ISO 14230-3 before changing any.
- The default session `10 03` on KWP2000 ECUs (the plan's phase 5).
- The broad `except Exception` around `identity_report` in `read.py`.
- The per-ECU response log (`transport/ecu_log.py`) stores raw replies, the `22 F190` VIN
  included, with no `sensitive:` mask.
- The "Hyundai/Kia identity-DID `-1` offset" mention in `docs/concepts/ecu-protocols.md` after
  the quirk was retired.

## Commits

One commit per item is easiest to review. Conventional Commits, for example:

- `fix(identity): read a refusal for this session as a service that exists`
- `fix(identity): stop asking an ECU that has not answered three reads`
- `fix(identity): say which flag or condition the refused records need`

Each body explains why and names the plan. Do not edit `CHANGELOG.md`.

## Done when

- The behaviour above holds, with tests that fail without the change.
- All gates pass, and `--json` output parses for every new path.
- Your report says what changed, what you verified, and that nothing ran on a car.
