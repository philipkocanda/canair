"""Rename a vehicle state everywhere a profile references it.

A state is a token, not an object: ``vehicle_states.yaml`` declares it, and the
per-ECU ``ecus/`` files, every recorded capture session (its ``vehicle_states``
union and ``state_spans`` timeline), the DTC log and the raw-CAN log index each
carry it as a plain string. Renaming only the declaration leaves every one of
those holding a token the vocabulary no longer knows, so a rename rewrites them
all, or none.

:func:`plan_state_rename` computes every edit in memory and writes nothing; the
command previews it. :func:`apply_state_rename` writes the plan and, if any step
fails, restores every file it already wrote.

Each store is rewritten in the narrowest way that keeps its file stable:

* ``ecus/`` files are rewritten line by line. Their ``vehicle_states`` are inline
  flow lists (``[READY, CHARGING]``), so only those lines change. A full
  ruamel round-trip would also re-flow unrelated long scalars, and the YAML 1.1
  reader turns a bare ``on:``/``off:`` key into ``true:``/``false:``. Each file is
  re-parsed and schema-validated afterwards.
* Capture files are JSON and go through the canonical capture serializer.
* The DTC log and the raw-CAN index are machine-written with the round-trip
  dumper already, so they are rewritten with it.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from canlib.states import ALL_STATE, needs_yaml_quotes

if TYPE_CHECKING:
    from canlib.profile import Profile

# An inline ``vehicle_states: [...]`` list, also as the first key of a list item.
_FLOW_LINE = re.compile(
    r"^(?P<head>[ \t]*(?:-[ \t]+)?vehicle_states:[ \t]*\[)(?P<body>[^\]\n]*)(?P<tail>\].*)$",
    re.MULTILINE,
)


class StateRenameError(Exception):
    """Raised when a rename cannot be planned or applied safely."""


@dataclass
class StateRenamePlan:
    """Every edit a rename makes, computed before anything is written.

    ``ecu_refs``/``capture_refs``/``log_refs`` count the rewritten references per
    file (a capture session counts once per list it appears in: its union and each
    timeline span). ``_texts`` holds the new contents to write, keyed by path.
    """

    old: str
    new: str
    states_file: Path
    ecu_refs: dict[Path, int] = field(default_factory=dict)
    capture_refs: dict[Path, int] = field(default_factory=dict)
    log_refs: dict[Path, int] = field(default_factory=dict)
    _texts: dict[Path, str] = field(default_factory=dict, repr=False)

    @property
    def rewrites_history(self) -> bool:
        """Whether the rename touches recorded data, not just definitions."""
        return bool(self.capture_refs or self.log_refs)


def swap_token(tokens, old: str, new: str) -> list[str] | None:
    """``tokens`` with ``old`` replaced by ``new``, or ``None`` when ``old`` is absent.

    Matching is case-insensitive (legacy sessions hold lowercase tokens). Every
    other token is kept verbatim, and a ``new`` the list already holds is not
    duplicated.
    """
    if not _holds(tokens, old):
        return None
    out: list[str] = []
    for tok in tokens:
        tok = new if _same(tok, old) else tok
        if not any(_same(tok, seen) for seen in out):
            out.append(tok)
    return out


def _same(token, name: str) -> bool:
    return str(token).strip().strip("'\"").upper() == str(name).upper()


def _holds(tokens, name: str) -> bool:
    return isinstance(tokens, list) and any(_same(t, name) for t in tokens)


def rewrite_flow_lists(text: str, old: str, new: str) -> tuple[str, int]:
    """Rewrite the inline ``vehicle_states`` lists in a YAML text.

    Returns ``(new_text, lists_changed)``. Untouched tokens keep their exact
    spelling; only a list that held ``old`` is re-joined. A ``new`` that YAML 1.1
    reads as a boolean (``ON``) is written double-quoted.
    """
    count = 0

    def _sub(m: re.Match) -> str:
        nonlocal count
        tokens = [t.strip() for t in m.group("body").split(",") if t.strip()]
        written = f'"{new}"' if needs_yaml_quotes(new) else new
        swapped = swap_token(tokens, old, written)
        if swapped is None:
            return m.group(0)
        count += 1
        return f"{m.group('head')}{', '.join(swapped)}{m.group('tail')}"

    return _FLOW_LINE.sub(_sub, text), count


def _walk_state_lists(node: Any, old: str, new: str) -> int:
    """Swap ``old`` in place in every ``vehicle_states`` list under ``node``."""
    from canlib.yaml_rt import state_token

    count = 0
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "vehicle_states":
                swapped = swap_token(value, old, state_token(new))
                if swapped is not None:
                    value[:] = swapped
                    count += 1
            else:
                count += _walk_state_lists(value, old, new)
    elif isinstance(node, list):
        for item in node:
            count += _walk_state_lists(item, old, new)
    return count


def _leftover(node: Any, old: str) -> bool:
    """Whether any ``vehicle_states`` list under ``node`` still holds ``old``."""
    if isinstance(node, dict):
        return any(
            (key == "vehicle_states" and _holds(value, old)) or _leftover(value, old)
            for key, value in node.items()
        )
    if isinstance(node, list):
        return any(_leftover(item, old) for item in node)
    return False


def _plan_ecus(plan: StateRenamePlan, ecus_dir: Path) -> None:
    from canlib import yaml_io

    for path in sorted(ecus_dir.glob("*.yaml")):
        text = path.read_text()
        new_text, n = rewrite_flow_lists(text, plan.old, plan.new)
        if _leftover(yaml_io.safe_load(new_text), plan.old):
            raise StateRenameError(
                f"{path.name} holds {plan.old} in a block-style vehicle_states list; "
                "write it as an inline list ([A, B]) and rename again"
            )
        if n:
            plan.ecu_refs[path] = n
            plan._texts[path] = new_text


def _plan_captures(plan: StateRenamePlan, captures_dir: Path) -> None:
    from canlib.captures import io as capture_io

    if not captures_dir.is_dir():
        return
    for path in capture_io.iter_capture_files(captures_dir):
        data = capture_io.load_capture_file(path)
        n = 0
        for session in data.get("sessions") or []:
            swapped = swap_token(session.get("vehicle_states"), plan.old, plan.new)
            if swapped is not None:
                session["vehicle_states"] = swapped
                n += 1
            for span in (session.get("state_spans") or {}).get("spans") or []:
                swapped = swap_token(span.get("states"), plan.old, plan.new)
                if swapped is not None:
                    span["states"] = swapped
                    n += 1
        if n:
            plan.capture_refs[path] = n
            plan._texts[path] = capture_io.capture_text(data)


def _plan_round_trip(plan: StateRenamePlan, path: Path) -> None:
    import io

    from canlib.yaml_rt import detect_sequence_indent, dump, round_trip_yaml

    if not path.is_file():
        return
    text = path.read_text()
    data = round_trip_yaml().load(text)
    n = _walk_state_lists(data, plan.old, plan.new)
    if n:
        seq, off = detect_sequence_indent(text) or (2, 0)
        buf = io.StringIO()
        dump(data, buf, sequence=seq, offset=off)
        plan.log_refs[path] = n
        plan._texts[path] = buf.getvalue()


def plan_state_rename(old: str, new: str, *, profile: Profile | None = None) -> StateRenamePlan:
    """Compute every edit renaming ``old`` to ``new`` makes. Writes nothing.

    Raises :class:`StateRenameError` (or a
    :class:`~canlib.states.edit.StatesEditError` for an invalid name) when the
    rename cannot be made: ``old`` is not declared, ``new`` already is, either is
    ``ALL``, or a reference is in a form this cannot rewrite.
    """
    from canlib.dtc.log import log_file as dtc_log_file
    from canlib.profile import active
    from canlib.states.edit import _find, _load_doc, normalize_name

    profile = profile or active()
    old, new = normalize_name(old), normalize_name(new)
    if ALL_STATE in (old, new):
        raise StateRenameError(f"{ALL_STATE} is the every-state meta-token and cannot be renamed")
    if old == new:
        raise StateRenameError(f"{old} is already called {new}")
    states = _load_doc(profile.states_file)["states"]
    if _find(states, old) == -1:
        raise StateRenameError(f"state {old!r} not found in {profile.states_file}")
    if _find(states, new) != -1:
        raise StateRenameError(f"state {new!r} already exists")

    plan = StateRenamePlan(old=old, new=new, states_file=profile.states_file)
    _plan_ecus(plan, profile.ecus_dir)
    _plan_captures(plan, profile.captures_dir)
    _plan_round_trip(plan, dtc_log_file(profile))
    _plan_round_trip(plan, profile.can_index_file)
    return plan


def apply_state_rename(plan: StateRenamePlan, *, profile: Profile | None = None) -> None:
    """Write ``plan``: the vocabulary first, then every reference.

    All or nothing: if any write or post-write validation fails, every file
    already written (the vocabulary included) is restored before the error is
    re-raised.
    """
    from canlib.commands.validate import validate_pids_file
    from canlib.pids import clear_cache
    from canlib.profile import active
    from canlib.states.edit import rename_state

    profile = profile or active()
    originals: dict[Path, str] = {plan.states_file: plan.states_file.read_text()}
    try:
        rename_state(plan.old, plan.new, profile=profile)
        for path, text in plan._texts.items():
            originals[path] = path.read_text()
            _write_atomic(path, text)
            if path in plan.ecu_refs:
                clear_cache()
                ok, msg = validate_pids_file(path, profile)
                if not ok:
                    raise StateRenameError(f"{path.name} invalid after the rename:\n{msg}")
    except Exception:
        for path, text in originals.items():
            _write_atomic(path, text)
        clear_cache()
        raise
    clear_cache()


def _write_atomic(path: Path, text: str) -> None:
    """Replace ``path`` with ``text`` via a temp file, so a crash never truncates it."""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
