"""Detecting an ambiguous ECU's revision at the start of a live session.

An ECU whose car may carry several of its revisions, with no pin and no remembered
detection, is *ambiguous*: its revision-specific PIDs would be skipped. Before the
session's mode runs, :func:`detect_revisions` sends each ambiguous ECU the command is
about to read its cheapest ``responds:`` fingerprint — one safe identity read per ECU,
never per PID, through the terminal's usual safety policy and echo validation — and
settles the revision when exactly one matches. When none of those answers and a
revision has a ``software:`` fingerprint, it reads the ECU's software version
(:mod:`canair.live.identity.software`), which is how a memory PID's revision is told. The
result is remembered (:mod:`canair.variants.detected`), so later sessions and offline
analysis need no probe.

A sleeping ECU answers nothing and is left undetermined: no guess, as without detection.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable, Sequence

from canair.ecus.memory import protocol_of
from canair.profile import Profile
from canair.safety.blocklist import SafetyBlocked
from canair.transport.protocol import Terminal

from .identity.software import read_software_version


def _tokens(steps: Iterable[str]) -> set[str]:
    """Every upper-cased word of the query steps, split at ``:`` too (ECU:PID)."""
    out: set[str] = set()
    for step in steps:
        for word in re.split(r"[\s,]+", str(step)):
            out.update(part.upper() for part in word.split(":") if part)
    return out


def targeted_ecus(steps: Sequence[str] | None, *, profile: Profile) -> set[str] | None:
    """Upper-cased names the command's query steps mention, or None when it has none.

    Only the multi mini-language (``read``/``monitor`` steps) decodes revision-gated
    PIDs, so only it probes; a group reference is expanded first.
    """
    if not steps:
        return None
    steps = list(steps)
    if any("@" in str(s) for s in steps):
        from canair.ecus.ecu_groups import expand_group_refs, load_groups

        try:
            steps = expand_group_refs(steps, load_groups(profile=profile))
        except Exception:
            pass
    return _tokens(steps)


async def _ask(terminal: Terminal, tx_id: int, request: str) -> bool | None:
    """Whether the ECU answered ``request`` positively (None: it could not be asked)."""
    from canair.safety.blocklist import SafetyBlocked
    from canair.uds.echo import request_echo

    echo = request_echo(request)
    sid = echo[0] if echo else int(request[:2], 16)
    try:
        async with terminal.transaction():
            await terminal.set_header(tx_id)
            resp = await terminal.send_uds(
                request, expected_sid=sid, expected_echo=echo[1] if echo else None
            )
    except (SafetyBlocked, ValueError):
        return None
    return bool(resp.get("ok"))


async def detect_revisions(
    terminal: Terminal, targets: set[str] | None, *, profile: Profile
) -> Profile | None:
    """Probe each ambiguous ECU in ``targets``; return ``profile`` carrying what was settled.

    None when no revision was settled. The caller adopts the returned profile and reloads
    the definitions with it, since a settled revision changes which PIDs exist.
    """
    from canair.ecus.pids import load_raw_pids, revision_resolutions
    from canair.variants.detected import remember
    from canair.variants.fingerprints import detected, match, probes, wants_software
    from canair.variants.selection import with_detections
    from canair.variants.tags import selected_variant

    if targets is None:
        return None
    prof = profile
    ambiguous = [
        r
        for r in revision_resolutions(prof).values()
        if r.revision is None and len(r.possible) > 1 and r.ecu.upper() in targets
    ]
    if not ambiguous:
        return None
    ecus = load_raw_pids(prof).get("ecus") or {}
    settled = False
    for res in ambiguous:
        ecu_def = ecus.get(res.ecu) or {}
        tx_id = ecu_def.get("tx_id")
        requests = probes(ecu_def, res.possible)
        by_software = wants_software(ecu_def, res.possible)
        if tx_id is None or not (requests or by_software):
            continue
        responded: dict[str, bool] = {}
        for request in requests:
            answer = await _ask(terminal, int(tx_id), request)
            if answer is not None:
                responded[request] = answer
            if answer:
                break  # one positive answer is the evidence; don't spend another read
        software = None
        if by_software and not any(responded.values()):
            software = await _software(terminal, int(tx_id), ecu_def)
        rid = detected(
            match(ecu_def, possible=res.possible, responded=responded, software=software)
        )
        if rid is None:
            asked = [*requests, *(["the software version"] if by_software else [])]
            print(
                f"note: {res.ecu}: no fingerprint of {', '.join(res.possible)} answered "
                f"({', '.join(asked)}), so its revision stays undetermined.",
                file=sys.stderr,
            )
            continue
        remember(prof.name, selected_variant(prof), res.ecu, rid)
        which = next((r for r, ok in responded.items() if ok), f"software {software}")
        print(f"note: {res.ecu} is revision {rid} (it answered {which}).", file=sys.stderr)
        settled = True
    return with_detections(prof) if settled else None


async def _software(terminal: Terminal, tx_id: int, ecu_def: dict) -> str | None:
    """The ECU's software version for a ``software:`` fingerprint (None: no answer)."""
    try:
        return await read_software_version(terminal, tx_id, protocol_of(ecu_def))
    except (SafetyBlocked, ValueError):
        return None


def identity_report(tx_id: int, responded: dict[str, bool], *, profile: Profile) -> dict | None:
    """Which revision an identity reading of ``tx_id`` matches, and which cars allow it.

    Matched across every declared revision and every variant, so it answers "which car
    do I have?" with no variant chosen. A single match is remembered (replacing an older
    detection it contradicts). None when the ECU declares no revisions.
    """
    from canair.ecus.pids import load_raw_pids
    from canair.variants import load_variants
    from canair.variants.detected import remember, remembered
    from canair.variants.fingerprints import detected, match
    from canair.variants.revisions import declared
    from canair.variants.tags import selected_variant

    prof = profile
    ecus = load_raw_pids(prof).get("ecus") or {}
    name, ecu_def = next(
        ((n, d) for n, d in ecus.items() if isinstance(d, dict) and d.get("tx_id") == tx_id),
        (None, None),
    )
    if name is None or ecu_def is None:
        return None
    revs = declared(ecu_def)
    if len(revs) < 2:
        return None
    matches = match(ecu_def, responded=responded)
    rid = detected(matches)
    variants = load_variants(prof)
    allowing = [
        vid for vid, v in variants.items() if rid and rid in (v.revisions.get(name) or revs)
    ]
    report = {"ecu": name, "revisions": revs, "matches": matches, "variants": allowing}
    if rid:
        car = selected_variant(prof)
        before = remembered(prof.name, car).get(name)
        if remember(prof.name, car, name, rid) and before:
            report["replaced"] = before
    return report


def describe_identity_report(report: dict) -> list[str]:
    ecu, matches = report["ecu"], report["matches"]
    if len(matches) == 1:
        cars = ", ".join(report["variants"]) or "none of the profile's variants"
        lines = [f"  Revision: {ecu} is {matches[0]} — the variant(s) that allow it: {cars}"]
        if report.get("replaced"):
            lines.append(f"  (replaces the remembered detection {report['replaced']})")
        return lines
    if matches:
        return [f"  Revision: matches several of {ecu}'s revisions ({', '.join(matches)})"]
    return [
        f"  Revision: matches none of {ecu}'s revisions ({', '.join(report['revisions'])}) — "
        "possibly a revision the profile doesn't know yet; check before declaring one."
    ]
