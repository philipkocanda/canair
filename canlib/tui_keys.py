"""The canonical keymap shared by every canair TUI.

canair has four full-screen Textual apps (``monitor``, ``captures --step``,
``decode --plot``, ``sniff``) plus a dozen modal dialogs. They grew
independently, so the same key came to mean different things in each — ``a``
added PIDs in the stepper while the monitor used ``p``, ``escape`` quit one app
and cleared a selection in another, ``l`` was both vim-right and "open the event
log". A key that means two things is worse than no key at all: muscle memory
built in one view misfires in the next.

This module is the single home for **what a key means**, so an app can no longer
invent its own meaning. Each :class:`KeyRole` names one semantic role, the key(s)
that trigger it (first is canonical, the rest are hidden aliases) and the help
text it is advertised with. Apps never write a key string — they call
:func:`bind`, which expands a role into the right ``Binding`` objects.

``tests/test_tui_keymap.py`` walks every app and modal in ``canlib`` and asserts
each declared key resolves to a role and is described in that role's words, so a
new binding cannot quietly reintroduce a collision.

Two conventions worth knowing because they are not obvious from the table:

- **``escape`` never quits.** It backs out one level (close a modal, clear a
  selection) and is a no-op at the top level; ``q`` is the only way out. An
  escape that sometimes exits the program is the one binding users cannot
  safely press to find out what it does.
- **``g``/``G`` address the app's primary axis**, which is not always vertical:
  the monitor's is its row list (top/bottom), the stepper's is the frame
  timeline (first/last frame). That is why the role allows both wordings.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from textual.binding import Binding


@dataclass(frozen=True)
class KeyRole:
    """One semantic action, its keys, and the words it is advertised with.

    ``keys[0]`` is canonical and shown in the footer/cheat-sheet; the rest are
    hidden aliases (the vim/arrow pairs, ``+`` next to ``=``). ``alt`` holds
    additional accepted descriptions for the few roles whose *subject* legitimately
    differs per app — ``next`` steps a frame in the stepper and a byte offset in
    the plot explorer — so the keymap test can stay strict about the rest.
    """

    role: str
    keys: tuple[str, ...]
    description: str
    alt: frozenset[str] = field(default_factory=frozenset)

    @property
    def descriptions(self) -> frozenset[str]:
        return frozenset({self.description}) | self.alt


def _r(role: str, keys: str, description: str, *alt: str) -> KeyRole:
    return KeyRole(role, tuple(keys.split()), description, frozenset(alt))


# The keymap. Grouped by concern; the order here is also the order an app's
# BINDINGS should follow, so every cheat-sheet reads the same way.
ROLES: dict[str, KeyRole] = {
    r.role: r
    for r in (
        # -- always available ----------------------------------------------
        _r("quit", "q", "quit"),
        _r("quit_force", "ctrl+c", "quit"),
        _r("help", "question_mark", "help"),
        _r("back", "escape", "back", "cancel"),
        # -- moving around -------------------------------------------------
        _r("move_down", "j down", "down", "scroll down", "select down"),
        _r("move_up", "k up", "up", "scroll up", "select up"),
        _r("next", "right l", "next", "next frame", "next offset/param"),
        _r("prev", "left h", "prev", "prev frame", "prev offset/param"),
        _r("page_next", "right_square_bracket", "page forward", "+100 frames"),
        _r("page_prev", "left_square_bracket", "page back", "-100 frames"),
        _r("axis_start", "g", "start", "top", "first frame"),
        _r("axis_end", "G", "end", "bottom", "last frame"),
        _r("goto", "colon", "goto", "goto frame"),
        _r("block_next", "tab", "next block"),
        _r("block_prev", "shift+tab", "prev block"),
        # -- choosing what is shown ----------------------------------------
        _r("pick", "p a", "add/remove signals", "switch PID"),
        _r("filter", "slash", "filter"),
        _r("filter_cycle", "F", "cycle filter"),
        _r("view", "V", "view mode"),
        _r("rulers", "r", "rulers", "byte ruler"),
        _r("info", "i", "info", "session info", "captures in view"),
        _r("unique", "u", "unique/all"),
        _r("overlay", "o", "overlay ref"),
        _r("mode", "m", "bytes/param mode"),
        _r("transform", "f", "transform"),
        _r("byte_order", "b", "byte order"),
        _r("value_type", "t", "type", "session type"),
        _r("value_type_prev", "T", "type -"),
        _r("join_tol", "J", "join tolerance"),
        _r("nudge_down", "comma", "nudge -", "pan left", "tighter tolerance"),
        _r("nudge_up", "full_stop", "nudge +", "pan right", "wider tolerance"),
        _r("increase", "equals_sign plus", "increase", "poll faster", "zoom in"),
        _r("decrease", "minus underscore", "decrease", "poll slower", "zoom out"),
        _r("reset", "0", "reset"),
        _r("pause", "space", "pause"),
        # -- acting on the focused thing -----------------------------------
        _r("edit", "e", "edit", "edit note", "annotate"),
        _r("rename", "N", "rename"),
        _r("verify", "v", "verify"),
        _r("exclude", "x", "exclude", "en/disable", "drop this PID"),
        _r("delete", "d", "delete", "delete capture"),
        # -- the session / the device --------------------------------------
        _r("session", "s", "session", "save / label", "sessions & notes"),
        _r("new", "n", "new", "new session"),
        _r("event_log", "E", "errors/log"),
        _r("reconnect", "R", "reconnect"),
        _r("clear", "c", "clear"),
        # -- dialogs -------------------------------------------------------
        _r("apply", "ctrl+s", "apply", "save"),
        _r("confirm_yes", "y", "yes"),
        _r("confirm_no", "n", "no"),
        _r("notes_only", "ctrl+n", "notes only"),
    )
}

#: ``key -> roles`` reverse index. A key normally has exactly one role; the sole
#: deliberate overlap is ``n`` ("new" in an app, "no" in a yes/no dialog, which is
#: near-universal terminal convention and confined to :class:`ConfirmModal`).
KEY_ROLES: dict[str, tuple[KeyRole, ...]] = {}
for _role in ROLES.values():
    for _key in _role.keys:
        KEY_ROLES[_key] = (*KEY_ROLES.get(_key, ()), _role)

#: Accepted as a description for *any* role. A modal is conventionally dismissed
#: by the same key that opened it (``i`` closes the session info it opened), and
#: "close" is the honest word for that binding regardless of the key's role.
DISMISS_DESCRIPTION = "close"


def bind(
    role: str,
    action: str,
    *,
    desc: str | None = None,
    show: bool = True,
    priority: bool = False,
) -> list[Binding]:
    """Expand a keymap *role* into its ``Binding`` objects.

    The canonical key carries the description (unless ``show=False``); the
    aliases are always hidden, so ``j``/``down`` collapse to one cheat-sheet row
    instead of two. ``desc`` overrides the advertised words for the roles whose
    subject is app-specific and must be one of the role's accepted alternatives —
    the keymap test enforces that, which is what stops a role from being reused
    for an unrelated action.
    """
    entry = ROLES[role]
    description = desc or entry.description
    return [
        Binding(key, action, description, show=show and i == 0, priority=priority)
        for i, key in enumerate(entry.keys)
    ]


def keys_for(role: str) -> tuple[str, ...]:
    """The keys bound to *role* — for a hint line that must not drift."""
    return ROLES[role].keys
