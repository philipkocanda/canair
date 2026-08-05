#!/usr/bin/env python3
"""State + rendering model for the ``captures --step`` viewer.

Framework-free by design: :class:`StepModel` owns everything the step view *is*
(the selected (ECU, PID) keys, the join tolerance, the view mode, the frame and
block cursors) and renders a frame to a :class:`rich.text.Text`. The Textual app
(:mod:`step_tui`) is a thin shell over it, and the piped/JSON paths use
the same model with no Textual involved — so the capability is never
TTY-only.

Two navigation shapes share one model:

* **stacked** (``stacked``/``signals``/``changed``) — one frame per *joined*
  moment, with one block per selected key (see
  :func:`~canlib.commands.captures.query.build_join_frames`). This is the
  cross-compare view: several PIDs underneath each other at the same instant.
* **interleaved** — one frame per capture, chronologically across the selected
  keys. Better for browsing many PIDs, where stacking would not fit a screen.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.text import Text

from canlib.capture_dates import entry_datetime
from canlib.capture_types import CaptureEntry
from canlib.states import join_states as _join_states

from .join import JoinFrame, build_join_frames
from .query import (
    PidDefs,
    _capture_key,
    _dedupe_payloads,
    _is_hex_payload,
    _key_ordinals,
    _load_ecu_index,
    _prev_same_index,
    _resolve_defs,
    group_sessions,
    key_index,
    load_all_captures,
)
from .step_render import (
    capture_block_text,
    frame_header_text,
    key_label,
    missing_block_text,
    separator_text,
)

# View vocabulary. ``auto`` is resolved at construction time and never stored.
VIEW_AUTO = "auto"
VIEW_STACKED = "stacked"
VIEW_SIGNALS = "signals"
VIEW_CHANGED = "changed"
VIEW_INTERLEAVED = "interleaved"

#: Selectable views, in the order ``V`` cycles them.
VIEWS = (VIEW_STACKED, VIEW_SIGNALS, VIEW_CHANGED, VIEW_INTERLEAVED)
#: Values accepted by ``--view``.
VIEW_CHOICES = (VIEW_AUTO, *VIEWS)

# Above this many selected keys, ``auto`` prefers the interleaved walk: stacking
# every PID of a whole ECU produces a frame no terminal can show usefully.
AUTO_STACK_MAX_KEYS = 6

#: Tolerance ladder stepped by ``<``/``>`` in the TUI (seconds).
TOL_LADDER = (0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 300.0)

#: Default join window for the stepper — deliberately wider than the shared
#: :data:`canlib.align.DEFAULT_JOIN_TOL_S` (5s) used by
#: ``align``/``correlate``/``hunt``.
#:
#: A full round-robin ``monitor`` cycle over several ECUs routinely spans ~8-10s,
#: so two PIDs polled in the *same* cycle can sit further apart than 5s and would
#: not be joined — the frames split, and the comparison the view exists for
#: disappears. The stepper can afford the wider window because it is a *viewer*:
#: every block reports its own ``Δt`` from the anchor, so an over-wide join is
#: visible and self-correcting. The statistics tools cannot — a loose pairing
#: silently changes a correlation coefficient — which is why they keep the
#: tighter shared default.
DEFAULT_STEP_JOIN_TOL_S = 10.0

# Frames skipped by a "page" jump.
PAGE_JUMP = 100

#: A capture's immutable identity: ``(file, session_idx, capture_idx)``. Indices
#: into :attr:`StepModel.captures` are invalidated by every rebuild, so anything
#: that outlives a rebuild (a jump target) addresses captures by this instead.
CaptureRef = tuple[str, int, int]


def capture_ref(entry: Mapping[str, Any]) -> CaptureRef:
    """The ``(file, session_idx, capture_idx)`` locator of a capture entry."""
    return (
        str(entry.get("file", "")),
        int(entry.get("_session_idx", 0)),
        int(entry.get("_capture_idx", 0)),
    )


# Why a jump target can't be shown, or "" when it can. Kept short: they are
# rendered inline in the jump list, where the row budget is tight.
BLOCK_NON_PAYLOAD = "no hex payload"
BLOCK_NO_FRAME = "not in this selection"


@dataclass(frozen=True)
class JumpTarget:
    """One row of the session/note jump list.

    A *session* row (``ref is None``) jumps to that session's first frame; a
    *note* row jumps to the noted capture. ``blocked`` is empty when the row is
    reachable, else the reason it isn't (the row is shown disabled — a note the
    stepper cannot display is still worth surfacing).
    """

    session: tuple[str, int]
    is_note: bool
    label: str
    detail: str = ""
    ref: CaptureRef | None = None
    key: tuple[str, str] | None = None
    blocked: str = ""

    @property
    def searchable(self) -> str:
        """Lower-cased text the modal's filter matches against."""
        return f"{self.label} {self.detail}".lower()


@dataclass(frozen=True)
class JumpList:
    """The jump modal's rows, plus what was left out of them.

    A session offering nothing for the current comparison — no frame *and* no
    notes — is omitted rather than listed as unreachable: those rows are pure
    noise when the point is to navigate to *relevant* captures. So is a note the
    current view cannot place. Both counts are carried here so the footer can
    report them instead of them vanishing silently.
    """

    rows: list[JumpTarget]
    hidden_sessions: int = 0
    hidden_notes: int = 0


def _one_line(text: str) -> str:
    """Collapse whitespace so a multi-line note fits a single list row."""
    return " ".join(str(text).split())


def resolve_view(view: str, n_keys: int) -> str:
    """Resolve ``auto`` to a concrete view for ``n_keys`` selected keys."""
    if view != VIEW_AUTO:
        return view
    return VIEW_STACKED if n_keys <= AUTO_STACK_MAX_KEYS else VIEW_INTERLEAVED


@dataclass
class StepModel:
    """Everything the step view shows, and the cursors into it.

    Construct via :meth:`from_entries` (which resolves ``auto`` and does the
    first :meth:`rebuild`) rather than directly.
    """

    index: dict[tuple[str, str], list[CaptureEntry]]
    keys: list[tuple[str, str]]
    defs: dict[tuple[str, str], PidDefs]
    view: str = VIEW_STACKED
    tol_s: float = DEFAULT_STEP_JOIN_TOL_S
    show_all: bool = False
    rulers: bool = False
    captures_dir: Path | None = None
    aliases: dict[str, str] = field(default_factory=dict)
    #: Draw the block cursor. Off for the static/piped render, where there is
    #: nothing to focus and a ``▶`` would only imply interactivity.
    cursor: bool = True
    #: Every scoped entry, including the ones no view can render (non-payload
    #: captures). Kept so the jump list can show the *whole* recording history —
    #: a note the stepper cannot display is still worth telling the user about.
    entries: list[CaptureEntry] = field(default_factory=list)

    # Derived by rebuild().
    captures: list[CaptureEntry] = field(default_factory=list)
    prev_idx: list[int | None] = field(default_factory=list)
    ordinals: list[tuple[int, int]] = field(default_factory=list)
    frames: list[JoinFrame] = field(default_factory=list)
    n_no_time: int = 0
    #: Capture locator -> its index in :attr:`captures`.
    _at: dict[CaptureRef, int] = field(default_factory=dict)
    #: Index in :attr:`captures` -> the first frame showing it.
    _frame_of: dict[int, int] = field(default_factory=dict)

    # Cursors.
    frame_idx: int = 0
    block_idx: int = 0

    _ecu_index: dict = field(default_factory=dict)

    # -- construction ------------------------------------------------------

    @classmethod
    def from_entries(
        cls,
        entries: Sequence[CaptureEntry],
        keys: list[tuple[str, str]],
        defs: dict[tuple[str, str], PidDefs],
        *,
        view: str = VIEW_AUTO,
        tol_s: float = DEFAULT_STEP_JOIN_TOL_S,
        show_all: bool = False,
        rulers: bool = False,
        captures_dir: Path | None = None,
        aliases: dict[str, str] | None = None,
    ) -> StepModel:
        model = cls(
            index=key_index(entries),
            keys=sorted(keys),
            defs=dict(defs),
            view=resolve_view(view, len(keys)),
            tol_s=tol_s,
            show_all=show_all,
            rulers=rulers,
            captures_dir=captures_dir,
            aliases=dict(aliases or {}),
            entries=list(entries),
        )
        model.rebuild()
        model.last()
        return model

    # -- derived state -----------------------------------------------------

    @property
    def stacked(self) -> bool:
        """True when frames stack one block per key (i.e. not the interleaved walk)."""
        return self.view != VIEW_INTERLEAVED

    def rebuild(self, *, preserve_time: bool = False) -> None:
        """Re-select captures for the current keys and re-join them.

        ``preserve_time`` re-seeks the frame cursor to the same point on the
        timeline afterwards, so changing the tolerance or the key set does not
        throw the user back to the end of the recording.
        """
        anchor = self.current_time() if preserve_time else None

        caps: list[CaptureEntry] = []
        for k in self.keys:
            caps.extend(self.index.get(k, ()))
        caps.sort(key=lambda e: (str(e.get("date", "")), str(e.get("time", ""))))
        if not self.show_all:
            caps = _dedupe_payloads(caps)

        self.captures = caps
        self.prev_idx = _prev_same_index(caps)
        self.ordinals = _key_ordinals(caps)
        self.frames, self.n_no_time = build_join_frames(caps, self.keys, self.tol_s)
        self._reindex()

        self.frame_idx = min(self.frame_idx, max(self.frame_count() - 1, 0))
        self.block_idx = min(self.block_idx, max(len(self.keys) - 1, 0))
        if anchor is not None:
            self.seek_time(anchor)

    def _reindex(self) -> None:
        """Rebuild the locator -> capture -> frame lookups the jump list needs.

        Capture *indices* are invalidated by every rebuild, so a jump target is
        addressed by its immutable ``(file, session, capture)`` locator and
        resolved through here instead.
        """
        self._at = {capture_ref(e): i for i, e in enumerate(self.captures)}
        frame_of: dict[int, int] = {}
        if self.stacked:
            for n, frame in enumerate(self.frames):
                for idx in frame.indices:
                    if idx is not None:
                        frame_of.setdefault(idx, n)
        else:
            frame_of = {i: i for i in range(len(self.captures))}
        self._frame_of = frame_of

    def reload_from_disk(self) -> bool:
        """Re-read captures from disk (after a note edit / delete). False if empty."""
        entries = load_all_captures(self.captures_dir)
        self.entries = entries
        self.index = key_index(entries)
        self.rebuild(preserve_time=True)
        return bool(self.captures)

    def frame_count(self) -> int:
        return len(self.frames) if self.stacked else len(self.captures)

    def is_empty(self) -> bool:
        return self.frame_count() == 0

    def frame_indices(self, n: int | None = None) -> tuple[int | None, ...]:
        """Capture indices for frame ``n`` (default: current), one slot per key.

        The interleaved view fills only the slot of that capture's own key, so
        block-scoped actions work identically in both views.
        """
        if self.is_empty():
            return tuple(None for _ in self.keys)
        i = self.frame_idx if n is None else n
        if self.stacked:
            return self.frames[i].indices
        cur = _capture_key(self.captures[i])
        return tuple(i if k == cur else None for k in self.keys)

    def frame_time(self, n: int | None = None) -> datetime | None:
        """Anchor timestamp of frame ``n`` (default: current), or None if untimed."""
        if self.is_empty():
            return None
        i = self.frame_idx if n is None else n
        if self.stacked:
            return self.frames[i].anchor_dt
        return entry_datetime(self.captures[i])

    def current_time(self) -> datetime | None:
        """Timestamp of the current frame (its anchor), or None when empty/untimed."""
        return self.frame_time()

    def focused_capture(self) -> CaptureEntry | None:
        """The capture the block cursor points at (target of note/delete)."""
        indices = self.frame_indices()
        if not indices:
            return None
        idx = indices[min(self.block_idx, len(indices) - 1)]
        return self.captures[idx] if idx is not None else None

    def focused_key(self) -> tuple[str, str] | None:
        """The (ECU, PID) key the block cursor points at."""
        if not self.keys:
            return None
        return self.keys[min(self.block_idx, len(self.keys) - 1)]

    def available_keys(self) -> list[tuple[tuple[str, str], int]]:
        """Every (ECU, PID) with captures in scope, sorted, with its capture count."""
        return sorted((k, len(v)) for k, v in self.index.items())

    # -- jump list ---------------------------------------------------------

    def jump_targets(self) -> JumpList:
        """Session rows (newest first), each followed by its noted captures.

        Built from *all* scoped entries, so notes are surfaced even on captures
        the stepper cannot render. A session is listed when it has a frame for
        the current selection **or** carries at least one note; one that has
        neither offers nowhere to go and is counted into
        :attr:`JumpList.hidden_sessions` instead of padding the list.
        """
        rows: list[JumpTarget] = []
        hidden = 0
        hidden_notes = 0
        for g in reversed(group_sessions(self.entries)):
            sid = (g["file"], g["session_idx"])
            reachable = self._session_frame(sid) is not None
            noted = [e for e in g["noted"] if self._note_placeable(e)]
            hidden_notes += len(g["noted"]) - len(noted)
            if not reachable and not noted:
                hidden += 1
                continue
            times = [t for t in g["times"] if t]
            span = ""
            if times:
                lo, hi = min(times).split(".")[0], max(times).split(".")[0]
                span = lo if lo == hi else f"{lo}-{hi}"
            states = _join_states(g["vehicle_states"])
            notes = len(noted)
            detail = f"{g['n']} caps"
            if notes:
                detail += f" · {notes} note{'s' if notes != 1 else ''}"
            rows.append(
                JumpTarget(
                    session=sid,
                    is_note=False,
                    label=" ".join(
                        x
                        for x in (
                            g["date"],
                            span or "—",
                            f"[{states}]" if states else "",
                            _one_line(g["label"]),
                        )
                        if x
                    ),
                    detail=detail,
                    # Kept only as a grouping heading for its notes — the row is
                    # not selectable, and says nothing beyond that.
                    blocked="" if reachable else BLOCK_NO_FRAME,
                )
            )
            for e in noted:
                rows.append(self._note_target(sid, e))
        return JumpList(rows=rows, hidden_sessions=hidden, hidden_notes=hidden_notes)

    def _note_target(self, sid: tuple[str, int], e: CaptureEntry) -> JumpTarget:
        key = _capture_key(e)
        return JumpTarget(
            session=sid,
            is_note=True,
            label=_one_line(str(e.get("notes") or "")),
            detail=f"{str(e.get('time') or '—').split('.')[0]}  {key[0]}:{key[1]}",
            ref=capture_ref(e),
            key=key,
            blocked=self._note_block(e),
        )

    def _note_placeable(self, e: CaptureEntry) -> bool:
        """Whether the current view can put this noted capture on screen at all.

        A capture with no timestamp cannot be joined onto a timeline, so the
        stacked views have no frame for it — and this profile's legacy captures
        are overwhelmingly untimed, which would bury the list in rows that go
        nowhere. Such a note is omitted (and counted) rather than listed; the
        interleaved view, which needs no timestamps, still lists and reaches it.
        """
        return entry_datetime(e) is not None or not self.stacked

    def _note_block(self, e: CaptureEntry) -> str:
        """Why a *listed* noted capture can't be jumped to, or ``""``.

        Only one state survives here: a capture carrying no hex payload, which
        no view renders. It is still listed — flagged — because it is otherwise
        invisible, whereas an unplaceable note is merely a view away. A missing
        PID or a dedup-hidden payload is *not* blocking: the jump resolves those
        by adjusting the selection (see :meth:`seek_capture`).
        """
        return "" if _is_hex_payload(e.get("payload")) else BLOCK_NON_PAYLOAD

    def _session_frame(self, sid: tuple[str, int]) -> int | None:
        """The earliest frame showing a capture from that session, if any."""
        best: int | None = None
        for i, e in enumerate(self.captures):
            if (e.get("file", ""), e.get("_session_idx", 0)) != sid:
                continue
            n = self._frame_of.get(i)
            if n is not None and (best is None or n < best):
                best = n
        return best

    def seek_session(self, sid: tuple[str, int]) -> str:
        """Jump to a session's first frame. Returns a status message."""
        n = self._session_frame(sid)
        if n is None:
            return f"No frame for session {sid[0]}#{sid[1]} in this selection"
        self.frame_idx = n
        return f"Jumped to {sid[0]}#{sid[1]}"

    def seek_capture(self, ref: CaptureRef, key: tuple[str, str] | None = None) -> str:
        """Jump to a specific capture, adjusting the view to make it visible.

        The point of jumping to a noted capture is to *see* it, so two
        obstructions are resolved rather than reported: a PID that isn't in the
        comparison is added, and unique-payload dedup is lifted when it hid the
        target. Both are named in the returned status so nothing changes
        silently (``x`` and ``u`` undo them).
        """
        changed: list[str] = []
        if key is not None and key not in self.keys and key in self.index:
            self.set_keys([*self.keys, key])
            changed.append(f"added {key[0]}:{key[1]}")
        if ref not in self._at and not self.show_all:
            self.show_all = True
            self.rebuild()
            changed.append("all payloads on")

        idx = self._at.get(ref)
        if idx is None:
            return "That capture is not in view" + (f" ({', '.join(changed)})" if changed else "")
        n = self._frame_of.get(idx)
        if n is None:
            return "That capture has no frame (untimed)"
        self.frame_idx = n
        self.block_idx = self._block_of(n, idx)
        return "Jumped to note" + (f" — {', '.join(changed)}" if changed else "")

    def _block_of(self, frame: int, idx: int) -> int:
        """The block slot showing capture ``idx`` in frame ``frame``."""
        indices = self.frame_indices(frame)
        for slot, at in enumerate(indices):
            if at == idx:
                return slot
        return self.block_idx

    # -- navigation --------------------------------------------------------

    def advance(self, delta: int) -> str:
        """Move the frame cursor by ``delta``; returns a status note when clamped."""
        n = self.frame_count()
        if n == 0:
            return ""
        target = self.frame_idx + delta
        if target < 0:
            clamped = self.frame_idx == 0
            self.frame_idx = 0
            return "At first frame" if clamped else ""
        if target > n - 1:
            clamped = self.frame_idx == n - 1
            self.frame_idx = n - 1
            return "At last frame" if clamped else ""
        self.frame_idx = target
        return ""

    def first(self) -> None:
        self.frame_idx = 0

    def last(self) -> None:
        self.frame_idx = max(self.frame_count() - 1, 0)

    def goto(self, n: int) -> str:
        """Jump to 1-based frame ``n``; returns a status note when clamped."""
        total = self.frame_count()
        if total == 0:
            return ""
        if 1 <= n <= total:
            self.frame_idx = n - 1
            return ""
        self.frame_idx = max(0, min(n - 1, total - 1))
        return f"Clamped to {self.frame_idx + 1} (valid: 1-{total})"

    def seek_time(self, when: datetime) -> None:
        """Move the cursor to the first frame at or after ``when``."""
        total = self.frame_count()
        if total == 0:
            self.frame_idx = 0
            return
        if self.stacked:
            times = [f.anchor_dt for f in self.frames]
        else:
            times = [entry_datetime(c) or datetime.min for c in self.captures]
        self.frame_idx = min(bisect_left(times, when), total - 1)

    def move_block(self, delta: int) -> None:
        """Move the block cursor, wrapping around the selected keys."""
        if not self.keys:
            self.block_idx = 0
            return
        self.block_idx = (self.block_idx + delta) % len(self.keys)

    # -- mutation ----------------------------------------------------------

    def set_keys(self, keys: list[tuple[str, str]]) -> None:
        """Replace the selected key set (blocks are shown sorted)."""
        self.keys = sorted(set(keys))
        for k in self.keys:
            if k not in self.defs:
                if not self._ecu_index:
                    self._ecu_index = _load_ecu_index()
                self.defs[k] = _resolve_defs(self._ecu_index, *k)
        self.rebuild(preserve_time=True)

    def remove_key(self, key: tuple[str, str]) -> bool:
        """Drop one key from the selection. False when it is the only one left."""
        if key not in self.keys or len(self.keys) <= 1:
            return False
        remaining = [k for k in self.keys if k != key]
        self.set_keys(remaining)
        self.block_idx = min(self.block_idx, len(self.keys) - 1)
        return True

    def set_tol(self, tol_s: float) -> None:
        self.tol_s = max(0.0, float(tol_s))
        self.rebuild(preserve_time=True)

    def nudge_tol(self, direction: int) -> None:
        """Step the join tolerance along :data:`TOL_LADDER`."""
        if direction > 0:
            nxt = next((t for t in TOL_LADDER if t > self.tol_s), TOL_LADDER[-1])
        else:
            nxt = next((t for t in reversed(TOL_LADDER) if t < self.tol_s), TOL_LADDER[0])
        self.set_tol(nxt)

    def cycle_view(self) -> str:
        """Advance to the next view; rebuilds when the frame shape changes."""
        was_stacked = self.stacked
        self.view = VIEWS[(VIEWS.index(self.view) + 1) % len(VIEWS)]
        if was_stacked != self.stacked:
            self.rebuild(preserve_time=True)
        return self.view

    def toggle_rulers(self) -> bool:
        self.rulers = not self.rulers
        return self.rulers

    def toggle_show_all(self) -> bool:
        """Toggle unique-payloads-only vs every capture."""
        self.show_all = not self.show_all
        self.rebuild(preserve_time=True)
        return self.show_all

    # -- rendering ---------------------------------------------------------

    def render(self, n: int | None = None) -> Text:
        """Render frame ``n`` (default: the current one)."""
        if self.is_empty():
            return Text(f"\n  {self.empty_reason()}\n", style="yellow")
        i = self.frame_idx if n is None else n
        return self._render_stacked(i) if self.stacked else self._render_interleaved(i)

    def empty_reason(self) -> str:
        """Why there is nothing to show — distinguishing 'none' from 'unplaceable'.

        Captures with no timestamp can't be joined onto a timeline, so a
        selection of only untimed captures yields no stacked frame even though
        captures exist. Saying so beats an unqualified "no captures".
        """
        if self.captures and self.n_no_time:
            return (
                f"No timestamped captures for the selected PIDs "
                f"({self.n_no_time} untimed capture(s) cannot be placed on the "
                f"timeline — try --view interleaved)."
            )
        return "No captures for the selected PIDs."

    def _render_interleaved(self, i: int) -> Text:
        return capture_block_text(
            self.captures,
            i,
            self.defs,
            self.prev_idx,
            self.ordinals,
            rulers=self.rulers,
            position=f"capture {i + 1}/{len(self.captures)}",
            aliases=self.aliases,
            show_per_pid=len(self.keys) > 1,
        )

    def _render_stacked(self, n: int) -> Text:
        frame = self.frames[n]
        anchor = frame.anchor_dt
        show_hex = self.view != VIEW_SIGNALS
        changed_only = self.view == VIEW_CHANGED

        lead = next((self.captures[i] for i in frame.indices if i is not None), None)
        out = frame_header_text(
            position=f"frame {n + 1}/{len(self.frames)}",
            timestamp=anchor.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            tol_s=self.tol_s if len(self.keys) > 1 else None,
            states=_join_states((lead or {}).get("vehicle_states")),
            label=str((lead or {}).get("session_label") or ""),
        )

        for slot, key in enumerate(self.keys):
            if slot:
                out.append(separator_text())
            selected = self.cursor and slot == self.block_idx and len(self.keys) > 1
            idx = frame.indices[slot]
            if idx is None:
                out.append(missing_block_text(key, self.tol_s, selected=selected))
                continue
            out.append(
                capture_block_text(
                    self.captures,
                    idx,
                    self.defs,
                    self.prev_idx,
                    self.ordinals,
                    rulers=self.rulers,
                    aliases=self.aliases,
                    show_hex=show_hex,
                    changed_only=changed_only,
                    selected=selected,
                    dt_label=self._dt_label(idx, anchor),
                    show_per_pid=True,
                )
            )
        return out

    def _dt_label(self, idx: int, anchor: datetime) -> str:
        """``Δt`` of a block's capture relative to the frame anchor."""
        dt = entry_datetime(self.captures[idx])
        if dt is None:
            return ""
        return f"Δt={(dt - anchor).total_seconds():+.2f}s"

    def status_line(self) -> str:
        """One-line summary of position and settings (plain text)."""
        total = self.frame_count()
        position = f"frame {self.frame_idx + 1}/{total}" if total else "no frames"
        bits = [position, f"view {self.view}"]
        if self.stacked and len(self.keys) > 1:
            bits.append(f"tol {self.tol_s:g}s")
        bits.append(f"{len(self.keys)} PID{'s' if len(self.keys) != 1 else ''}")
        bits.append("all payloads" if self.show_all else "unique payloads")
        if self.n_no_time:
            bits.append(f"{self.n_no_time} untimed excluded")
        return " · ".join(bits)

    def keys_label(self) -> str:
        """The selected keys as a compact ``HVAC:220100 HVAC:2201A0`` string."""
        return " ".join(key_label(k) for k in self.keys)

    # -- serialization -----------------------------------------------------

    def to_json(self, limit: int = 0) -> dict:
        """Frames as JSON-ready data (the non-interactive equivalent of the view).

        ``limit`` keeps only the most recent N frames (0 = all), mirroring the
        ``--limit`` cap of the list view.
        """
        from .query import _entry_to_dict

        total = self.frame_count()
        start = max(total - limit, 0) if limit > 0 else 0
        frames: list[dict] = []
        for n in range(start, total):
            indices = self.frame_indices(n)
            anchor = self.frame_time(n)
            blocks: list[dict | None] = []
            for slot, key in enumerate(self.keys):
                idx = indices[slot]
                if idx is None:
                    blocks.append(None)
                    continue
                block = _entry_to_dict(self.captures[idx])
                dt = entry_datetime(self.captures[idx])
                block["key"] = key_label(key)
                block["dt_s"] = (
                    round((dt - anchor).total_seconds(), 3)
                    if dt is not None and anchor is not None
                    else None
                )
                blocks.append(block)
            frames.append(
                {
                    "frame": n + 1,
                    "time": anchor.isoformat(sep=" ") if anchor else None,
                    "blocks": blocks,
                }
            )
        return {
            "view": self.view,
            "tol_s": self.tol_s if self.stacked else None,
            "keys": [key_label(k) for k in self.keys],
            "show_all": self.show_all,
            "frame_count": total,
            "untimed_excluded": self.n_no_time,
            "frames": frames,
        }
