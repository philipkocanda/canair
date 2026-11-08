"""Cross-TUI keymap consistency — one key, one meaning, everywhere.

canair's five Textual apps and their modals grew independently and drifted: ``a``
added PIDs in the stepper while the monitor used ``p``, ``escape`` quit one app
and cleared a selection in another, ``l`` was both vim-right and "open the event
log". :mod:`canair.tui.keys` is now the single home for what a key means; these
tests walk every ``App``/``Screen`` in ``canair`` and fail when a binding
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
from textual.screen import ModalScreen, Screen
from textual.widget import Widget

import canair
from canair.tui.keys import DISMISS_DESCRIPTION, KEY_ROLES, ROLES, bind, keys_for

# `n` is "new" in an app and "no" in a yes/no dialog. The second is near-universal
# terminal convention and confined to ConfirmModal, so it is the one sanctioned
# overlap rather than a hole in the keymap.
_ALLOWED_MULTI_ROLE_KEYS = {"n"}


def _tui_classes() -> list[type]:
    """Every Textual ``App``/``Screen`` subclass defined in ``canair``, and every widget with keys.

    A widget that declares its own ``BINDINGS`` (a monitor pane) owns those keys while it has
    focus, so they follow the same keymap as an app's.

    Discovered by import rather than listed, so a new TUI is covered the moment
    it exists — a hand-maintained list would be exactly as driftable as the
    keymap it is meant to police.
    """
    found: dict[str, type] = {}
    for mod_info in pkgutil.walk_packages(canair.__path__, prefix="canair."):
        try:
            module = importlib.import_module(mod_info.name)
        except Exception:  # optional/heavy deps are not this test's business
            continue
        for _name, obj in inspect.getmembers(module, inspect.isclass):
            if obj.__module__ != mod_info.name:
                continue
            own_keys = issubclass(obj, Widget) and "BINDINGS" in obj.__dict__
            if (issubclass(obj, App | Screen) or own_keys) and obj not in (
                App,
                Screen,
                ModalScreen,
            ):
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
    assert {"MonitorApp", "CapturesStepApp", "PlotApp", "SniffApp", "MonitorSelectApp"} <= names
    assert {"HelpModal", "ConfirmModal", "TextPromptModal", "SaveDialog"} <= names
    assert {"MonitorSelectScreen", "LiveMonitorSelectScreen", "ListPickerModal"} <= names
    assert "ValuesPane" in names


def test_every_declared_key_is_in_the_keymap():
    """A binding may not invent a key: it must resolve to a :mod:`tui_keys` role."""
    unknown = [(cls.__name__, b.key, b.action) for cls, b in _declared() if b.key not in KEY_ROLES]
    assert not unknown, (
        "keys not in canair.tui.keys.ROLES — add a role there (or reuse one) "
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
    from canair.tui.apps.captures_step.app import CapturesStepApp
    from canair.tui.apps.decode_plot.app import PlotApp
    from canair.tui.apps.monitor.app import MonitorApp

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


def _modals() -> list[type]:
    return [c for c in _tui_classes() if issubclass(c, ModalScreen)]


def _apps() -> list[type]:
    return [c for c in _tui_classes() if issubclass(c, App)]


def _resolves(cls: type, key: str) -> list[str]:
    """The actions *cls* binds *key* to once its bases are merged, as Textual resolves them."""
    merged = cls._merged_bindings  # type: ignore[attr-defined]
    return [b.action for b in merged.key_to_bindings.get(key, ())]


def test_help_resolves_on_every_modal():
    """``?`` opens help on every dialog, because Textual cuts a modal off from the app's keys.

    Where a text field has focus ``?`` is text, so the binding is still declared and fires
    once focus is anywhere else.
    """
    for key in keys_for("help"):
        wrong = {
            c.__name__: _resolves(c, key)
            for c in _modals()
            if c.__name__ != "HelpModal" and "help" not in _resolves(c, key)
        }
        assert not wrong, f"{key} must open help on a modal: {wrong}"
    assert _resolves(_by_name("HelpModal"), "question_mark") == ["close"]


def _by_name(name: str) -> type:
    return next(c for c in _tui_classes() if c.__name__ == name)


def test_the_inherited_force_quit_is_a_declared_role():
    """Textual gives every app ``ctrl+q``; each canair app declares it through ``quit_force``."""
    assert set(keys_for("quit_force")) == {"ctrl+c", "ctrl+q"}
    missing = [
        c.__name__
        for c in _apps()
        if not {b.key for b in _bindings(c)} >= set(keys_for("quit_force"))
    ]
    assert not missing, f"apps that leave Textual's own ctrl+q in place: {missing}"


def test_every_top_level_app_declares_escape_as_inert():
    """``escape`` is bound, and never quits, in the four apps that have a top level."""
    for name in ("MonitorApp", "CapturesStepApp", "PlotApp", "SniffApp"):
        actions = [b.action for b in _bindings(_by_name(name)) if b.key == "escape"]
        assert actions, f"{name} does not bind escape"
        assert not any("quit" in a for a in actions)


def test_read_only_overlays_close_on_q():
    """``q`` closes help, the event log and the session info rather than the app below."""
    for name in ("HelpModal", "EventLogModal", "SessionInfoModal"):
        assert _resolves(_by_name(name), "q") == ["close"], name


def test_a_modal_never_takes_a_printable_key_with_priority():
    """A priority binding on a typed character would swallow it from a focused text field."""
    offenders = [
        (cls.__name__, b.key)
        for cls in _modals()
        for b in _bindings(cls)
        if b.priority and len(b.key) == 1
    ]
    assert not offenders, offenders
