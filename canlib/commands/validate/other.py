"""states.yaml / signals/ / captures/can index validation."""

import json
import re

import yaml
from jsonschema import Draft202012Validator

from ._common import CAN_INDEX_SCHEMA_FILE


def _run_states() -> int:
    """Validate the profile's optional states.yaml (structure + predicates)."""
    from canlib.profile import active
    from canlib.states import StatePredicateError, compile_predicate

    path = active().states_file
    if not path.exists():
        print("No states.yaml (optional) — skipping.")
        return 0

    data = yaml.safe_load(path.read_text()) or {}
    errors: list[str] = []
    if not isinstance(data, dict) or "states" not in data:
        print("states.yaml: missing top-level 'states:' list")
        return 1

    seen: set[str] = set()
    states = data.get("states") or []
    if not isinstance(states, list):
        print("states.yaml: 'states' must be a list")
        return 1

    for i, entry in enumerate(states):
        if not isinstance(entry, dict):
            errors.append(f"states[{i}]: must be a mapping")
            continue
        for extra in set(entry) - {"name", "description", "when"}:
            errors.append(f"states[{i}]: unknown field '{extra}'")
        name = entry.get("name")
        if not name:
            errors.append(f"states[{i}]: missing 'name'")
        elif name in seen:
            errors.append(f"states[{i}]: duplicate state name '{name}'")
        else:
            seen.add(name)
        expr = entry.get("when")
        if expr:
            try:
                compile_predicate(expr)
            except StatePredicateError as ex:
                errors.append(f"states[{i}] ('{name}'): invalid when: {ex}")

    if errors:
        print(f"states.yaml: {len(errors)} errors")
        for e in errors:
            print(f"  - {e}")
        return 1
    print(f"states.yaml: OK ({len(seen)} states)")
    return 0


_ARB_ID_RE = re.compile(r"^0x[0-9A-Fa-f]+$")


def check_signals_doc(data: object) -> tuple[list[str], int]:
    """Structural check of one parsed signals/<bus>.yaml doc.

    Returns ``(errors, signal_count)``. Shared by ``validate signals`` and the
    ``signals`` editor's rollback guard so both enforce the same rules
    (mirroring the ``signals_schema.yaml`` companion).
    """
    errors: list[str] = []
    if not isinstance(data, dict):
        return (["top level must be a mapping"], 0)
    valid_byte_orders = {"little", "big"}
    for extra in set(data) - {"bus", "bitrate", "messages"}:
        errors.append(f"unknown top-level field '{extra}'")
    if "bitrate" in data and not isinstance(data.get("bitrate"), int):
        errors.append("'bitrate' must be an integer")
    messages = data.get("messages") or {}
    if not isinstance(messages, dict):
        return ([*errors, "'messages' must be a mapping keyed by arbitration ID"], 0)
    n_signals = 0
    allowed = {
        "start_bit",
        "length",
        "byte_order",
        "scale",
        "offset",
        "min",
        "max",
        "unit",
        "verified",
        "source",
        "notes",
    }
    for mid, msg in messages.items():
        if not _ARB_ID_RE.match(str(mid)):
            errors.append(f"message id '{mid}' is not a hex arbitration ID (e.g. 0x220)")
        if not isinstance(msg, dict):
            errors.append(f"message '{mid}': must be a mapping")
            continue
        for extra in set(msg) - {"name", "tx_ecu", "signals"}:
            errors.append(f"message '{mid}': unknown field '{extra}'")
        signals = msg.get("signals") or {}
        if not isinstance(signals, dict):
            errors.append(f"message '{mid}': 'signals' must be a mapping")
            continue
        for sname, sig in signals.items():
            n_signals += 1
            if not isinstance(sig, dict):
                errors.append(f"{mid}/{sname}: must be a mapping")
                continue
            for extra in set(sig) - allowed:
                errors.append(f"{mid}/{sname}: unknown field '{extra}'")
            for req in ("start_bit", "length"):
                if req not in sig:
                    errors.append(f"{mid}/{sname}: missing required '{req}'")
            sb = sig.get("start_bit")
            if isinstance(sb, int) and sb < 0:
                errors.append(f"{mid}/{sname}: start_bit must be >= 0")
            ln = sig.get("length")
            if isinstance(ln, int) and ln < 1:
                errors.append(f"{mid}/{sname}: length must be >= 1")
            bo = sig.get("byte_order")
            if bo is not None and bo not in valid_byte_orders:
                errors.append(f"{mid}/{sname}: byte_order must be little|big (got '{bo}')")
    return (errors, n_signals)


def _run_signals() -> int:
    """Validate the profile's optional signals/ broadcast signal-definition files.

    Domain-B (broadcast frame) signal maps: one signals/<bus>.yaml per CAN bus,
    keyed by arbitration ID, each signal a DBC-compatible linear model. Structural
    checks mirror the signals_schema.yaml companion.
    """
    from canlib.profile import active

    sig_dir = active().signals_dir
    if not sig_dir.exists():
        print("No signals/ (optional) — skipping.")
        return 0
    files = sorted(sig_dir.glob("*.yaml"))
    if not files:
        print("signals/: no files — skipping.")
        return 0

    total_errors = 0
    total_signals = 0
    for path in files:
        data = yaml.safe_load(path.read_text()) or {}
        errors, n_signals = check_signals_doc(data)
        total_signals += n_signals
        if errors:
            print(f"\n{path.name}: {len(errors)} errors")
            for e in errors:
                print(f"  - {e}")
            total_errors += len(errors)
        else:
            print(f"{path.name}: OK ({n_signals} signals)")

    if total_errors:
        print(f"\n{total_errors} total errors across {len(files)} signals file(s)")
        return 1
    print(f"\nAll {len(files)} signals file(s) valid ({total_signals} signals).")
    return 0


def _run_can() -> int:
    """Validate the profile's optional captures/can/index.yaml (raw-CAN log index)."""
    from canlib.profile import active

    path = active().can_index_file
    if not path.exists():
        print("No captures/can/index.yaml (optional) — skipping.")
        return 0
    with open(CAN_INDEX_SCHEMA_FILE) as f:
        schema = json.load(f)
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    data = yaml.safe_load(path.read_text()) or {}
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
    if errors:
        print(f"captures/can/index.yaml: {len(errors)} errors")
        for e in errors:
            loc = "/".join(str(p) for p in e.path) or "(root)"
            print(f"  - {loc}: {e.message}")
        return 1
    n = len(data.get("logs") or [])
    print(f"captures/can/index.yaml: OK ({n} log(s))")
    return 0
