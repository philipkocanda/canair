"""The bridge between a profile's ``response_frames:`` and a transport's ledger.

Two directions, one module, because they must agree on how a PID definition maps
onto the key a terminal tallies against — a seed written under one convention and
read back under another would silently persist counts against the wrong PID.

- :func:`seed_counts` reads the profile so a fresh connection starts already
  knowing what previous sessions proved (forward: definition → wire).
- :func:`resolve_edits` reads the ledger so a session's findings can be written
  back (reverse: wire → definition).

The key is ``(tx_id, request_hex)``: the arbitration id the terminal last sent
``ATSH`` for, and the request string exactly as ``build_ecu_index`` forms it
(``str(pid_code).upper()``). Anything the ledger holds that does not resolve to a
defined PID — a multi-DID batch request, a scan probe, an ECU discovered but not
registered — is reported, never guessed at.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from .frame_counts import CountKey, FrameCountLedger
from .pids import pid_status

FIELD = "response_frames"


def _pid_request(pid_code: object) -> str:
    """The request string for ``pid_code``, matching ``build_ecu_index``."""
    return str(pid_code).upper()


def _eligible(pid_def: object) -> bool:
    """Can this PID definition carry a frame count at all?

    A variable-length response has no single count to record, and an ``ignored``
    PID is never polled, so neither is a candidate in either direction.
    """
    if not isinstance(pid_def, dict):
        return False
    return not pid_def.get("variable_length") and pid_status(pid_def) != "ignored"


def stored_count(pid_def: object) -> int | None:
    """The ``response_frames`` a PID definition records, or None if it has none.

    The single reader of the field, so every consumer applies the same guards. The
    ``bool`` exclusion is load-bearing rather than pedantic: ``bool`` is an ``int``
    subclass, so a stray ``response_frames: true`` would otherwise read as a
    1-frame count and truncate every multi-frame response.
    """
    if not _eligible(pid_def):
        return None
    assert isinstance(pid_def, dict)
    frames = pid_def.get(FIELD)
    if isinstance(frames, bool) or not isinstance(frames, int) or frames < 1:
        return None
    return frames


def seed_counts(pids_data: dict) -> dict[CountKey, int]:
    """``{(tx_id, request): frames}`` from every PID carrying ``response_frames``.

    Deliberately unfiltered by magnitude: the count is a fact about the response,
    and whether it is small enough to *request* is the transport's call
    (``FrameCountCache.seed`` applies ``MAX_REQUESTABLE_FRAMES``). Keeping the
    ceiling in one place stops a profile fact from being silently rewritten by
    the layer that happens to read it.
    """
    out: dict[CountKey, int] = {}
    for ecu_def in (pids_data.get("ecus") or {}).values():
        tx = ecu_def.get("tx_id")
        if tx is None:
            continue
        for pid_code, pid_def in (ecu_def.get("pids") or {}).items():
            frames = stored_count(pid_def)
            if frames is None:
                continue
            out[(int(tx), _pid_request(pid_code))] = frames
    return out


@dataclass(frozen=True)
class FrameCountEdit:
    """One pending ``response_frames`` write, already diffed against the profile.

    ``frames is None`` clears the field — the session disproved what was stored.
    """

    ecu: str
    pid: str
    frames: int | None
    previous: int | None

    @property
    def cleared(self) -> bool:
        return self.frames is None

    def describe(self) -> str:
        if self.frames is None:
            return (
                f"{self.ecu} {self.pid}: {FIELD} {self.previous} cleared (response length varies)"
            )
        if self.previous is None:
            return f"{self.ecu} {self.pid}: {FIELD} {self.frames}"
        return f"{self.ecu} {self.pid}: {FIELD} {self.previous} → {self.frames}"


def _owners_by_tx(pids_data: dict) -> dict[int, list[str]]:
    """``{tx_id: [ecu_name, ...]}`` — a list because sharing one header is legal."""
    owners: dict[int, list[str]] = {}
    for name, ecu_def in (pids_data.get("ecus") or {}).items():
        tx = ecu_def.get("tx_id")
        if tx is not None:
            owners.setdefault(int(tx), []).append(name)
    return owners


def resolve_edits(
    pids_data: dict, ledger: FrameCountLedger
) -> tuple[list[FrameCountEdit], list[str]]:
    """Diff ``ledger`` against ``pids_data``; return the edits and any skip notes.

    A confirmed count is written, a retired one clears whatever was stored, and a
    count already matching the profile produces no edit — so a steady-state
    session touches no files.

    The ambiguity guard is load-bearing. Under ``normal_extended_11bit`` several
    ECUs share one 11-bit request header and are told apart by a target-address
    byte the ledger key does not carry, so a count learned there cannot be
    attributed. Writing it to the first ECU that happens to claim the header
    would corrupt a definition; it is skipped and reported instead.
    """
    owners = _owners_by_tx(pids_data)
    ecus = pids_data.get("ecus") or {}
    edits: list[FrameCountEdit] = []
    notes: list[str] = []
    ambiguous: set[int] = set()

    def attribute(key: CountKey) -> tuple[str, dict] | None:
        tx, request = key
        if tx is None:
            return None
        names = owners.get(int(tx), [])
        if len(names) > 1:
            if int(tx) not in ambiguous:
                ambiguous.add(int(tx))
                notes.append(
                    f"0x{int(tx):03X} is shared by {', '.join(sorted(names))} — "
                    f"counts learned on it cannot be attributed to one ECU"
                )
            return None
        if not names:
            return None
        pid_def = (ecus[names[0]].get("pids") or {}).get(request)
        if pid_def is None:
            # Try the raw key too: a PID written as an unquoted YAML int keeps its
            # numeric form here, while the request string is always upper-case text.
            for pid_code, candidate in (ecus[names[0]].get("pids") or {}).items():
                if _pid_request(pid_code) == request:
                    pid_def = candidate
                    break
        if not _eligible(pid_def):
            return None
        assert isinstance(pid_def, dict)
        return names[0], pid_def

    def stored(pid_def: dict) -> int | None:
        value = pid_def.get(FIELD)
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return value

    for key, frames in sorted(ledger.confirmed().items(), key=lambda kv: (kv[0][0] or 0, kv[0][1])):
        found = attribute(key)
        if found is None:
            continue
        ecu, pid_def = found
        was = stored(pid_def)
        if was != frames:
            edits.append(FrameCountEdit(ecu=ecu, pid=key[1], frames=frames, previous=was))

    for key in sorted(ledger.retired(), key=lambda k: (k[0] or 0, k[1])):
        found = attribute(key)
        if found is None:
            continue
        ecu, pid_def = found
        was = stored(pid_def)
        if was is not None:
            edits.append(FrameCountEdit(ecu=ecu, pid=key[1], frames=None, previous=was))

    return edits, notes


def persist(pids_data: dict, ledger: FrameCountLedger, *, verbose: bool = False) -> int:
    """Write a session's confirmed counts into the profile; return how many landed.

    Called from the shared session teardown, so it covers every transport with one
    implementation. Silent when there is nothing to say — the steady state, once a
    profile has learned its counts, is no output at all.

    Deliberately *not* wrapped in the ``pids`` command's schema-validate-and-revert
    guard. That guard reverts on any error anywhere in the file, which at teardown
    would throw away a correct edit because of unrelated pre-existing breakage, and
    it buys nothing here: the two schema rules for this field (``>= 1``, and never
    alongside ``variable_length``) are enforced by the editor's own checker, which
    already reparses and reverts the file it wrote.

    A failure never propagates. The measurement is a by-product of whatever the
    user actually ran, so a read-only profile or a locked file must not turn a
    successful session into a failed command.
    """
    from .edit_echo import echo_edit
    from .pids_edit import PidsEditError, set_response_frames
    from .profile import ProfileError, require_writable_definitions

    edits, notes = resolve_edits(pids_data, ledger)
    if verbose:
        for note in notes:
            print(f"  [count] {note}", file=sys.stderr)
    if not edits:
        return 0

    try:
        require_writable_definitions()
    except ProfileError as e:
        # A layered profile's base is read-only by design; the counts are simply
        # not recorded rather than the session failing over it.
        if verbose:
            print(f"  [count] not persisted: {e}", file=sys.stderr)
        return 0

    written = 0
    for edit in edits:
        try:
            path = set_response_frames(edit.ecu, edit.pid, edit.frames)
        except (PidsEditError, OSError) as e:
            print(f"  [count] could not record {edit.ecu} {edit.pid}: {e}", file=sys.stderr)
            continue
        echo_edit(edit.describe(), path)
        written += 1
    return written
