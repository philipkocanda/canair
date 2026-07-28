# canair — Type the transport contract: `Terminal` Protocol + `UdsResponse`

Status: implementation plan (backlog). Follow-on to the C1–C6 architecture
cleanup (`plans/2026-07-27-architecture-cleanup.md`) — a typing pass that turns
the codebase's two most-threaded-but-least-typed seams (the dual-transport
terminal surface and the UDS response payload it returns) from prose contracts
into compiler-checked types.

Motivation: the contributing skill (`.claude/skills/contributing/SKILL.md`) names
the dual-transport surface "the most important architectural rule" and asks for
"a `TypedDict`/dataclass over a bare `dict` where the shape matters … [for] the
terminal surface and its returned dict shapes." Today both are enforced only by
review. Formalizing them would have caught the six divergent `send_uds`
signatures the C4 shared-fake work just consolidated, and lets `ty` police the
"keep the WiCAN replaceable" litmus test instead of a human.

These two items are a natural pair (the Protocol's `send_uds` returns the
`TypedDict`) but each is independently shippable. Higher-risk than the trivial
`#3/#4/#5` typing nits (done separately) because *adopting* them will surface
latent concrete-type assumptions — which is the point; each surfaced mismatch is
a real "this code silently assumed `WiCANTerminal`" finding to resolve or
annotate.

Verification gate for each item (contributing skill "Before you finish"):

```
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
uv run ty check          # must be clean — this is where the value lands
uv run canair <touched-cmd> --help
```

Baseline pointers (verify before editing — these drift):

- Real terminals: `canlib/terminal.py::WiCANTerminal`,
  `canlib/transport/raw_terminal.py::RawTerminal`. Both already expose matching
  signatures; only the *declared* return of `send_uds` is a bare `dict`.
- The fake: `tests/_fakes.py::FakeTerminal` (added in C4) — the third
  implementer that must conform.
- The shared dispatch seam: `canlib/commands/_live.py::dispatch_mode` +
  `modes/raw_ops.py::run_raw` both call every mode against the terminal surface.
- The payload producer: `canlib/uds_parse.py::parse_uds_response` — the single
  place the response dict is built; both terminals funnel through it.

---

## Item 1 — a `Terminal` `Protocol` for the dual-transport surface

**Goal:** one `typing.Protocol` that both real terminals and the test fake
structurally satisfy, so `ty` verifies conformance and mode/handler code can type
its `terminal` parameter against the *contract* rather than a concrete class.

### Shape

New module `canlib/transport/protocol.py` (transport layer is the natural home;
it already owns `resolve_transport`/`SlcanTcpBus`). Declare:

```python
from __future__ import annotations
import asyncio
from typing import Protocol, runtime_checkable
from canlib.uds_parse import UdsResponse   # once Item 2 lands; until then: dict

@runtime_checkable
class Terminal(Protocol):
    """The async surface every transport exposes (WiCANTerminal / RawTerminal).

    Modes are written against THIS, never a concrete terminal, so a new backend
    slots in by implementing it (the "keep the WiCAN replaceable" rule)."""

    async def set_header(self, tx_id: int) -> None: ...
    async def send_uds(
        self,
        service_pid: str,
        timeout: float | None = ...,
        expected_sid: int | None = ...,
        expected_did: int | None = ...,
        expected_echo: bytes | None = ...,
        retries: int = ...,
    ) -> UdsResponse: ...          # `dict` until Item 2
    async def send_command(self, cmd: str, timeout: float | None = ...) -> str: ...
    async def enter_extended_session(
        self, wake: bool = ..., mode: str = ...
    ) -> tuple[bool, asyncio.Task | None]: ...
    async def close(self) -> None: ...
```

Signatures are copied verbatim from `WiCANTerminal` (verify against the tree —
`send_uds` has `expected_sid`/`expected_did`/`expected_echo`/`retries`;
`enter_extended_session(wake=False, mode="03")` — the `mode=` keyword is the
dual-transport contract C4's fake already honours).

### Adoption (where the value lands)

1. **Annotate the seam.** Type `terminal: Terminal` in `dispatch_mode`
   (`commands/_live.py`), `modes/raw_ops.py::run_raw`, and the `mode_*` handler
   signatures that currently say `WiCANTerminal` (grep
   `terminal: WiCANTerminal`, `terminal,` in `modes/`). Do NOT change runtime
   behavior — this is annotation only.
2. **Do NOT** make `WiCANTerminal`/`RawTerminal` *inherit* the Protocol
   (structural typing needs no nominal base); a `runtime_checkable` Protocol also
   allows an `isinstance` smoke assertion in a test.
3. **Note the `WiCANTerminal` type name.** Several files import `WiCANTerminal`
   purely for annotation (e.g. `iocontrol.py`, `_iocontrol_actuate.py` via the
   TUI). Where the code only *uses* the surface, retype to `Terminal`; where it
   genuinely needs WiCAN-specifics (device mode/reboot), keep the concrete type
   (litmus test: does it still make sense from a replayed `.asc`?).

### Test

- `tests/test_transport_protocol.py`: assert `isinstance(WiCANTerminal(...) , Terminal)`
  and `isinstance(RawTerminal(...), Terminal)` (construct via existing fixtures /
  `make_terminal`), and `isinstance(FakeTerminal(), Terminal)` — the fake's
  conformance is the regression guard against signature drift.
- The real oracle is `ty check`: after retyping the seam, any concrete-only
  method call on a `Terminal`-typed variable fails the build.

### Risk

Medium. Adoption may surface: (a) a mode reaching for a `WiCANTerminal`-only
attribute (a real coupling to fix or annotate), (b) the `send_uds -> dict` vs
`-> UdsResponse` seam (sequence Item 2 first, or land Item 1 with `-> dict` and
tighten later). Keep it annotation-only; no runtime changes.

---

## Item 2 — a `UdsResponse` `TypedDict` for the response payload

**Goal:** replace the bare `dict` returned by `parse_uds_response` / `send_uds`
with a `TypedDict` that documents the shape and lets `ty` catch key typos and
wrong-type access across every consumer.

### Shape

Declared in `canlib/uds_parse.py` (beside its sole producer):

```python
from typing import NotRequired, TypedDict

class UdsResponse(TypedDict):
    raw: str                          # always: original response text
    ok: bool                          # always: positive + echo-matching?
    hex: NotRequired[str]             # parseable positive: uppercased, space-stripped
    bytes: NotRequired[bytes]         # parseable positive
    nrc: NotRequired[int]             # negative (7F): the NRC byte
    nrc_service: NotRequired[int]     # negative: echoed service byte
    nrc_desc: NotRequired[str]        # negative: human description
    error: NotRequired[str]           # parse failure / NO DATA / echo mismatch
```

Cross-check the keys against `parse_uds_response`'s own docstring + body and
against the `nrc`/`nrc_service` removal-on-mismatch branch, so the `NotRequired`
set is exactly right. Mirror `ResultEntry` in `modes/multi_batch.py` (already a
`TypedDict` with `NotRequired`) for style.

### Adoption

1. `parse_uds_response(...) -> UdsResponse`.
2. `send_uds(...) -> UdsResponse` on **both** `WiCANTerminal` and `RawTerminal`
   (they funnel through `parse_uds_response`), and on the `Terminal` Protocol
   (Item 1).
3. Update the C4 fake builders to the type: `tests/_fakes.py::ok()`/`nrc()` return
   `UdsResponse`; `FakeTerminal.send_uds -> UdsResponse`. This makes the fake's
   payloads compiler-checked against the real shape — the shape half of the C4
   consolidation.
4. Consumers that index the dict gain checking for free; notable ones to eyeball:
   `IOControlActuator.extract_status_bytes`, `MonitorRecorder.observe`, the
   scanners (`iocontrol_scan`/`kwp_*`/`sessions_scan`/`discovery_scan`), and
   `multi_batch`/`multi`. Fix any key typo / `.get` on a required key that `ty`
   flags.

### Test

The `ty` pass is the test; add nothing unless a consumer becomes independently
worth a unit (`extract_status_bytes` already has a device-free smoke via the C6b
work). Keep `test_uds_parse.py` green.

### Risk

Medium, mechanical. `TypedDict` access rules: reading a `NotRequired` key without
a guard is a `ty` error — expect to add `resp.get("hex")` / `if "nrc" in resp`
narrowing in a handful of spots. That's the shape contract doing its job. No
runtime change (a `TypedDict` is a plain `dict` at runtime).

---

## Suggested sequencing

1. **Item 2 first** (`UdsResponse`) — it's the return type Item 1 references, and
   it's self-contained (one producer, mechanical consumer fixups).
2. **Item 1** (`Terminal` Protocol) — retype the seam against the contract, with
   `send_uds -> UdsResponse` already in place.

Both are annotation-only (no behavior change); the win is entirely in `ty`
coverage and in code that now *states* the contract it was relying on.
