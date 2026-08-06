"""vehicle_states.yaml / signals/ / captures/can index validation."""

import json
import re

from jsonschema import Draft202012Validator

from canlib import yaml_io

from ._common import CAN_INDEX_SCHEMA_FILE


def _state_reference_errors(predicates: list[tuple[str, str]]) -> tuple[list[str], str | None]:
    """Cross-reference each ``when:`` predicate's ECU.PARAM refs against the registry.

    Returns ``(errors, skip_reason)``. An unresolvable reference makes its state
    permanently un-suggestable, and the evaluator cannot report it: a missing
    signal is UNKNOWN exactly like a not-polled one. So this is the only place a
    renamed/typo'd signal name in a predicate can be caught.

    The check is skipped (with a reason, never silently) when the ECU registry
    can't be loaded — mirroring ``_run_groups``' tolerance, since a profile with a
    broken ``ecus/`` already fails ``validate pids`` loudly.
    """
    from canlib.pids import load_pids
    from canlib.state_refs import check_references

    if not predicates:
        return ([], None)
    try:
        pids_data = load_pids()
    except Exception as ex:
        return ([], f"could not load ecus/ ({ex}) — see `canair validate pids`")
    if not pids_data.get("ecus"):
        return ([], "no ECUs defined in ecus/ yet")
    return ([str(issue) for issue in check_references(predicates, pids_data)], None)


def _run_states() -> int:
    """Validate the profile's optional vehicle_states.yaml (structure + predicates)."""
    from canlib.profile import active
    from canlib.states import StatePredicateError, compile_predicate

    path = active().states_file
    if not path.exists():
        print("No vehicle_states.yaml (optional) — skipping.")
        return 0

    data = yaml_io.safe_load(path.read_text()) or {}
    errors: list[str] = []
    if not isinstance(data, dict) or "states" not in data:
        print(f"{path.name}: missing top-level 'states:' list")
        return 1

    seen: set[str] = set()
    states = data.get("states") or []
    if not isinstance(states, list):
        print(f"{path.name}: 'states' must be a list")
        return 1

    predicates: list[tuple[str, str]] = []
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
            else:
                predicates.append((str(name), str(expr)))

    ref_errors, skipped = _state_reference_errors(predicates)
    errors += ref_errors

    if errors:
        print(f"{path.name}: {len(errors)} errors")
        for e in errors:
            print(f"  - {e}")
        if ref_errors:
            print(
                "  (a predicate referencing a signal that does not exist can never "
                "match — fix it with `canair states set-predicate NAME EXPR`)"
            )
        return 1
    print(f"{path.name}: OK ({len(seen)} states, {len(predicates)} predicate(s))")
    if skipped:
        print(f"  note: predicate signal references not checked — {skipped}")
    return 0


def _run_groups() -> int:
    """Validate the profile's optional groups.yaml (selector-group vocabulary)."""
    from canlib.ecus import build_canonical_name_index, canonical_ecu_name_safe
    from canlib.pids import build_ecu_index, load_pids
    from canlib.profile import active
    from canlib.query import QueryError, parse_selector

    path = active().groups_file
    if not path.exists():
        print("No groups.yaml (optional) — skipping.")
        return 0

    data = yaml_io.safe_load(path.read_text()) or {}
    if not isinstance(data, dict) or "groups" not in data:
        print("groups.yaml: missing top-level 'groups:' mapping")
        return 1
    groups = data.get("groups")
    if not isinstance(groups, dict):
        print("groups.yaml: 'groups' must be a mapping keyed by group name")
        return 1

    # Registry for the member ECU-existence check (best-effort — an absent/partial
    # registry just skips that check, mirroring the query resolver's tolerance).
    try:
        ecu_index = build_ecu_index(load_pids())
        name_index = build_canonical_name_index()
    except Exception:
        ecu_index, name_index = {}, None

    errors: list[str] = []
    seen: set[str] = set()
    n_members = 0
    for name, meta in groups.items():
        key = str(name).strip().lower()
        if not key:
            errors.append("empty group name")
            continue
        if key in seen:
            errors.append(f"duplicate group name '{key}'")
            continue
        seen.add(key)
        if isinstance(meta, dict):
            for extra in set(meta) - {"description", "members"}:
                errors.append(f"group '{key}': unknown field '{extra}'")
            members = meta.get("members")
        elif isinstance(meta, list):  # bare-list shorthand
            members = meta
        else:
            errors.append(f"group '{key}': must be a mapping (description/members) or a list")
            continue
        if not isinstance(members, list) or not members:
            errors.append(f"group '{key}': 'members' must be a non-empty list")
            continue
        for raw in members:
            tok = str(raw).strip()
            n_members += 1
            if tok.startswith("@"):
                errors.append(f"group '{key}': member '{tok}' — groups cannot contain groups")
                continue
            try:
                sel = parse_selector(tok)
            except QueryError as ex:
                errors.append(f"group '{key}': invalid member selector '{tok}': {ex}")
                continue
            if ecu_index:
                canon = canonical_ecu_name_safe(sel.ecu, name_index).upper()
                if canon not in ecu_index:
                    errors.append(f"group '{key}': member '{tok}' names unknown ECU '{sel.ecu}'")

    if errors:
        print(f"groups.yaml: {len(errors)} errors")
        for e in errors:
            print(f"  - {e}")
        return 1
    print(f"groups.yaml: OK ({len(seen)} groups, {n_members} members)")
    return 0


_ARB_ID_RE = re.compile(r"^0x[0-9A-Fa-f]+$")


def _run_can_buses() -> int:
    """Validate the profile's optional can_buses.yaml (CAN bus vocabulary)."""
    from canlib.profile import active

    path = active().can_buses_file
    if not path.exists():
        print("No can_buses.yaml (optional) — skipping.")
        return 0

    data = yaml_io.safe_load(path.read_text()) or {}
    if not isinstance(data, dict) or "can_buses" not in data:
        print("can_buses.yaml: missing top-level 'can_buses:' mapping")
        return 1
    buses = data.get("can_buses")

    errors: list[str] = []
    seen: set[str] = set()

    def _check_code(i, code) -> None:
        code = str(code).strip()
        if not code:
            errors.append(f"can_buses[{i}]: empty code")
        elif code in seen:
            errors.append(f"can_buses: duplicate code '{code}'")
        else:
            seen.add(code)

    if isinstance(buses, dict):
        for i, (code, meta) in enumerate(buses.items()):
            _check_code(i, code)
            if meta is not None and not isinstance(meta, (dict, str)):
                errors.append(
                    f"can_buses['{code}']: must be a mapping (name/description) or omitted"
                )
            elif isinstance(meta, dict):
                for extra in set(meta) - {"name", "description", "bitrate"}:
                    errors.append(f"can_buses['{code}']: unknown field '{extra}'")
                rate = meta.get("bitrate")
                if rate is not None and (
                    not isinstance(rate, int) or isinstance(rate, bool) or rate <= 0
                ):
                    errors.append(
                        f"can_buses['{code}']: bitrate must be a positive integer (bit/s)"
                    )
    elif isinstance(buses, list):  # legacy list form
        for i, code in enumerate(buses):
            _check_code(i, code)
    else:
        print("can_buses.yaml: 'can_buses' must be a mapping (or legacy list)")
        return 1

    if errors:
        print(f"can_buses.yaml: {len(errors)} errors")
        for e in errors:
            print(f"  - {e}")
        return 1
    print(f"can_buses.yaml: OK ({len(seen)} bus codes)")
    return 0


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
    from canlib.signals import load_signals, signals_dir

    if not signals_dir().is_dir():
        print("No signals/ (optional) — skipping.")
        return 0
    docs = load_signals()
    if not docs:
        print("signals/: no files — skipping.")
        return 0

    total_errors = 0
    total_signals = 0
    for path, data in ((d.path, d.data) for d in docs):
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
        print(f"\n{total_errors} total errors across {len(docs)} signals file(s)")
        return 1
    print(f"\nAll {len(docs)} signals file(s) valid ({total_signals} signals).")
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
    data = yaml_io.safe_load(path.read_text()) or {}
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
