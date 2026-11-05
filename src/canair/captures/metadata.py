"""The label, states and notes a recorded session is saved with.

``--label`` answers them non-interactively. Otherwise the user is asked at the end of the run,
and a question nobody can answer (no terminal on stdin, end of input, Ctrl-C at the prompt) never
costs the recording: it is saved under the suggested label, and the note says how to remove it.
Only an empty label with no suggestion is a deliberate skip.
"""

from __future__ import annotations

import sys
from datetime import datetime

from canair.states.tokens import join_states as _join_states
from canair.states.tokens import parse_states as _parse_states

Metadata = tuple[str, list, str]


def _stdin_is_terminal() -> bool:
    isatty = getattr(sys.stdin, "isatty", None)
    return bool(isatty and isatty())


def _unanswered(
    why: str,
    suggested_label: str,
    states: list | str | None,
    notes: str,
) -> Metadata:
    """The metadata to save under when the prompt cannot be answered, and the note saying so."""
    label = suggested_label or f"Unlabelled session {datetime.now():%Y-%m-%d %H:%M}"
    print(
        f"note: {why}; saved as {label!r}. Pass --label next time. "
        "Remove it with: canair captures uds --delete --last-session",
        file=sys.stderr,
    )
    return label, _parse_states(states), notes


def prompt_metadata(
    suggested_label: str = "",
    last_state: list | str | None = None,
) -> Metadata | None:
    """Prompt user for session label, vehicle_states, notes.

    Returns ``(label, vehicle_states, notes)`` (vehicle_states is a token list), or None when
    the user skips by giving an empty label with no suggestion. The suggested_label is shown in
    brackets and accepted on Enter. last_state (a list or string) is shown as the default and
    re-used across saves when the user just presses Enter. Without a terminal on stdin nothing
    is asked and the suggestion is used, as it is when the prompt ends early.
    """
    if not _stdin_is_terminal():
        return _unanswered("no terminal to ask for a label", suggested_label, last_state, "")

    last_str = _join_states(last_state)
    try:
        if suggested_label:
            label = input(f"  Session label [{suggested_label}]: ").strip()
            if not label:
                label = suggested_label
        else:
            label = input("  Session label (required, empty to skip): ").strip()
            if not label:
                print("  Skipped (empty label); nothing saved.", file=sys.stderr)
                return None

        state_prompt = (
            f"  States [{last_str}]: "
            if last_str
            else "  States (comma-separated, e.g. sleep, acc, charging) []: "
        )
        raw = input(state_prompt).strip()
        if not raw and last_str:
            raw = last_str

        notes = input("  Notes []: ").strip()
    except (KeyboardInterrupt, EOFError):
        print(file=sys.stderr)
        return _unanswered("the label prompt ended early", suggested_label, last_state, "")

    return label, _parse_states(raw), notes


def resolve_metadata(
    label: str | None,
    vehicle_states: list | str | None,
    notes: str | None,
    suggested_label: str = "",
    last_state: list | str | None = None,
) -> Metadata | None:
    """Resolve session metadata, non-interactively when a label is supplied.

    If ``label`` is given (e.g. from the ``--label`` CLI flag), use the flag
    values directly and do NOT prompt — this is what agents/scripts use. When
    ``label`` is None, fall back to the prompt in :func:`prompt_metadata`.
    Returns ``(label, vehicle_states, notes)`` (vehicle_states as a token list),
    or None when the user skipped saving.
    """
    if label is not None:
        return label, _parse_states(vehicle_states), (notes or "")
    if not _stdin_is_terminal():
        return _unanswered(
            "no terminal to ask for a label",
            suggested_label,
            vehicle_states if vehicle_states else last_state,
            notes or "",
        )
    return prompt_metadata(suggested_label=suggested_label, last_state=last_state)
