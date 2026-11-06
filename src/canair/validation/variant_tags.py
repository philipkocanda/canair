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

from canair.captures import io as capture_io

from .report import VariantTagReport


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
    from canair.ecus.pids import load_raw_pids
    from canair.ecus.registry import build_rx_index, ecu_name_from_ref
    from canair.variants import load_variants
    from canair.variants.revisions import canonical_ecu, gate, possible_revisions

    ecus = load_raw_pids(profile).get("ecus") or {}
    variants = load_variants(profile)
    rx_index = build_rx_index(profile=profile)
    pid_defs = {
        name: {
            str(k).upper(): d
            for k, d in reversed(list(((ecu_def or {}).get("pids") or {}).items()))
        }
        for name, ecu_def in ecus.items()
    }
    found: Counter[str] = Counter()
    for path in files:
        for session in (capture_io.load_capture_file(path) or {}).get("sessions") or []:
            tags = {str(e).upper(): r for e, r in (session.get("revisions") or {}).items()}
            outside: set[str] = set()
            for cap in session.get("captures") or []:
                rx = capture_io.capture_rx(cap)
                ecu = str(ecu_name_from_ref(rx, rx_index, profile=profile)) if rx else ""
                rid = tags.get(ecu.upper())
                pid_def = pid_defs.get(ecu, {}).get(str(cap.get("pid", "")).upper())
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


def variant_tag_report(
    files: list[Path], profile, max_untagged: int | None = None
) -> VariantTagReport:
    """The variant-tag findings for ``files``.

    ``max_untagged`` is the CI ratchet: on a profile with several variants the untagged
    count may not grow past it, since every recording names its car now and the count only
    falls as history is tagged.
    """
    from canair.variants import VariantsError, load_variants

    try:
        declared = set(load_variants(profile))
    except VariantsError:
        return VariantTagReport()  # `validate variants` reports a broken variants.yaml
    untagged, undeclared = variant_tag_findings(files, declared)
    try:
        bad_revisions = revision_tag_findings(files, profile)
    except Exception:
        bad_revisions = Counter()  # a broken ecus/ is `validate pids`'s to report
    problems: list[str] = []
    for problem, n in sorted(bad_revisions.items()):
        hint = (
            ""
            if problem.startswith("a reply")
            else " — was the revision renamed or removed outside `canair ecu revisions`?"
        )
        problems.append(
            f"{n} session(s) {'holding' if not hint else 'tagged with'} {problem}{hint}"
        )
    for variant, n in sorted(undeclared.items()):
        known = ", ".join(sorted(declared)) or "none"
        problems.append(
            f"{n} session(s) tagged with variant {variant!r}, which the profile does not "
            f"declare (declared: {known}) — retag with `captures uds --set-variant`"
        )
    several = len(declared) > 1
    return VariantTagReport(
        problems=tuple(problems),
        warnings=sum(undeclared.values()) + sum(bad_revisions.values()),
        untagged=untagged,
        several_variants=several,
        max_untagged=max_untagged,
        failed=several and max_untagged is not None and untagged > max_untagged,
    )
