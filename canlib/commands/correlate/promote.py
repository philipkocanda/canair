"""Writing a correlation result back out, and warning when it rests on too little.

``--promote`` persists the top raw-byte hit into ``ecus/`` as an enabled but
unverified candidate with its evidence in ``notes``; the thin-join warning is the
counterpart that says "this r is real, but it joined almost nothing".
"""

from __future__ import annotations

import sys

from canlib.align import (
    join_nearest,
)

NAME = "correlate"

_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[92m"
_CYAN = "\033[96m"
_YELLOW = "\033[93m"
_RESET = "\033[0m"


def _warn_thin_reference_join(command: str, ref_label: str, best_n: int, n_ref: int, args) -> None:
    """Print the shared thin/zero-join warning for an ``--against`` sweep."""
    from canlib.align import thin_join_warning

    msg = thin_join_warning(
        command=command,
        ref_label=ref_label,
        n_joined=best_n,
        n_candidates=n_ref,
        tol_s=args.join_tol,
        min_n=args.min_n,
    )
    if msg:
        print(msg, file=sys.stderr)


def _promote_top_byte(name, rows, series, ref_series, ref_label, tol, *, ref_unit=None) -> int:
    """Promote the strongest raw-byte hit vs the reference to a candidate param.

    Only raw bytes (``Bn``) are promotable — an already-defined param needs no
    promotion. Routes through the shared guarded write, with a fresh linear fit
    and unit guess added to the evidence notes.
    """
    import re

    from canlib.commands._promote import print_promoted, write_candidate
    from canlib.pids_edit import PidsEditError
    from canlib.xanalysis import linear_fit, sniff_unit

    byte_hit = None
    for sig, r, n in rows:
        parts = sig.split(":")
        if len(parts) == 3 and re.fullmatch(r"B\d+", parts[2]):
            byte_hit = (sig, parts[0], parts[1], parts[2], r, n)
            break
    if byte_hit is None:
        print(
            "Nothing to promote — no raw-byte hit in the ranked list. "
            "Re-run with --bytes so undecoded bytes are considered.",
            file=sys.stderr,
        )
        return 1

    sig, ecu, pid, expr, r, n = byte_hit
    xs, ys, _ = join_nearest(ref_series, series[sig], tol_s=tol)
    fit = linear_fit(xs, ys)
    fit_note = f", fit y={fit[0]:.4f}·x{fit[1]:+.2f}, resid={fit[2]:.2f}" if fit else ""
    unit = sniff_unit(xs, ys, ref_unit)
    unit_note = f" {unit}" if unit else ""
    notes = (
        f"Candidate from `canair correlate --against {ref_label}`: r={r:+.3f} (n={n})"
        f"{fit_note}.{unit_note} Enabled unverified — confirm scale/sign against reality."
    )
    try:
        fpath = write_candidate(
            ecu, pid, name, expr, source=f"canair correlate vs {ref_label}", notes=notes
        )
    except (PidsEditError, SystemExit) as e:
        print(f"promote failed: {e}", file=sys.stderr)
        return 1
    print_promoted(ecu, pid, name, expr, r, fpath)
    return 0
