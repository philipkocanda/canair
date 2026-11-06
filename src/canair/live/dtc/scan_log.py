"""Record ``canair dtc`` scans and clears to the DTC log (:mod:`canair.dtc.log`)."""

from __future__ import annotations

from canair.dtc.log import (
    append_clear,
    append_scan,
    build_clear,
    build_scan,
    clears_between,
    diff_scans,
    format_diff,
    latest_matching,
)
from canair.profile.model import Profile
from canair.render import ansi

from ...dtc.refs import ecu_ref


def _log_scan(
    scope: str, results: list[dict], label: str | None, vehicle_states=None, *, profile: Profile
):
    """Append this scan to the DTC log; return (path, previous_scan, diff).

    ``scope`` is ``all`` or the scanned ECU's reference (:func:`canair.dtc.refs.ecu_ref`).
    The diff is against the previous same-scope scan, net of any ``--clear`` since."""
    ecus: dict = {}
    unread: list[str] = []
    for r in results:
        rx = ecu_ref(int(r["tx"], 16), profile=profile)
        if "dtcs" not in r:
            unread.append(rx)
            continue
        codes = [d["dtc"] for d in r["dtcs"]]
        # Full sweep keeps only ECUs with codes (absence on a same-scope sweep =
        # cleared); a single-ECU scope keeps the ECU even when clean so a later
        # compare can see its codes clear.
        if scope != "all" or codes:
            ecus[rx] = {"protocol": r["protocol"], "dtcs": codes}
            snapshots = logged_snapshots(r["dtcs"])
            if snapshots:
                ecus[rx]["snapshots"] = snapshots

    current = build_scan(
        scope,
        ecus,
        label=label,
        vehicle_states=vehicle_states,
        unread=unread if scope == "all" else None,
    )
    previous = latest_matching(scope, profile=profile)
    diff = None
    if previous:
        since = clears_between(previous.get("timestamp", ""), current["timestamp"], profile=profile)
        diff = diff_scans(previous, current, since, profile=profile)
    path = append_scan(current, profile=profile)
    return path, previous, diff


def logged_snapshots(dtcs: list[dict]) -> dict[str, list[dict]]:
    """``{code: [{record, hex}]}`` for the codes whose snapshot replies were read.

    Only the raw replies are kept, as the log keeps only readings: they are decoded when
    the log is read, so a later definition improves an old entry. A record whose read
    failed has no reply to keep, and one holding a sensitive DID is never written
    (:mod:`canair.privacy.sensitive`).
    """
    out: dict[str, list[dict]] = {}
    for d in dtcs:
        records = [
            {"record": s["record"], "hex": s["hex"]}
            for s in d.get("snapshots") or ()
            if s.get("hex") and not s.get("sensitive")
        ]
        if records:
            out[d["dtc"]] = records
    return out


def _print_log_result(path, previous, diff, *, profile: Profile) -> None:
    if previous:
        for line in format_diff(diff, previous, profile=profile):
            print(line)
    else:
        print("  No previous scan of this scope — recorded as baseline.")
    print(f"  {ansi.DIM}Logged scan to {path} (`canair dtc log` to review){ansi.RESET}\n")


def _log_manual_clear(tx_id, proto, group, pre_codes, label, snapshots=None, *, profile: Profile):
    """Record a ``--clear``: readers take the ECU as clean from here until it is read.

    ``snapshots`` (``{code: [{record, hex}]}``) are the snapshots the clear erased, read
    just before it, since the clear leaves no other record of them."""
    append_clear(
        build_clear(
            ecu_ref(tx_id, profile=profile),
            protocol=proto,
            group=group,
            codes=pre_codes or None,
            label=label,
            snapshots=snapshots or None,
        ),
        profile=profile,
    )
