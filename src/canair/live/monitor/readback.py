"""Live monitor — an output's readback, and the watch that notices the ECU take the output back.

An output's readback is its entry's ``status_param`` signal as the monitor last decoded it, when
that signal's PID is polled (:func:`readback_row`). It costs no bus traffic.

An ECU leaves its diagnostic session on its own when no request reaches it before its S3 timer
runs out (a stalled link, for one), and it then takes back every output it was driving.
TesterPresent is answered in the default session too, so nothing on the bus tells canair. The
readback can: :class:`ReadbackWatch` keeps the readback from just before an output was switched,
and says once when the readback goes back to that value after it had moved away from it while
the output stayed on. The first reads after a switch can still show the old value (a relay that
takes a few seconds), so a readback that never moved says nothing.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from canair.expression.decoding import ParamRow
from canair.expression.results import EcuFrame
from canair.render.formatting import format_value

from .health import label_ecu

#: A decoded signal value: a number, or the text of an ascii/date/struct signal.
Value = float | str


def readback_row(frames: Sequence[EcuFrame], ecu: str, signal: str | None) -> ParamRow | None:
    """``signal``'s row on ``ecu`` in the monitor's last ``frames``; None when not polled."""
    if not signal:
        return None
    for label, entries in frames:
        if label_ecu(label) != ecu:
            continue
        for entry in entries:
            for row in entry.get("params") or ():
                if row[0] == signal:
                    return row
    return None


def readback_value(row: ParamRow | None) -> Value | None:
    """The decoded value of a readback ``row``; None when there is none or it failed."""
    if row is None or row[4]:
        return None
    return row[1]


def readback_text(row: ParamRow) -> str:
    """A readback ``row`` as the actuator pane shows it: its value with its unit, or its error."""
    _name, value, unit, _expr, error, _verified, display = row
    return error if error else format_value(value, unit, display)


@dataclass
class _Held:
    before: Value
    moved: bool = False
    warned: bool = False


class ReadbackWatch:
    """For each output being held (by its pane key): its readback before the switch, and since."""

    def __init__(self) -> None:
        self._held: dict[str, _Held] = {}

    def switched(self, key: str, before: Value | None) -> None:
        """``key`` is being switched on, or to a value, while its readback reads ``before``.

        With no readback to compare against, the hold is not watched.
        """
        if before is None:
            self._held.pop(key, None)
        else:
            self._held[key] = _Held(before)

    def released(self, key: str) -> None:
        """``key`` is not held any more."""
        self._held.pop(key, None)

    def returned(self, key: str, value: Value | None) -> bool:
        """``key`` is still held and its readback reads ``value``.

        True once per hold: when the readback is back at its value from before the switch,
        after it had read another value since.
        """
        held = self._held.get(key)
        if held is None or value is None or held.warned:
            return False
        if value != held.before:
            held.moved = True
            return False
        if not held.moved:
            return False
        held.warned = True
        return True
