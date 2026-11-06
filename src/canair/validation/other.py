"""Validation of the profile's other YAML files: vehicle_states.yaml, groups.yaml, actions.yaml,
can_buses.yaml, signals/ and the captures/can index."""

import json

from jsonschema import Draft202012Validator

from canair.core import yaml_io
from canair.core.json_schema import schema_errors
from canair.core.paths import CAN_BUSES_SCHEMA_FILE, GROUPS_SCHEMA_FILE, STATES_SCHEMA_FILE
from canair.profile import Profile

from ._common import CAN_INDEX_SCHEMA_FILE, duplicate_key_errors
from .report import FileReport, SignalsReport

# First line of the pointer file a checkout without the LFS object holds in place of the content.
LFS_POINTER_PREFIX = "version https://git-lfs.github.com/spec/v1"


def _state_reference_errors(
    predicates: list[tuple[str, str]], profile: Profile
) -> tuple[list[str], str | None]:
    """Cross-reference each ``when:`` predicate's ECU.PARAM refs against the registry.

    Returns ``(errors, skip_reason)``. An unresolvable reference makes its state
    permanently un-suggestable, and the evaluator cannot report it: a missing
    signal is UNKNOWN exactly like a not-polled one. So this is the only place a
    renamed/typo'd signal name in a predicate can be caught.

    The check is skipped (with a reason, never silently) when the ECU registry
    can't be loaded — mirroring :func:`check_groups`' tolerance, since a profile with a
    broken ``ecus/`` already fails ``validate pids`` loudly.
    """
    from canair.ecus.pids import load_raw_pids
    from canair.states.refs import check_references_on_every_car

    if not predicates:
        return ([], None)
    try:
        if not load_raw_pids(profile).get("ecus"):
            return ([], "no ECUs defined in ecus/ yet")
        issues = check_references_on_every_car(predicates, profile)
    except Exception as ex:
        return ([], f"could not load ecus/ ({ex}) — see `canair validate pids`")
    return ([str(issue) for issue in issues], None)


def _state_relation_errors(
    implies_entries: list[tuple[str, object]],
    excludes_entries: list[tuple[str, object]],
    declared: set[str],
) -> list[str]:
    """Validate the ``implies:`` hierarchy and the ``excludes:`` exclusivity relation.

    Both are hard errors rather than warnings because both fail *silently* at
    runtime: an unresolvable target disables the relation for that state (the
    closure walk simply doesn't find it), a cycle makes "more specific than"
    meaningless, and a pair declared both implied and exclusive makes every
    session carrying it look self-contradictory.
    """
    from canair.states.implies import (
        exclusion_conflicts,
        implication_cycle,
        parse_excludes,
        parse_implies,
    )
    from canair.states.predicates import StateRule
    from canair.states.tokens import ALL_STATE

    errors: list[str] = []
    targets_by_state: dict[str, dict[str, tuple[str, ...]]] = {}

    for field, entries, verb, infinitive, tail in (
        ("implies", implies_entries, "implies", "imply", "not a state to specialize"),
        ("excludes", excludes_entries, "excludes", "exclude", "not a state to exclude"),
    ):
        for name, raw in entries:
            upper = str(name).upper()
            if not isinstance(raw, (list, tuple, str)):
                continue  # the schema reports it
            parse = parse_implies if field == "implies" else parse_excludes
            targets = parse(raw)
            for target in targets:
                if target == upper:
                    errors.append(f"state '{name}': {verb} itself")
                elif target == ALL_STATE:
                    errors.append(
                        f"state '{name}': {verb} ALL — ALL is the meta-token for "
                        f"'readable in every state', {tail}"
                    )
                elif target not in declared:
                    errors.append(
                        f"state '{name}': {verb} '{target}', which is not a declared state"
                    )
            if upper == ALL_STATE and targets:
                errors.append(f"state '{name}': the ALL meta-token cannot {infinitive} anything")
            # A self-reference is already reported above; dropping it here keeps one
            # mistake to one error instead of also tripping the cycle/conflict checks.
            slot = targets_by_state.setdefault(upper, {})
            slot[field] = tuple(t for t in targets if t != upper)

    rules = [
        StateRule(name=upper, implies=rel.get("implies", ()), excludes=rel.get("excludes", ()))
        for upper, rel in targets_by_state.items()
    ]

    cycle = implication_cycle(rules)
    if cycle:
        errors.append(f"implies: cycle {' -> '.join(cycle)} — the hierarchy must be acyclic")
    for a, b in exclusion_conflicts(rules):
        errors.append(
            f"states '{a}' and '{b}': declared both implied and mutually exclusive — "
            "implies: says one always holds with the other, excludes: says it never can"
        )
    return errors


def check_states(profile: Profile) -> FileReport:
    """Check the profile's optional vehicle_states.yaml (schema, predicates and relations)."""
    from canair.states.predicates import StatePredicateError, compile_predicate

    path = profile.states_file
    name = path.name
    if not path.exists():
        return FileReport(name, skipped="No vehicle_states.yaml (optional) — skipping.")

    text = path.read_text()
    data = yaml_io.safe_load(text) or {}
    errors: list[str] = duplicate_key_errors(text) + schema_errors(STATES_SCHEMA_FILE, data)
    states = data.get("states") if isinstance(data, dict) else None

    seen: set[str] = set()
    predicates: list[tuple[str, str]] = []
    implies_entries: list[tuple[str, object]] = []
    excludes_entries: list[tuple[str, object]] = []
    for i, entry in enumerate(states if isinstance(states, list) else []):
        state = entry.get("name") if isinstance(entry, dict) else None
        if not isinstance(state, str) or not state.strip():
            continue  # the schema reports it
        if state in seen:
            errors.append(f"states[{i}]: duplicate state name '{state}'")
            continue
        seen.add(state)
        expr = entry.get("when")
        if isinstance(expr, str) and expr:
            try:
                compile_predicate(expr)
            except StatePredicateError as ex:
                errors.append(f"states[{i}] ('{state}'): invalid when: {ex}")
            else:
                predicates.append((state, expr))
        if entry.get("implies") is not None:
            implies_entries.append((state, entry["implies"]))
        if entry.get("excludes") is not None:
            excludes_entries.append((state, entry["excludes"]))

    errors += _state_relation_errors(implies_entries, excludes_entries, {n.upper() for n in seen})

    ref_errors, skipped = _state_reference_errors(predicates, profile)
    errors += ref_errors

    if errors:
        hint = (
            "  (a predicate referencing a signal that does not exist can never "
            "match — fix it with `canair states set-predicate NAME EXPR`)"
            if ref_errors
            else ""
        )
        return FileReport(name, errors=tuple(errors), error_hint=hint)
    # Only mention a relation when the profile declares one — a profile with no
    # `implies:` should not carry a permanent ", 0 with implies:" in its OK line.
    hierarchy = f", {len(implies_entries)} with implies:" if implies_entries else ""
    hierarchy += f", {len(excludes_entries)} with excludes:" if excludes_entries else ""
    return FileReport(
        name,
        summary=f"({len(seen)} states, {len(predicates)} predicate(s){hierarchy})",
        notes=(f"  note: predicate signal references not checked — {skipped}",) if skipped else (),
    )


def check_groups(profile: Profile) -> FileReport:
    """Check the profile's optional groups.yaml (schema, names, selectors and member ECUs)."""
    from canair.ecus.names import build_canonical_name_index, canonical_ecu_name_safe
    from canair.ecus.pids import build_ecu_index, load_pids
    from canair.query import QueryError, parse_selector

    path = profile.groups_file
    if not path.exists():
        return FileReport("groups.yaml", skipped="No groups.yaml (optional) — skipping.")

    text = path.read_text()
    data = yaml_io.safe_load(text) or {}
    errors: list[str] = duplicate_key_errors(text) + schema_errors(GROUPS_SCHEMA_FILE, data)
    groups = data.get("groups") if isinstance(data, dict) else None

    # Registry for the member ECU-existence check (best-effort — an absent/partial
    # registry just skips that check, mirroring the query resolver's tolerance).
    try:
        ecu_index = build_ecu_index(load_pids(profile=profile))
        name_index = build_canonical_name_index(profile=profile)
    except Exception:
        ecu_index, name_index = {}, None

    seen: set[str] = set()
    n_members = 0
    for name, meta in groups.items() if isinstance(groups, dict) else ():
        key = str(name).strip().lower()
        if not key:
            continue  # the schema reports it
        if key in seen:
            errors.append(f"duplicate group name '{key}'")
            continue
        seen.add(key)
        members = meta.get("members") if isinstance(meta, dict) else meta
        for raw in members if isinstance(members, list) else ():
            n_members += 1
            if not isinstance(raw, str) or not raw.strip() or raw.strip().startswith("@"):
                continue  # the schema reports it
            tok = raw.strip()
            try:
                sel = parse_selector(tok)
            except QueryError as ex:
                errors.append(f"group '{key}': invalid member selector '{tok}': {ex}")
                continue
            if ecu_index:
                canon = canonical_ecu_name_safe(sel.ecu, name_index, profile=profile).upper()
                if canon not in ecu_index:
                    errors.append(f"group '{key}': member '{tok}' names unknown ECU '{sel.ecu}'")

    if errors:
        return FileReport("groups.yaml", errors=tuple(errors))
    return FileReport("groups.yaml", summary=f"({len(seen)} groups, {n_members} members)")


def check_actions(profile: Profile) -> FileReport:
    """Check the profile's optional actions.yaml (guarded profile actions).

    Structure and the guard rules come from the loader itself
    (:func:`canair.actions.parse_actions_doc`), so a file that loads is a file
    that validates. On top of that every target, read and condition signal must
    resolve against the ECU registry, and a request the command blocklist refuses
    must be granted by one of the profile's quirks, or the action could never run.
    """
    from canair.actions import ActionError, parse_actions_doc, registry_issues
    from canair.actions.plan import action_policy, plan_requests
    from canair.ecus.pids import load_pids
    from canair.safety.blocklist import SafetyBlocked, SessionPolicy

    path = profile.actions_file
    if not path.exists():
        return FileReport("actions.yaml", skipped="No actions.yaml (optional) — skipping.")
    text = path.read_text()
    errors: list[str] = duplicate_key_errors(text)
    try:
        actions = parse_actions_doc(yaml_io.safe_load(text))
    except ActionError as e:
        return FileReport("actions.yaml", errors=(*errors, str(e)))
    notes: tuple[str, ...] = ()
    try:
        pids_data = load_pids(profile=profile)
    except Exception as ex:
        pids_data = None
        notes = (f"actions.yaml: registry checks skipped — could not load ecus/ ({ex})",)
    if pids_data:
        policy = SessionPolicy.from_profile(pids_data)
        for action in actions.values():
            issues = registry_issues(action, pids_data)
            errors.extend(issues)
            if issues:
                continue
            try:
                action_policy(
                    action, plan_requests(action, pids_data, profile=profile), policy, pids_data
                )
            except (ActionError, SafetyBlocked) as e:
                errors.append(str(e))
    if errors:
        return FileReport("actions.yaml", errors=tuple(errors), notes=notes)
    return FileReport("actions.yaml", summary=f"({len(actions)} actions)", notes=notes)


def check_can_buses(profile: Profile) -> FileReport:
    """Check the profile's optional can_buses.yaml (CAN bus vocabulary) against its schema."""

    path = profile.can_buses_file
    if not path.exists():
        return FileReport("can_buses.yaml", skipped="No can_buses.yaml (optional) — skipping.")

    text = path.read_text()
    data = yaml_io.safe_load(text) or {}
    errors = duplicate_key_errors(text) + schema_errors(CAN_BUSES_SCHEMA_FILE, data)
    if errors:
        return FileReport("can_buses.yaml", errors=tuple(errors))
    return FileReport("can_buses.yaml", summary=f"({len(data['can_buses'])} bus codes)")


def check_signals(profile: Profile) -> SignalsReport:
    """Check the profile's optional signals/ broadcast signal-definition files.

    Domain-B (broadcast frame) signal maps: one signals/<bus>.yaml per CAN bus,
    keyed by arbitration ID, each signal a DBC-compatible linear model, checked against
    signals_schema.yaml plus the frame-layout rules (:func:`check_signals_doc`).
    """
    from canair.broadcast import load_signals, signals_dir
    from canair.validation.signals import check_signals_doc

    if not signals_dir(profile=profile).is_dir():
        return SignalsReport(skipped="No signals/ (optional) — skipping.")
    docs = load_signals(profile=profile)
    if not docs:
        return SignalsReport(skipped="signals/: no files — skipping.")

    files: list[FileReport] = []
    total_signals = 0
    for doc in docs:
        errors, n_signals = check_signals_doc(doc.data)
        errors = duplicate_key_errors(doc.path.read_text()) + errors
        total_signals += n_signals
        files.append(
            FileReport(doc.path.name, errors=tuple(errors))
            if errors
            else FileReport(doc.path.name, summary=f"({n_signals} signals)")
        )
    return SignalsReport(files=tuple(files), total_signals=total_signals)


def check_can(profile: Profile) -> FileReport:
    """Check the profile's optional captures/can/index.yaml (raw-CAN log index)."""

    name = "captures/can/index.yaml"
    path = profile.can_index_file
    if not path.exists():
        return FileReport(name, skipped="No captures/can/index.yaml (optional) — skipping.")
    with open(CAN_INDEX_SCHEMA_FILE) as f:
        schema = json.load(f)
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    text = path.read_text()
    if text.startswith(LFS_POINTER_PREFIX):
        return FileReport(
            name,
            errors=(
                "is a Git LFS pointer, not the index — run `git lfs pull`, and keep the index "
                "out of LFS (.gitattributes)",
            ),
        )
    data = yaml_io.safe_load(text) or {}
    schema_errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
    errors = duplicate_key_errors(text) + [
        f"{'/'.join(str(p) for p in e.path) or '(root)'}: {e.message}" for e in schema_errors
    ]
    if errors:
        return FileReport(name, errors=tuple(errors))
    return FileReport(name, summary=f"({len(data.get('logs') or [])} log(s))")
