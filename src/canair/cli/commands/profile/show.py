"""``canair profile show`` and ``path``."""

from __future__ import annotations

from canair.profile import (
    BUNDLE_MEMBERS,
    active,
    resolve_profile,
)
from canair.states.predicates import StatePredicateError
from canair.states.vocabulary import load_states
from canair.variants import effective_meta


def _resolve(name: str | None):
    return resolve_profile(name) if name else active()


def _detail_can_buses(prof) -> str:
    from canair.ecus.can_buses import load_can_buses

    buses = load_can_buses(prof)
    codes = ", ".join(b.code for b in buses) if buses else "empty"
    return f"{len(buses)} buses: {codes}"


def _detail_states(prof) -> str:
    try:
        rules = load_states(prof)
    except StatePredicateError as ex:
        return f"INVALID: {ex}"
    names = ", ".join(f"{r.name}*" if r.predicate else r.name for r in rules)
    # A second line is printed as an indented continuation (see cmd_show).
    return f"{len(rules)} states: {names}\n(* = has an auto-suggest predicate)"


def _detail_groups(prof) -> str:
    from canair.ecus.ecu_groups import GroupError, load_groups

    try:
        groups = load_groups(prof)
    except GroupError as ex:
        return f"INVALID: {ex}"
    names = ", ".join(f"@{g}" for g in groups) if groups else "empty"
    return f"{len(groups)} groups: {names}"


def _detail_actions(prof) -> str:
    from canair.actions import ActionError, load_actions

    try:
        actions = load_actions(prof)
    except ActionError as ex:
        return f"INVALID: {ex}"
    return f"{len(actions)} actions: {', '.join(actions) if actions else 'empty'}"


def _detail_signals(prof) -> str:
    from canair.broadcast import load_signals

    names = sorted(d.path.name for d in load_signals(profile=prof))
    return f"{len(names)} files: {', '.join(names) if names else 'empty'}"


def _detail_variants(prof) -> str:
    from canair.variants import VariantsError, describe, load_variants

    try:
        variants = load_variants(prof)
    except VariantsError as ex:
        return f"INVALID: {ex}"
    if not variants:
        return "empty"
    # Each further line is printed as an indented continuation (see cmd_show).
    return "\n".join(
        [f"{len(variants)} variants", *(f"  {vid}: {describe(v)}" for vid, v in variants.items())]
    )


def _detail_references(prof) -> str:
    return f"{sum(1 for p in prof.references_dir.rglob('*') if p.is_file())} files"


# Richer "what's in it" line per member, where a bare ok/missing isn't useful.
# A member with no entry here still gets listed (see cmd_show) — the registry
# decides *what* is shown, this only decides how much detail.
_MEMBER_DETAIL = {
    "can_buses.yaml": _detail_can_buses,
    "vehicle_states.yaml": _detail_states,
    "groups.yaml": _detail_groups,
    "actions.yaml": _detail_actions,
    "variants.yaml": _detail_variants,
    "signals": _detail_signals,
    "references": _detail_references,
}


# How `profile show` names where the selected variant came from.
_VARIANT_SOURCE = {
    "flag": "--variant",
    "env": "CANAIR_VARIANT",
    "config": "profile_settings in the config",
    "alias": "the old profile name",
    "only": "the profile's only variant",
}


def cmd_show(args) -> int:
    prof = _resolve(args.name)
    meta = effective_meta(prof)
    print(f"name:       {prof.name}")
    print(f"root:       {prof.root}")
    for overlay in prof.overlays:
        print(f"overlay:    {overlay}  (your layer — captures land here)")
    print(f"car_model:  {meta.get('car_model', '?')}")
    from canair.profile.names import also_known_as

    if known_as := also_known_as(prof.root).all():
        print(f"known as:   {', '.join(known_as)}")
    if prof.variant:
        print(
            f"variant:    {prof.variant}  (from {_VARIANT_SOURCE[prof.variant_source or 'only']})"
        )
    from canair.ecus.pids import revision_resolutions
    from canair.variants.revisions import describe_resolution

    for name, res in revision_resolutions(prof).items():
        print(f"revision:   {name} {describe_resolution(res)}")
    print(f"init:       {meta.get('init', '?')}")

    for member in BUNDLE_MEMBERS:
        path = prof.member_path(member)
        if not prof.member_exists(member):
            detail = "MISSING" if member.required else "none — optional"
        elif member.name in _MEMBER_DETAIL:
            detail = _MEMBER_DETAIL[member.name](prof)
        else:
            detail = "ok"
        head, _, rest = detail.partition("\n")
        print(f"{member.display_label + ':':<12}{path}  ({head})")
        for line in rest.splitlines():
            print(f"{'':<12}{line}")
        # The raw-CAN frame-log store lives inside captures/.
        if member.name == "captures":
            for layer in prof.capture_layers[:-1]:
                print(f"{'':<12}{layer}  (base layer, read-only)")
            if prof.can_dir.is_dir():
                idx = "index ok" if prof.can_index_file.exists() else "no index"
            else:
                idx = "none — optional"
            print(f"{'can logs:':<12}{prof.can_dir}  ({idx})")

    from canair.dtc.log import load_log, log_file

    dtc_log = log_file(prof)
    scans = len(load_log(dtc_log)["scans"]) if dtc_log.exists() else 0
    detail = f"{scans} scan(s) — user state" if scans else "none — user state"
    print(f"{'dtc log:':<12}{dtc_log}  ({detail})")
    return 0


def cmd_path(args) -> int:
    print(_resolve(args.name).root)
    return 0
