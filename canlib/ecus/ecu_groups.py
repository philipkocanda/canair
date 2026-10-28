"""Named capture/monitor selector groups.

A *group* is a named, reusable query stored per-profile in ``groups.yaml``: a
list of ECU / ECU:PID selectors in the shared mini-language (see
:mod:`canlib.query`). It lets you save a recurring set of things to poll —
``charging`` = ``BMS:2101 BMS:2105 OBC VCU MCU`` — and recall it on the command
line with the ``@`` sigil::

    canair monitor @charging                 # expands to the group's members
    canair monitor @driving CLU:220B         # a group plus an extra selector
    canair read @powertrain --save --label "…"

Groups are referenced by :func:`expand_group_refs`, a purely textual expansion
run *before* the query parser, so a group composes freely with other groups and
with ad-hoc selectors. Members are plain selectors (ECU or ECU:PID), never full
mini-language steps.

This module is profile-data-facing but device-free: :func:`load_groups` reads
the vocabulary; :func:`expand_group_refs` is pure (it takes the loaded mapping,
mirroring how :mod:`canlib.query` stays source-agnostic).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from canlib import yaml_io

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from canlib.profile import Profile

# The sigil that marks a group reference on the command line (``@charging``).
GROUP_SIGIL = "@"

# Leading verbs of the multi mini-language whose steps are NOT query selectors —
# a group ref inside one is meaningless, so those steps pass through unexpanded.
# Mirrors the query-excluded subset of ``canlib.commands._live.steps.STEP_VERBS`` (a
# drift guard test keeps them in sync); kept local so this low-level module never
# imports the heavy live-command runtime.
_NON_QUERY_VERBS = frozenset({"skm-wake", "session", "raw", "scan", "iocontrol", "sleep", "repl"})


class GroupError(ValueError):
    """Raised when a group reference or definition is invalid."""


@dataclass(frozen=True)
class Group:
    """One named selector group.

    Attributes:
        name:        Group name (lower-cased).
        description: Human-readable description (may be empty).
        members:     Selector tokens (ECU or ECU:PID), order preserved.
    """

    name: str
    description: str = ""
    members: tuple[str, ...] = ()


def normalize_group_name(name: str) -> str:
    """Normalize a group name to its canonical (lower-cased, stripped) form."""
    return str(name).strip().lower()


def _groups_path(profile: Profile | None = None) -> Path:
    from canlib.profile import active

    return (profile or active()).groups_file


def load_groups(profile: Profile | None = None) -> dict[str, Group]:
    """Load the profile's groups. Returns an empty mapping when absent.

    Keyed by the normalized (lower-cased) group name, insertion order preserved.
    Raises :class:`GroupError` on a structurally invalid file.
    """
    path = _groups_path(profile)
    if not path.exists():
        return {}
    data = yaml_io.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise GroupError(f"{path.name}: top-level must be a mapping")
    raw = data.get("groups") or {}
    if not isinstance(raw, dict):
        raise GroupError(f"{path.name}: 'groups:' must be a mapping")

    out: dict[str, Group] = {}
    for name, meta in raw.items():
        key = normalize_group_name(name)
        if not key:
            continue
        if isinstance(meta, dict):
            members = meta.get("members") or []
            description = str(meta.get("description", "") or "").strip()
        elif isinstance(meta, list):  # bare list shorthand: name: [SEL, ...]
            members = meta
            description = ""
        else:
            members = []
            description = ""
        if not isinstance(members, list):
            raise GroupError(f"{path.name}: group '{name}' members must be a list")
        toks = tuple(str(m).strip() for m in members if str(m).strip())
        out[key] = Group(name=key, description=description, members=toks)
    return out


def allowed_groups(profile: Profile | None = None) -> set[str]:
    """The set of declared group names for a profile (empty when undeclared)."""
    return set(load_groups(profile))


def group_members(name: str, profile: Profile | None = None) -> tuple[str, ...]:
    """The member selectors of a group. Raises :class:`GroupError` if unknown."""
    groups = load_groups(profile)
    key = normalize_group_name(name)
    if key not in groups:
        raise GroupError(_unknown_group_message(key, groups))
    return groups[key].members


def _unknown_group_message(name: str, groups: Mapping[str, Group]) -> str:
    available = ", ".join(f"{GROUP_SIGIL}{g}" for g in sorted(groups)) or "none"
    return f"unknown group {GROUP_SIGIL}{name!s}. Available groups: {available}"


def _expand_token(token: str, groups: Mapping[str, Group]) -> list[str]:
    """Expand one selector token: a ``@group`` ref → its members, else itself."""
    if not token.startswith(GROUP_SIGIL):
        return [token]
    ref = token[len(GROUP_SIGIL) :]
    if not ref:
        raise GroupError(
            f"bare {GROUP_SIGIL!r} is not a group — write {GROUP_SIGIL}NAME "
            f"(e.g. {GROUP_SIGIL}charging)"
        )
    if ":" in ref:
        raise GroupError(
            f"group reference {token!r} must not carry a PID — attach PIDs to "
            f"ECUs (BMS:2101), not to groups. Did you mean {GROUP_SIGIL}{ref.split(':')[0]}?"
        )
    key = normalize_group_name(ref)
    if key not in groups:
        raise GroupError(_unknown_group_message(key, groups))
    return list(groups[key].members)


def expand_group_refs(steps: Sequence[str], groups: Mapping[str, Group]) -> list[str]:
    """Expand ``@group`` references in mini-language steps into their selectors.

    Each step string is tokenized on whitespace. A step whose leading verb is a
    non-query mini-language verb (``session``/``raw``/``scan``/…) is passed
    through untouched — group refs are only meaningful in query steps (bare
    selectors or an explicit ``query`` step). Within a query step, every
    ``@name`` token is replaced *in place* by that group's member tokens, so a
    group composes with surrounding ad-hoc selectors and other groups.

    Args:
        steps:  The raw positional STEP strings (as typed on the command line).
        groups: The loaded group mapping (see :func:`load_groups`).

    Returns:
        A new list of step strings with group refs expanded. Steps without a
        group ref are returned unchanged.

    Raises:
        GroupError: on a bare ``@``, a ``@name:pid`` ref, or an unknown group.
    """
    out: list[str] = []
    for step in steps:
        tokens = step.split()
        if not tokens:
            out.append(step)
            continue
        verb = tokens[0].lower().replace("_", "-")
        if verb in _NON_QUERY_VERBS:
            out.append(step)
            continue
        # A query step: an explicit `query …` keeps its verb; a bare selector
        # step has none. Expand the selector tokens (everything after `query`).
        prefix = tokens[:1] if verb == "query" else []
        selector_tokens = tokens[len(prefix) :]
        expanded: list[str] = []
        for tok in selector_tokens:
            expanded.extend(_expand_token(tok, groups))
        out.append(" ".join([*prefix, *expanded]))
    return out
