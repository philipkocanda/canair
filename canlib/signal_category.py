"""What a signal measures or represents: its category.

Most signals need no stated category, because the unit already says it (``V`` is a voltage,
``km/h`` a speed). :data:`UNIT_CATEGORIES` holds those, and :func:`category_of` derives them.
A signal states ``category:`` only where the unit does not: a ``%`` that is a state of charge
rather than a duty cycle, or a one-bit flag that is a door rather than a relay.

The vocabulary is canair's own and tied to no consumer. An exporter maps it onto its
target's vocabulary (the WiCAN AutoPID ``class``: ``canlib/wican/autopid_class.py``) and
leaves out a category the target has no word for.

A binary category names the *thing*, never which value means what: that is the signal's
``type: enum`` ``values:`` map (``{0: unlocked, 1: locked}``), so a binary category without
one is reported. Two lock bits of opposite polarity are both ``lock``.
"""

from __future__ import annotations

from collections.abc import Mapping

# A category the unit implies. Matched exactly: a unit no row names implies nothing.
UNIT_CATEGORIES: dict[str, str] = {
    **dict.fromkeys(("°C", "C", "degC", "°F", "degF"), "temperature"),
    **dict.fromkeys(("V", "mV", "kV"), "voltage"),
    **dict.fromkeys(("A", "mA"), "current"),
    **dict.fromkeys(("W", "kW"), "power"),
    **dict.fromkeys(("Wh", "kWh"), "energy"),
    **dict.fromkeys(("km/h", "mph", "m/s"), "speed"),
    **dict.fromkeys(("bar", "mbar", "Pa", "hPa", "kPa", "psi"), "pressure"),
    **dict.fromkeys(("km", "mi", "m", "cm", "mm"), "distance"),
    **dict.fromkeys(("h", "min", "s", "ms"), "duration"),
    **dict.fromkeys(("Hz", "kHz"), "frequency"),
}

QUANTITY_CATEGORIES: tuple[str, ...] = tuple(dict.fromkeys(UNIT_CATEGORIES.values()))

# A quantity the unit alone cannot tell apart (a ``%`` is all three, and a throttle too).
SCALAR_CATEGORIES: tuple[str, ...] = ("state_of_charge", "state_of_health", "humidity")

# A two-state signal; which state is which lives in its enum ``values:``.
BINARY_CATEGORIES: tuple[str, ...] = ("door", "opening", "lock", "lamp", "plug", "charging")

CATEGORIES: tuple[str, ...] = QUANTITY_CATEGORIES + SCALAR_CATEGORIES + BINARY_CATEGORIES


def unit_category(unit: object) -> str | None:
    """The category ``unit`` implies, or ``None``."""
    return UNIT_CATEGORIES.get(unit.strip()) if isinstance(unit, str) else None


def category_of(param: Mapping) -> str | None:
    """A signal's category: its own ``category:``, else the one its unit implies.

    Pass the *effective* parameter (:func:`canlib.pids.effective_param`), since the unit may
    be stated on the PID.
    """
    stated = param.get("category")
    if isinstance(stated, str) and stated:
        return stated
    return unit_category(param.get("unit"))


def category_findings(param: Mapping, label: str) -> tuple[list[str], list[str]]:
    """``(errors, warnings)`` for one effective parameter's ``category:``.

    An error for a value outside the vocabulary, or one the unit already implies (stated
    twice, it can only drift). A warning for a binary category whose signal has no two-entry
    enum, because then nothing says which state is which; a warning, not an error, so a
    profile migrated off ``ha_class`` stays editable until its polarities are written down.
    """
    stated = param.get("category")
    if stated is None:
        return [], []
    if not isinstance(stated, str) or stated not in CATEGORIES:
        return [f"{label}: invalid category {stated!r} (allowed: {', '.join(CATEGORIES)})"], []
    implied = unit_category(param.get("unit"))
    if implied is not None:
        return [
            f"{label}: unit {param.get('unit')!r} already implies category {implied!r}; "
            "remove category"
        ], []
    if stated in BINARY_CATEGORIES:
        values = param.get("values")
        if param.get("type") != "enum" or not isinstance(values, Mapping) or len(values) != 2:
            return [], [
                f"{label}: category {stated!r} needs a two-state enum saying which value is "
                "which (canair pids upsert-param … --value 0=LABEL --value 1=LABEL)"
            ]
    return [], []
