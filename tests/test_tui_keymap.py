"""Cross-TUI keymap consistency — one key, one meaning, everywhere.

canair's four Textual apps and their modals grew independently and drifted: ``a``
added PIDs in the stepper while the monitor used ``p``, ``escape`` quit one app
and cleared a selection in another, ``l`` was both vim-right and "open the event
log". :mod:`canlib.tui_keys` is now the single home for what a key means; these
tests walk every ``App``/``ModalScreen`` in ``canlib`` and fail when a binding
escapes it, so the drift cannot come back one commit at a time.

They are deliberately structural (they read ``BINDINGS``, they do not drive a
terminal): a collision is a property of the declaration, and a test that has to
render a TUI to notice one would be skipped the moment it got slow.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from typing import Any

import pytest
from textual.app import App
from textual.binding import Binding
from textual.screen import ModalScreen

import canlib
from canlib.tui_keys import DISMISS_DESCRIPTION, KEY_ROLES, ROLES, bind

# `n` is "new" in an app and "no" in a yes/no dialog. The second is near-universal
# terminal convention and confined to ConfirmModal, so it is the one sanctioned
# overlap rather than a hole in the keymap.
_ALLOWED_MULTI_ROLE_KEYS = {"n"}


def _tui_classes() -> list[type]:
    """Every Textual ``App``/``ModalScreen`` subclass defined in ``canlib``.

    Discovered by import rather than listed, so a new TUI is covered the moment
    it exists — a hand-maintained list would be exactly as driftable as the
    keymap it is meant to police.
    """
    found: dict[str, type] = {}
    for mod_info in pkgutil.walk_packages(canlib.__path__, prefix="canlib."):
        try:
            module = importlib.import_module(mod_info.name)
        except Exception:  # optional/heavy deps are not this test's business
            continue
        for _name, obj in inspect.getmembers(module, inspect.isclass):
            if obj.__module__ != mod_info.name:
                continue
            if issubclass(obj, App | ModalScreen) and obj not in (App, ModalScreen):
                found[f"{obj.__module__}.{obj.__qualname__}"] = obj
    return list(found.values())


def _bindings(cls: type) -> list[Binding]:
    """The class's own ``BINDINGS`` (not inherited framework ones)."""
    raw: Any = cls.__dict__.get("BINDINGS", [])
    return [b for b in raw if isinstance(b, Binding)]


def _declared() -> list[tuple[type, Binding]]:
    return [(cls, b) for cls in _tui_classes() for b in _bindings(cls)]


def test_tui_classes_are_discovered():
    """Guard the discovery itself — a silent import failure would pass everything."""
    names = {c.__name__ for c in _tui_classes()}
    assert {"MonitorApp", "CapturesStepApp", "PlotApp", "SniffApp"} <= names
    assert {"HelpModal", "ConfirmModal", "TextPromptModal", "SaveDialog"} <= names


def test_every_declared_key_is_in_the_keymap():
    """A binding may not invent a key: it must resolve to a :mod:`tui_keys` role."""
    unknown = [(cls.__name__, b.key, b.action) for cls, b in _declared() if b.key not in KEY_ROLES]
    assert not unknown, (
        "keys not in canlib.tui_keys.ROLES — add a role there (or reuse one) "
        f"instead of hardcoding: {unknown}"
    )


def test_every_key_is_described_in_its_role_words():
    """The same key must be *advertised* the same way in every TUI.

    Descriptions are what the shared ``?`` cheat-sheet shows, so a key described
    two ways is a key that means two things. A role may allow alternative wording
    where the subject genuinely differs per app (``next frame`` vs ``next
    offset/param``); anything else is a collision.
    """
    offenders = []
    for cls, b in _declared():
        if b.key not in KEY_ROLES:
            continue  # reported by the test above
        accepted: set[str] = {DISMISS_DESCRIPTION}
        for role in KEY_ROLES[b.key]:
            accepted |= set(role.descriptions)
        if b.description not in accepted:
            offenders.append((cls.__name__, b.key, b.description, sorted(accepted)))
    assert not offenders, (
        "a key is advertised with words its keymap role does not allow — either "
        "use the role's wording, add it to the role's `alt`, or the binding "
        f"belongs to a different role: {offenders}"
    )


def test_no_key_serves_two_roles():
    """The reverse index must stay unambiguous apart from the sanctioned ``n``."""
    ambiguous = {
        key: [r.role for r in roles]
        for key, roles in KEY_ROLES.items()
        if len(roles) > 1 and key not in _ALLOWED_MULTI_ROLE_KEYS
    }
    assert not ambiguous, f"keys bound to more than one role: {ambiguous}"


def test_escape_never_quits():
    """``escape`` backs out one level; it is never an exit.

    This is the regression that mattered most: ``captures --step`` used to bind
    ``escape`` straight to ``quit`` while the monitor cleared a selection with
    it, so the same reflex either dismissed a cursor or dropped you back to the
    shell depending on which view you were in.
    """
    quitting = [
        (cls.__name__, b.action)
        for cls, b in _declared()
        if b.key == "escape" and "quit" in b.action
    ]
    assert not quitting, f"escape must not quit: {quitting}"


def test_same_action_key_across_apps():
    """The keys the user complained about, pinned per app.

    Spelled out rather than derived: the point is that *these specific* keys do
    the same thing in every view, and a derived assertion would happily pass on a
    keymap that had been renamed out from under the user.
    """
    from canlib.commands.captures.step_tui import CapturesStepApp
    from canlib.commands.decode.plot_tui import PlotApp
    from canlib.modes._monitor_tui import MonitorApp

    def keys_of(cls: type, needle: str) -> set[str]:
        return {b.key for b in _bindings(cls) if needle in b.action}

    # Adding/picking signals: was `a` in the stepper, `p` in the monitor.
    assert "p" in keys_of(MonitorApp, "pid_picker")
    assert "p" in keys_of(CapturesStepApp, "pick_pids")
    assert "p" in keys_of(PlotApp, "pick_pid")
    # `a` stays an alias everywhere it used to be the primary key, so the old
    # stepper reflex still works instead of silently doing nothing.
    assert "a" in keys_of(CapturesStepApp, "pick_pids")
    assert "a" in keys_of(MonitorApp, "pid_picker")

    # `l` is vim-right, never "open the event log" (the monitor's old meaning).
    assert keys_of(MonitorApp, "event_log") == {"E"}
    assert "l" in keys_of(CapturesStepApp, "advance(1)")
    assert "l" in keys_of(PlotApp, "move(1)")


@pytest.mark.parametrize("role", sorted(ROLES))
def test_bind_expands_to_one_shown_key(role: str):
    """Aliases are hidden, so the cheat-sheet gets one row per role, not one per key."""
    bindings = bind(role, "noop")
    assert len(bindings) == len(ROLES[role].keys)
    assert sum(1 for b in bindings if b.show) == 1
    assert bindings[0].key == ROLES[role].keys[0]


def test_bind_rejects_an_unaccepted_description():
    """``desc`` is for a role's sanctioned alternatives, not for redefining a key.

    Enforced here rather than in ``bind`` itself: raising at import time would
    turn a wording slip into a crashed CLI, while a failing test is exactly as
    loud in the only place it matters.
    """
    role = ROLES["pick"]
    assert "add/remove signals" in role.descriptions
    assert "open the event log" not in role.descriptions
