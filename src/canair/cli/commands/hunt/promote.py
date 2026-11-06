"""``hunt --promote``: write the winning hit into ``ecus/``."""

from __future__ import annotations

from canair.expression.inspect_bytes import NO_EXPR
from canair.render import messages
from canair.yaml_edit.pids import PidsEditError


def promote(name: str, ecu: str, pid: str, hits, ref_label: str) -> int:
    """Write the top hit as an unverified candidate param.

    Routes through the shared guarded write (``_promote.write_candidate``) — the
    same snapshot → edit → schema-validate → auto-revert gate as ``canair pids
    upsert-param`` — so a PCI-crossing expression is rejected and rolled back.
    """
    from canair.cli.commands._promote import print_promoted, write_candidate

    if not hits:
        messages.error("nothing to promote: no correlating byte found")
        return 1
    top = hits[0]
    if top.expr == NO_EXPR:
        messages.error(
            f"top hit ({top.interp} @ B{top.offset}) has no WiCAN expression "
            "(a float reinterpretation); cannot promote. Pick an expressible hit instead"
        )
        return 1
    unit_note = f" {top.unit_guess}" if top.unit_guess else ""
    notes = (
        f"Candidate from `canair hunt` vs {ref_label}: r={top.r:+.3f} (n={top.n}), "
        f"fit y={top.slope:.4f}·x{top.intercept:+.2f}, resid={top.resid:.2f}.{unit_note} "
        "Enabled unverified — confirm scale/sign against reality."
    )

    try:
        fpath = write_candidate(
            ecu, pid, name, top.expr, source=f"canair hunt vs {ref_label}", notes=notes
        )
    except (PidsEditError, SystemExit) as e:
        messages.error(f"promote failed: {e}")
        return 1
    print_promoted(ecu, pid, name, top.expr, top.r, fpath)
    return 0
