"""``validate captures``: which car each session is tagged with.

Two findings, both reported as a footer rather than per file:

* sessions tagged with a variant the profile does not declare — the tag names a
  car the profile no longer knows (a hand edit, or a ``variants.yaml`` change made
  without ``canair variants rename``);
* sessions tagged with an ECU revision the ECU does not declare (``revisions:``), or
  one the session's own variant cannot carry, and sessions holding replies to PIDs their
  tagged revision does not answer (a protocol revision identifies itself in the data);
* on a profile with more than one variant, how many sessions carry no tag at all,
  since analysis can't tell which car those came from.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from canlib.captures import io as capture_io


def variant_tag_findings(files: list[Path], declared: set[str]) -> tuple[int, Counter[str]]:
    """``(untagged sessions, undeclared tag -> sessions)`` across ``files``."""
    untagged = 0
    undeclared: Counter[str] = Counter()
    for path in files:
        for session in (capture_io.load_capture_file(path) or {}).get("sessions") or []:
            variant = session.get("variant")
            if not variant:
                untagged += 1
            elif variant not in declared:
                undeclared[str(variant)] += 1
    return untagged, undeclared


def revision_tag_findings(files: list[Path], profile) -> Counter[str]:
    """``problem -> sessions`` for every revision tag that names what cannot be."""
    from canlib.ecus import build_rx_index, ecu_name_from_ref
    from canlib.pids import load_raw_pids
    from canlib.variants import load_variants
    from canlib.variants.revisions import canonical_ecu, gate, possible_revisions

    ecus = load_raw_pids(profile).get("ecus") or {}
    variants = load_variants(profile)
    rx_index = build_rx_index()
    found: Counter[str] = Counter()
    for path in files:
        for session in (capture_io.load_capture_file(path) or {}).get("sessions") or []:
            tags = {str(e).upper(): r for e, r in (session.get("revisions") or {}).items()}
            outside: set[str] = set()
            for cap in session.get("captures") or []:
                rx = capture_io.capture_rx(cap)
                ecu = str(ecu_name_from_ref(rx, rx_index)) if rx else ""
                rid = tags.get(ecu.upper())
                ecu_def = ecus.get(ecu) or {}
                pid_def = next(
                    (
                        d
                        for k, d in (ecu_def.get("pids") or {}).items()
                        if str(k).upper() == str(cap.get("pid", "")).upper()
                    ),
                    None,
                )
                applies = gate(pid_def)
                if rid and applies is not None and rid not in applies and cap.get("payload"):
                    outside.add(f"{ecu} {cap.get('pid')}")
            for where in sorted(outside):
                ecu = where.split()[0]
                found[
                    f"a reply to {where}, which {ecu} revision {tags[ecu.upper()]!r} does not "
                    "answer (is the session's revision tag wrong?)"
                ] += 1
            for ecu, rid in (session.get("revisions") or {}).items():
                name = canonical_ecu(ecu, ecus)
                if name is None:
                    found[f"a revision tag for ECU {ecu!r}, which ecus/ does not define"] += 1
                    continue
                car = variants.get(str(session.get("variant") or ""))
                possible = possible_revisions(name, ecus[name], car)
                if rid not in possible:
                    on = f" on variant {car.id}" if car else ""
                    found[
                        f"{name} revision {rid!r}, which {name} cannot be{on} "
                        f"(possible: {', '.join(possible) or 'none'})"
                    ] += 1
    return found


def report_variant_tags(
    files: list[Path], profile, max_untagged: int | None = None
) -> tuple[int, bool]:
    """Print the variant-tag findings for ``files``.

    Returns ``(warnings, ratchet_failed)``. ``max_untagged`` is the CI ratchet: on a
    profile with several variants the untagged count may not grow past it, since every
    recording names its car now and the count only falls as history is tagged.
    """
    from canlib.variants import VariantsError, load_variants

    try:
        declared = set(load_variants(profile))
    except VariantsError:
        return 0, False  # `validate variants` reports a broken variants.yaml
    untagged, undeclared = variant_tag_findings(files, declared)
    try:
        bad_revisions = revision_tag_findings(files, profile)
    except Exception:
        bad_revisions = Counter()  # a broken ecus/ is `validate pids`'s to report
    for problem, n in sorted(bad_revisions.items()):
        hint = (
            ""
            if problem.startswith("a reply")
            else " — was the revision renamed or removed outside `canair ecu revisions`?"
        )
        print(f"  ⚠ {n} session(s) {'holding' if not hint else 'tagged with'} {problem}{hint}")
    for variant, n in sorted(undeclared.items()):
        known = ", ".join(sorted(declared)) or "none"
        print(
            f"  ⚠ {n} session(s) tagged with variant {variant!r}, which the profile does not "
            f"declare (declared: {known}) — retag with `captures uds --set-variant`"
        )
    several = len(declared) > 1
    failed = several and max_untagged is not None and untagged > max_untagged
    if several and untagged:
        ceiling = ""
        if max_untagged is not None:
            verb = "exceeds" if failed else "within"
            ceiling = f", {verb} the --max-untagged {max_untagged} baseline"
        print(
            f"  ({untagged} untagged session(s){ceiling}: no car named — tag them with "
            "`captures migrate-variants` or `captures uds --set-variant`)"
        )
    if failed:
        print(
            f"\n{untagged} untagged session(s) exceeds the --max-untagged {max_untagged} "
            "baseline — new recordings must name their car (--variant / profile use)."
        )
    return sum(undeclared.values()) + sum(bad_revisions.values()), failed
