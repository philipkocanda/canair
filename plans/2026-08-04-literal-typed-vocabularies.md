# `Literal` aliases for the closed controlled vocabularies

Status: DONE (2026-08-04) — all three vocabularies typed, each mutation-verified
to fail `ty` at the drifted write site; drift tests added for the schema ↔
`PidStatus` and `TRANSPORTS` ↔ `TransportType` pairs. Two deliberate deviations
from the sketch below, both noted inline: `CaptureEntry.keep_mode` became
`EntryKeepMode` (`"changes" | "unique" | ""`) rather than `PersistedKeepMode`,
because the read side legitimately carries `""` for a session that recorded no
policy; and the transport narrowing is a `_checked_type()` parse-then-narrow
helper rather than a bare `cast`, so the cast can't be forgotten.

Make canair's three genuinely-closed string vocabularies type-checked, deriving
each runtime tuple from the type so `argparse choices=` and existing guards keep
working unchanged. Zero behaviour change.

Follow-up to the review-remediation typing work already landed
(`a528730` actuator `Literal` + IOControl TypedDicts, `64de701` typed config
accessors, `fb9a3fa` static protocol conformance, `6ecdfa2` `CaptureEntry`), and
the last of the "Tier 3" items from that review.

## Motivation

`canlib/` has just **two** `Literal` aliases — `ActuatorState`
(`modes/_iocontrol_actuate.py:37`) and `ByteLayout` (`formatting.py:19`), both
added by that remediation work — plus three `StrEnum`s (`ByteSpace`,
`ByteNotation` in `notation.py`, `AddressingMode` in `addressing.py`). Several
other controlled vocabularies are still plain `str`, compared against bare
literals scattered across the tree. A drifted or mistyped value doesn't fail — it
silently compares `False` forever.

This is the same failure shape as the actuator-state finding: a mutation test
proved the suite could not catch `state[did] = "ON"`, which would have left
vehicle outputs energised. `Literal` closed that. Three vocabularies remain.

The pattern to reuse (already established in `_iocontrol_actuate.py:37-41`):

```python
ActuatorState = Literal["on", "off", "error"]
ACTUATOR_STATES: tuple[ActuatorState, ...] = get_args(ActuatorState)
```

One source of truth, checked statically, with the runtime tuple derived — so
`choices=`, membership guards and error messages are untouched.

## Scope

Three vocabularies, in descending value:

| # | Vocabulary | Today | Genuine sites |
|---|---|---|---|
| 1 | Keep modes | **no constants at all** | ~12 comparisons across 6 files |
| 2 | PID status | tuple in `pids.py` **+** a duplicate list in the schema | 5 consumers |
| 3 | Transport type | `TRANSPORTS` registry exists; `.type` is `str` | 6 `.type ==` comparisons |

### Explicitly out of scope

**Vehicle states** (`states.py`) and **CAN bus codes** (`can_buses.py`) stay
`str`. Both are *extended per profile* at runtime (`vehicle_states.yaml`,
`can_buses.yaml`) — `allowed_states()` returns base ∪ `ALL` ∪ the profile's own.
They are open vocabularies by design; a `Literal` would be actively wrong.

Also out of scope: `uds_parse.RESPONSE_CATEGORIES` / `ERROR_CATEGORIES` and
`keepmode`'s banner strings — the former are already tuple-constants with a
documented `CAT_*` prefix convention, the latter are message text, not a
vocabulary.

---

## 1. Keep modes — the most valuable, and not merely typo-catching

`canlib/keepmode.py` defines **no constant for any of the four values**. The
literals appear in:

- `keepmode.py:46` (`== "unique"`), `:54` (`== "changes"`)
- `capture_journal.py:270` (`== "unique"`), `:280` (`== "changes"`)
- `captures.py:156`, `:538` (`in ("changes", "unique")`)
- `_monitor_record.py:107`, `:401` (`in ("changes","unique")`), `:255`
  (`in ("all","last")`), `:258` (`== "last"`)
- `monitor.py:622` (`== "last"`), `:624` (`== "all"`)

### The finding: two concepts share one string

- **Recording policy** — what the monitor does: `changes` (run-length, the
  default), `unique` (legacy global dedup), `all` (full time-series), `last`
  (last N per PID). Produced by `keep_mode_from_args`.
- **Persisted provenance** — what actually reaches a capture file: **only**
  `changes` and `unique`. `all`/`last` are *deliberately dropped*, because they
  mean "no dedup applied", so the field's absence is the correct record.

That second rule is currently invisible and re-implemented in four places
(`captures.py:156`, `captures.py:538`, `_monitor_record.py:107`, `:401`), each
spelling `in ("changes", "unique")` by hand.

### Proposal

```python
# canlib/keepmode.py
KeepMode = Literal["changes", "unique", "all", "last"]          # monitor policy
PersistedKeepMode = Literal["changes", "unique"]                # what's stored

KEEP_MODES: tuple[KeepMode, ...] = get_args(KeepMode)
PERSISTED_KEEP_MODES: tuple[PersistedKeepMode, ...] = get_args(PersistedKeepMode)

def persisted_keep_mode(mode: str | None) -> PersistedKeepMode | None:
    """The value to store for `mode`, or None when it must not be persisted.

    `all`/`last` mean "no dedup was applied", so the field is omitted rather
    than recorded — absence is the honest provenance.
    """
```

Then:

- `keep_mode_from_args(args) -> KeepMode`
- `CaptureSession.keep_mode` (`capture_types.py:92`) →
  `NotRequired[PersistedKeepMode]`, and `CaptureEntry.keep_mode`
  (`capture_types.py:132`) → `PersistedKeepMode`
- the four hand-written `in ("changes","unique")` guards → `persisted_keep_mode()`
- `scope_is_keep_unique` / `scope_is_keep_changes` keep taking loaded entries
  (structural), but compare against the constants

This makes a real semantic distinction type-enforced, not just spell-checked.

### Care: many false-positive literals

`"all"` and `"last"` occur widely in unrelated contexts. A blanket replacement
would corrupt these — none is a keep mode:

- `_captures_step_tui.py:309` — `Binding("G", "last", "last frame")` (a keybinding)
- `commands/validate/__init__.py:131,133` — `--target all`
- `commands/states.py:186,195` — the `ALL` state meta-token
- `commands/sniff.py:36,53,64` — `e["last"]` (a timestamp field)
- `commands/decode.py:458,725` — `{"last": args.last}` (a scope dict)
- `commands/dtc.py:155`, `commands/contribute.py:130` — unrelated `"all"`

Each site must be inspected individually.

---

## 2. PID status — collapse two sources of truth

Two independent definitions exist:

- `canlib/pids.py:110` — `PID_STATUSES = ("active","draft","static","ignored")`
- `canlib/schema/pids_schema.yaml:347` — a `valid_pid_status:` list, read
  independently by `commands/validate/pids.py:316` and enforced at `:569`

**They currently agree** (verified), so this is drift-prevention rather than a
bug fix.

### Proposal

```python
PidStatus = Literal["active", "draft", "static", "ignored"]
PID_STATUSES: tuple[PidStatus, ...] = get_args(PidStatus)
DEFAULT_PID_STATUS: PidStatus = "active"
```

`pid_status(pid_def) -> PidStatus`; `PidIndexEntry.status: PidStatus`. The five
existing consumers are untouched because `PID_STATUSES` still exists:
`commands/pids.py:585`, `:648` (argparse `choices`), `pids_edit/params.py:519`,
`:586` (guards + error text), `pids.py:121`.

**Keep the schema key.** `canlib/schema/` is the documented runtime source of
truth for *data* validation and must keep working without importing Python.
Instead add a drift test asserting
`set(schema["valid_pid_status"]) == set(get_args(PidStatus))`, so the two cannot
diverge silently. (Deleting either side would be worse: the schema would lose
standalone validity, or the Python would lose static checking.)

---

## 3. Transport type — smallest, mechanical

`TRANSPORTS: dict[str, TransportSpec]` (`transport/config.py:51`) is already the
registry SSOT, and `VALID_TRANSPORTS = tuple(TRANSPORTS)` feeds argparse
`choices` in three places. But `TransportSpec.type` (`:43`) and
`TransportConfig.type` (`:93`) are `str`, so the six comparisons are unchecked:

- `commands/_live.py:444`, `:647` — `== "elm327-tcp"`
- `transport/config.py:108` — `!= "wican-ws"`, `:305`, `:326` — `== "wican-ws"`
- `transport/fallback.py:36` — `== "elm327-tcp"`

A typo (`"wican_ws"`) compares `False` forever — exactly how a Pro-only gate
could silently stop gating.

### Proposal

```python
TransportType = Literal["slcan-tcp", "wican-ws", "elm327-tcp"]
```

`TransportSpec.type` and `TransportConfig.type` become `TransportType`;
`VALID_TRANSPORTS: tuple[TransportType, ...] = get_args(TransportType)`
(the registry keys and the Literal are then asserted equal in a test, same
drift-guard shape as the schema above).

### The one wrinkle: a validated narrowing boundary

`.type` originates from **user config** (arbitrary `str`) and is validated at
`_check_type` (`transport/config.py:239`), which raises `TransportError` on an
unknown name. Annotating `.type` as `TransportType` therefore needs one
deliberate `cast(TransportType, ttype)` immediately after that check — the
standard parse-then-narrow shape. It is not free, and it should carry a comment
explaining that the cast is sound *because* `_check_type` ran.

Alternative considered and rejected: keep `.type: str` and only type the
comparisons. That gains almost nothing — the comparison sites are exactly what
needs checking.

---

## Files touched (estimate)

| Area | Files |
|---|---|
| Keep modes | `keepmode.py`, `capture_types.py`, `capture_journal.py`, `captures.py`, `modes/_monitor_record.py`, `modes/monitor.py` |
| PID status | `pids.py`, `commands/validate/pids.py` (drift test only), `pids_edit/params.py` |
| Transport type | `transport/config.py`, `transport/fallback.py`, `commands/_live.py` |
| Tests | `tests/test_keepmode.py`, `tests/test_pids.py`, `tests/test_transport_config.py`, + 2 drift tests |

~14 files. No behaviour change, no CLI-surface change.

## Verification

- **Mutation-test each `Literal`** — introduce a drifted value at one write site
  and confirm `ty` fails *at that line*, naming the type. This is the evidence
  standard the actuator work set (`a528730`); a `Literal` that doesn't bite is
  decoration.
- **Drift tests** — schema `valid_pid_status` ↔ `get_args(PidStatus)`, and
  `TRANSPORTS` keys ↔ `get_args(TransportType)`.
- **The 24-case golden suite** (`tests/test_analysis_golden.py`) must be
  byte-identical — this change is pure typing, so *any* output diff is a bug.
- Full gates: `ruff check` / `ruff format --check` / `ty check` / `pytest` /
  `canair validate all` / `make gen-check`.
- `gen_cli_reference.py --check` must stay green, proving no `--help` text moved
  (the tuples feeding `choices=` are derived, so the rendered choices must be
  unchanged).

## Non-goals / notes

- No new CLI flags, commands, or help text. If any `--help` output changes, the
  derivation from `get_args()` has gone wrong.
- Not an enum migration. `StrEnum` would change the *runtime* values' type and
  ripple into YAML/JSON serialisation and every f-string; `Literal` + a derived
  tuple keeps the values plain `str` at runtime with zero serialisation risk.
- `ty` only checks `canlib/` (`pyproject.toml` `[tool.ty.src]`), so `tests/` may
  keep passing bare strings — intentional, and why the mutation checks target
  `canlib/` write sites.
