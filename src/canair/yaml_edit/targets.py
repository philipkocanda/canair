"""The router: which YAML file and section each kind of profile edit belongs to.

Every editor works on one :class:`~canair.yaml_edit.doc.Target`, declared next to the rules it
enforces (the groups target beside the group-name rule, the states target beside the predicate
check). This module is where they are all listed, so there is one place to see what the profile
lets a command edit, to open any of them by name, and for a test to check that none was forgotten.

====================  ==========================  ==================  =========================
target                file                        section             edited by
====================  ==========================  ==================  =========================
``groups``            ``groups.yaml``             ``groups:``         :mod:`.groups`
``actions``           ``actions.yaml``            ``actions:``        :mod:`.actions`
``states``            ``vehicle_states.yaml``     ``states:``         :mod:`.states`
``variants``          ``variants.yaml``           ``variants:``       :mod:`.variants`
``signals``           ``signals/<bus>.yaml``      ``messages:``       :mod:`.signals`
``ecu``               ``ecus/<name>.yaml``        the ECU's mapping   :mod:`.ecu`, :mod:`.revisions`
====================  ==========================  ==================  =========================

The ECU files have a second engine, text surgery on one block of the file
(:mod:`canair.yaml_edit.text`, :mod:`canair.yaml_edit.pids`), for edits that must leave every
other byte alone. It reads and writes the same files and ends in the same
:func:`~canair.yaml_edit.commit.commit_text`, so both judge a file the same way.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from canair.profile import Profile

    from .doc import Doc, Target


def targets() -> dict[str, Target]:
    """Every registered target by key (imported on first use: the editors are heavy)."""
    from . import actions, ecu, groups, signals, states, variants

    found = (
        groups.GROUPS,
        actions.ACTIONS,
        states.STATES,
        variants.VARIANTS,
        signals.SIGNALS,
        ecu.ECU,
    )
    return {t.key: t for t in found}


def target(key: str) -> Target:
    """The target registered as ``key``."""
    try:
        return targets()[key]
    except KeyError:
        raise KeyError(f"no edit target {key!r} (known: {', '.join(targets())})") from None


def open_target(key: str, profile: Profile, **kwargs: Any) -> AbstractContextManager[Doc]:
    """:func:`~canair.yaml_edit.doc.edit_doc` for the target registered as ``key``."""
    from .doc import edit_doc

    return edit_doc(target(key), profile, **kwargs)
