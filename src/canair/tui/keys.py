"""The canonical keymap shared by every canair TUI.

canair has seven full-screen Textual apps (``monitor``, the monitor's ECU/PID picker,
``captures --step``, ``decode --plot``, ``sniff``, ``io``, ``routines``) plus a dozen modal
dialogs. They grew independently, so the same key came to mean different things in each —
``a`` added PIDs in the stepper while the monitor used ``p``, ``escape`` quit one app and
cleared a selection in another, ``l`` was both vim-right and "open the event log". A key that
means two things is worse than no key at all: muscle memory built in one view misfires in the
next.

This module is the single home for **what a key means**, so an app can no longer
invent its own meaning. Each :class:`KeyRole` names one semantic role, the key(s)
that trigger it (first is canonical, the rest are hidden aliases) and the help
text it is advertised with. Apps never write a key string — they call
:func:`bind`, which expands a role into the right ``Binding`` objects, and they never
write a hint such as ``esc cancel`` either: :func:`hint` builds it from the role, so the
key and its words cannot drift from the binding.

``tests/contracts/test_tui_keymap.py`` walks every app and modal in ``canair`` and asserts
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
from typing import TYPE_CHECKING

from canair.render.glyphs import SEP

if TYPE_CHECKING:
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
        _r("quit_force", "ctrl+c ctrl+q", "quit"),
        _r("help", "question_mark", "help"),
        _r("back", "escape", "back", "cancel", "deselect"),
        # The application shell (tui/shell/): the menu bar and the command palette. A menu's
        # mnemonic (alt+ its underlined letter) is derived from its title by the menu bar,
        # because the letters belong to each app's menus; it is not a role.
        _r("menu", "f10", "menu"),
        _r("palette", "ctrl+p", "commands"),
        # -- moving around -------------------------------------------------
        _r("move_down", "j down", "down", "scroll down", "select down", "scroll", "select"),
        _r("move_up", "k up", "up", "scroll up", "select up", "scroll", "select"),
        _r("next", "right l", "next", "next frame", "next offset/signal"),
        _r("prev", "left h", "prev", "prev frame", "prev offset/signal"),
        _r("page_next", "right_square_bracket", "page forward", "+100 frames"),
        _r("page_prev", "left_square_bracket", "page back", "-100 frames"),
        _r("axis_start", "g", "start", "top", "first frame"),
        _r("axis_end", "G", "end", "bottom", "last frame"),
        _r("goto", "colon", "goto", "goto frame"),
        _r("block_next", "tab", "next block", "next pane"),
        _r("block_prev", "shift+tab", "prev block", "prev pane"),
        # -- choosing what is shown ----------------------------------------
        _r("pick", "p a", "add/remove signals", "compare PIDs", "switch PID", "select ECUs/PIDs"),
        _r("filter", "slash", "filter"),
        _r("select_all", "ctrl+a", "select all/none"),
        _r("filter_cycle", "F", "cycle filter"),
        _r("view", "V", "view mode"),
        _r("rulers", "r", "rulers", "byte ruler"),
        _r("info", "i", "info", "session info", "captures in view"),
        _r("unique", "u", "unique/all"),
        _r("overlay", "o", "overlay ref"),
        _r("mode", "m", "bytes/signal mode", "poll schedule"),
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
        _r("pause", "space", "pause", "toggle"),
        # -- acting on the focused thing -----------------------------------
        _r("edit", "e", "edit", "edit note", "annotate"),
        _r("rename", "N", "rename", "edit label"),
        _r("verify", "v", "verify"),
        _r("exclude", "x", "exclude", "toggle ship", "drop this PID"),
        _r("delete", "d", "delete", "delete capture"),
        _r("promote", "P", "promote"),
        # -- actuators ---------------------------------------------------------
        _r("actuators", "A", "actuators"),
        _r("actuate_off", "O", "send OFF"),
        _r("set_value", "w", "set value"),
        _r("start_routine", "exclamation_mark", "start routine"),
        _r("stop_routine", "S", "stop routine"),
        # -- the session / the device --------------------------------------
        _r(
            "session",
            "s",
            "session",
            "save / label",
            "save",
            "label",
            "snapshot",
            "sessions & notes",
        ),
        _r("new", "n", "new", "new session"),
        _r("event_log", "E", "errors/log"),
        _r("reconnect", "R", "reconnect"),
        _r("clear", "c", "clear"),
        # -- dialogs -------------------------------------------------------
        _r(
            "accept",
            "enter",
            "accept",
            "next / start",
            "next / apply",
            "next",
            "start",
            "apply",
            "toggle",
            "request results",
        ),
        _r("apply", "ctrl+s", "apply", "next / start", "next / apply"),
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
    from textual.binding import Binding

    entry = ROLES[role]
    description = desc or entry.description
    return [
        Binding(key, action, description, show=show and i == 0, priority=priority)
        for i, key in enumerate(entry.keys)
    ]


def keys_for(role: str) -> tuple[str, ...]:
    """The keys bound to *role* — for a hint line that must not drift."""
    return ROLES[role].keys


#: The keys of the roles, written the way the cli-tui-standards skill writes them. A table
#: here (not Textual's own) keeps this module free of Textual until a binding is built.
_KEY_LABELS = {
    "escape": "esc",
    "enter": "enter",
    "question_mark": "?",
    "up": "↑",
    "down": "↓",
    "left": "←",
    "right": "→",
    "pageup": "pgup",
    "pagedown": "pgdn",
    "colon": ":",
    "exclamation_mark": "!",
    "slash": "/",
    "comma": ",",
    "full_stop": ".",
    "equals_sign": "=",
    "plus": "+",
    "minus": "-",
    "underscore": "_",
    "left_square_bracket": "[",
    "right_square_bracket": "]",
}


def display_key(key: str) -> str:
    """A Textual key name as the skill writes it: ``escape`` → ``esc``, ``up`` → ``↑``."""
    if key in _KEY_LABELS:
        return _KEY_LABELS[key]
    if "_" in key and "+" not in key:
        from textual.keys import format_key

        return format_key(key)
    return key


class Token(str):
    """One built hint, such as ``esc cancel``. :func:`hint` joins these without rewording."""

    __slots__ = ()


def _described(entry: KeyRole, desc: str | None) -> str:
    if desc is None:
        return entry.description
    if desc not in entry.descriptions and desc != DISMISS_DESCRIPTION:
        raise ValueError(f"{desc!r} is not a description of role {entry.role!r}")
    return desc


def key_hint(role: str, desc: str | None = None, *, key: str | None = None) -> Token:
    """The hint for *role*: its canonical key (or the alias *key*) and its words.

    ``desc`` must be one of the role's accepted descriptions, like :func:`bind`'s.
    """
    entry = ROLES[role]
    if key is not None and key not in entry.keys:
        raise ValueError(f"{key!r} is not a key of role {role!r}")
    return Token(f"{display_key(key or entry.keys[0])} {_described(entry, desc)}")


#: The pair of arrow keys as the skill writes them.
ARROWS = f"{display_key('up')}/{display_key('down')}"


def arrows_hint(desc: str) -> Token:
    """``↑/↓ <desc>`` for the pair of movement roles (``select``, ``scroll``)."""
    up, down = ROLES["move_up"], ROLES["move_down"]
    words = _described(up, desc), _described(down, desc)
    assert words[0] == words[1]
    return Token(f"{ARROWS} {words[0]}")


def side_hint(desc: str) -> Token:
    """``←/→ <desc>`` for the pair of ``prev``/``next`` roles ("frame", "offset/signal")."""
    prev, nxt = ROLES["prev"], ROLES["next"]
    _described(prev, f"prev {desc}")
    _described(nxt, f"next {desc}")
    return Token(f"{display_key('left')}/{display_key('right')} {desc}")


def hint(*items: str, sep: str = f" {SEP} ") -> str:
    """A hint line from role names and built :class:`Token` hints: ``esc cancel · ctrl+s apply``."""
    return sep.join(i if isinstance(i, Token) else key_hint(i) for i in items)


#: Hints that several screens share.
CANCEL_HINT = key_hint("back", "cancel")
CLOSE_HINT = key_hint("back", DISMISS_DESCRIPTION)
PAGE_HINT = Token(f"{display_key('pageup')}/{display_key('pagedown')} page")
