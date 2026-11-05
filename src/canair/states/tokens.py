"""State tokens: the base power states, ``ALL``, YAML boolean words, parse and join."""

from __future__ import annotations

# The canonical base power-state vocabulary shared across every vehicle profile.
# This is the **powertrain-neutral ignition-switch ladder** every road vehicle
# has. The universal switch positions are OFF → ACC → ON → START; canair names
# them with YAML-safe, vendor-agnostic tokens:
#
#     SLEEP   OFF / LOCK — ignition off, ECUs asleep / 12V standby
#     ACC     accessory power                                        (Hyundai ACC)
#     RUN     ignition on — electronics live / driveable             (Hyundai IGN1 + IGN2)
#     CRANK   starter engaged (ICE cranking)                         (Hyundai START)
#
# Hyundai/Kia terminal names, for orientation: ACC, IGN1 (ON or START), IGN2 (ON
# only) and START are the ignition-switch relays; an EV's IG3 is not one of them but
# a separate supply rail the HV side closes on its own (READY, charging), which an EV
# profile declares as its own state.
#
# `RUN` (not `ON`) and `SLEEP` (not `OFF`) are used because `ON`/`OFF`/`YES`/`NO`
# are YAML 1.1 booleans that would parse as `True`/`False` in a bare
# `vehicle_states:` list; `RUN` also reads unambiguously where a bare `IGN`
# invites "which IGN level?" (vendors number them: Hyundai IGN1/IGN2, plus IG3).
# A profile may still declare such a word as its own state (the manufacturer's
# name, e.g. ON); see YAML_BOOLEAN_WORDS for the quoting that then applies.
#
# Only the universal switch positions live here. Finer, vendor-specific rungs
# (Hyundai's separate IGN1/IGN2 relays, the Ioniq's quoted `"ON"`, its `IG3`
# rail) and powertrain modes (EV `PLUGGED`/`READY`/`CHARGING`; the EV `READY` is
# that car's name for the driveable state) are NOT baked in — a profile declares
# those in its `vehicle_states.yaml`, so an ICE profile never inherits EV states
# and an EV profile never inherits ICE-only ones. `allowed_states` returns the
# union (base + ALL + the profile's own).
#
# State tokens are UPPERCASE (like the CAN-bus segment codes) — a visual cue
# that they're a controlled vocabulary, not free prose. Input is normalized to
# uppercase (:func:`parse_states`), so any casing typed on the CLI is accepted.
POWER_STATES = ("SLEEP", "ACC", "RUN", "CRANK")


# YAML 1.1, which canair's readers speak, reads these bare words as booleans: a
# state named ON written as `[ON]` loads as `[True]`, and str() makes that the
# state TRUE. Such a state is legal but must be quoted (`["ON"]`) wherever YAML
# holds it; every canair writer quotes it, and the readers and validators below
# refuse an unquoted one instead of letting it become TRUE. JSON (the capture
# files) needs nothing.
YAML_BOOLEAN_WORDS = frozenset({"Y", "N", "YES", "NO", "ON", "OFF", "TRUE", "FALSE"})


def needs_yaml_quotes(token) -> bool:
    """Whether a state token must be quoted to survive a YAML 1.1 round trip."""
    return str(token).strip().strip("'\"").upper() in YAML_BOOLEAN_WORDS


def unquoted_boolean_error(where: str, value) -> str | None:
    """An error message when ``value`` (a token or token list) holds a YAML boolean.

    ``None`` when every item is a string. A ``bool`` here can only be a state
    word YAML read unquoted, so the message names the fix.
    """
    items = value if isinstance(value, list | tuple) else [value]
    if not any(isinstance(item, bool) for item in items):
        return None
    return (
        f"{where} holds an unquoted YAML boolean: a state named ON, OFF, YES or NO reads "
        'as true/false unless quoted, e.g. ["ON"]'
    )


# The conventional token meaning "applicable/readable in every vehicle state"
# — the state analogue of the ``ALL`` CAN-bus gateway code. It is documentary:
# a PID/DID/research entry tagged ``[ALL]`` is available regardless of power
# state. It carries no ``when:`` predicate (nothing to auto-suggest) and is
# always an accepted token (see :func:`allowed_states`).
ALL_STATE = "ALL"


# Bucket label for a capture carrying no state at all. An explicit population, so
# a state-axis analysis reports how much of its evidence is untagged instead of
# quietly analyzing a subset.
NO_STATE_KEY = "(no state)"


# Backwards-compatible alias (older code/imports referred to BASE_STATES).
BASE_STATES = POWER_STATES


def parse_states(value) -> list[str]:
    """Normalize a ``vehicle_states`` value into an UPPER-cased token list.

    Accepts a comma-separated string (as typed on ``--state``), a list/tuple of
    tokens, or None. Tokens are stripped and UPPER-cased; empties are dropped.
    Kept deliberately permissive (no vocabulary check) — validation soft-warns
    on unknown tokens elsewhere. Casing is normalized here so any casing typed
    on the CLI (``charging``/``Charging``/``CHARGING``) lands as the canonical
    UPPERCASE token.
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        toks = [str(v).strip() for v in value]
    else:
        toks = [t.strip() for t in str(value).split(",")]
    return [t.upper() for t in toks if t]


def join_states(states) -> str:
    """Human-readable join of a ``vehicle_states`` list (``", "``-separated)."""
    if not states:
        return ""
    if isinstance(states, str):
        return states
    return ", ".join(str(s) for s in states)
