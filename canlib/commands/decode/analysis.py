"""The analysis views: discriminability, mirrors, and correlations.

The ranked/comparative tables — ``--discriminate``, ``--find-mirrors``,
``--corr`` — as opposed to the value views in :mod:`.views`. These are the ones
that render **byte labels**, so they are the renderers the notation goldens in
``tests/test_analysis_golden.py`` pin.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from canlib import ansi
from canlib.align import longest_payload_len
from canlib.inspect_bytes import apply_transform
from canlib.mirrors import DEFAULT_MIRROR_MATCH
from canlib.notation import ByteNotation, relabel_signal
from canlib.states import load_states, state_bucket_key
from canlib.stats import correlation as _correlation
from canlib.xanalysis import byte_state_buckets as _byte_state_buckets
from canlib.xanalysis import discriminability as _discriminability

from .calc import _local_series, _paired, _paired_timed, _transform_series, find_mirrors
from .format import _mark_for

if TYPE_CHECKING:
    from canlib.align import TimePoint


def print_discriminate(
    all_results: list[dict],
    param_names: list[str],
    parameters: dict,
    candidate_names: set[str],
    field: str,
    *,
    include_bytes: bool = False,
    include_bits: bool = False,
    notation: ByteNotation = ByteNotation.WICAN,
    sub_bytes: int = 1,
    group_of: Callable[[dict], str | None] | None = None,
) -> None:
    """Rank params (and optionally raw bytes/bits) by how cleanly they separate
    across ``field`` groups.

    The confirmation lever for state-dependent signals (thermal/mode/relay) that
    a driving-anchor correlation misses — e.g. MCU inverter temp reads distinctly
    across charging/ready/driving. Uses an F-like between/within variance ratio.

    With ``include_bytes`` (``--bytes``) every varying non-PCI raw byte is ranked
    alongside the params; with ``include_bits`` (``--bits``) so is every varying
    bit (``Bn:k``) — finding a state-dependent byte/bit without a ``--try``.

    ``group_of`` overrides the grouping key per result — the arbitrary-axis
    generalization (bucket by a cross-signal's discretized value, not the vehicle
    state). It defaults to the session vehicle-state key; a capture it maps to
    ``None`` (e.g. no axis sample within tolerance) is dropped from every bucket.
    """

    rules = load_states() if group_of is None else []
    key_cache: dict[tuple[str, ...], str] = {}

    def _grp(r: dict) -> str | None:
        if group_of is not None:
            return group_of(r)
        raw = tuple(str(s).upper() for s in r["capture"].get("vehicle_states") or [])
        hit = key_cache.get(raw)
        if hit is None:
            hit = state_bucket_key(raw, rules)
            key_cache[raw] = hit
        return hit

    buckets: dict[str, dict[str, list[float]]] = {name: {} for name in param_names}
    # Parallel categorical view: for typed enum/bitmask params, collect the
    # nominal category per capture alongside its group, so we can score them with
    # Cramér's V (F assumes interval scale — invalid for a mode/flag set).
    cat_pairs: dict[str, tuple[list, list]] = {}
    for r in all_results:
        key = _grp(r)
        if key is None:
            continue
        for name in param_names:
            d = r["decoded"].get(name, {})
            v = d.get("value")
            if v is not None:
                buckets[name].setdefault(key, []).append(v)
            cat = d.get("category")
            if cat is not None:
                xs, ys = cat_pairs.setdefault(name, ([], []))
                xs.append(cat)
                ys.append(key)

    byte_names: set[str] = set()
    if include_bytes or include_bits:
        byte_buckets = _byte_state_buckets(
            all_results, field, include_bits=include_bits, group_of=group_of
        )
        byte_names = set(byte_buckets)
        buckets.update(byte_buckets)

    rows = []
    for name in list(buckets):
        score = _discriminability(buckets[name])
        rows.append((name, score, buckets[name]))
    rows.sort(key=lambda t: (t[1] is None, -(t[1] if t[1] is not None else 0)))

    hdr_extra = ""
    if include_bytes and include_bits:
        hdr_extra = " (params + bytes + bits)"
    elif include_bytes:
        hdr_extra = " (params + bytes)"
    elif include_bits:
        hdr_extra = " (params + bits)"
    print(
        f"  {ansi.BOLD}Discriminability by {field}{hdr_extra}{ansi.RESET} "
        f"{ansi.DIM}(numeric: between/within variance F; categorical: Cramér's V — "
        f"higher = cleaner separation){ansi.RESET}"
    )
    plen = longest_payload_len([r.get("capture") for r in all_results])
    for name, score, groups in rows:
        if name in byte_names:
            mark = f"{ansi.DIM}·{ansi.RESET}"
        else:
            mark = _mark_for(name, parameters, candidate_names)
        disp = relabel_signal(name, notation, sub_bytes=sub_bytes, payload_len=plen)
        try_tag = f" {ansi.CYAN}(try){ansi.RESET}" if name in candidate_names else ""

        # Categorical params: report Cramér's V vs state (nominal association)
        # instead of the interval-scale F, which doesn't apply to a mode/flag set.
        if name in cat_pairs:
            from canlib.stats import cramers_v

            xs, ys = cat_pairs[name]
            v = cramers_v(xs, ys)
            if v is None:
                print(f"    {mark} {disp}{try_tag}  {ansi.DIM}V=n/a{ansi.RESET}")
                continue
            vcolor = ansi.GREEN if v >= 0.6 else ansi.YELLOW if v >= 0.3 else ansi.DIM
            distinct = "  ".join(sorted({str(c) for c in xs})[:6])
            print(
                f"    {mark} {disp}{try_tag}  {vcolor}V={v:.2f}{ansi.RESET}  "
                f"{ansi.DIM}(categorical: {distinct}){ansi.RESET}"
            )
            continue

        if score is None:
            print(f"    {mark} {disp}{try_tag}  {ansi.DIM}F=n/a{ansi.RESET}")
            continue
        color = (
            ansi.GREEN
            if score >= 10 or score == float("inf")
            else ansi.YELLOW
            if score >= 2
            else ansi.DIM
        )
        fstr = "∞" if score == float("inf") else f"{score:.1f}"
        means = "  ".join(f"{g}={sum(v) / len(v):.1f}" for g, v in groups.items() if v)
        print(
            f"    {mark} {disp}{try_tag}  {color}F={fstr}{ansi.RESET}  {ansi.DIM}{means}{ansi.RESET}"
        )
    print()


def print_mirrors(
    all_results: list[dict],
    *,
    bits: bool = False,
    notation: ByteNotation = ByteNotation.WICAN,
    sub_bytes: int = 1,
    min_fraction: float = DEFAULT_MIRROR_MATCH,
    allow_offset: bool = False,
) -> None:
    """Print byte/bit mirrors (redundant signals) found across this PID's captures."""
    mirrors = find_mirrors(
        all_results, bits=bits, min_fraction=min_fraction, allow_offset=allow_offset
    )
    what = "positions equal" if not allow_offset else "positions equal up to an offset/scale"
    print(
        f"  {ansi.BOLD}Mirrors{ansi.RESET} {ansi.DIM}({what} in ≥{min_fraction * 100:.0f}% of captures){ansi.RESET}"
    )
    if not mirrors:
        print(f"    {ansi.DIM}none{ansi.RESET}")
        print()
        return
    plen = longest_payload_len([r.get("capture") for r in all_results])
    for hit in mirrors:
        da = relabel_signal(hit.a, notation, sub_bytes=sub_bytes, payload_len=plen)
        db = relabel_signal(hit.b, notation, sub_bytes=sub_bytes, payload_len=plen)
        rel = hit.relation
        print(
            f"    {ansi.GREEN}{da} == {rel.describe(db)}{ansi.RESET}  {ansi.DIM}({rel.quality()}){ansi.RESET}"
        )
    print()


def print_correlations(
    all_results: list[dict],
    param_names: list[str],
    parameters: dict,
    candidate_names: set[str],
    ref: str,
    *,
    cross_ref_series: list[TimePoint] | None = None,
    cross_ref_label: str | None = None,
    tol_s: float | None = None,
    transform: str | None = None,
    method: str = "pearson",
) -> None:
    """Correlation of every parameter against ``ref`` across captures.

    The key reverse-engineering lever: correlate a candidate expression against
    a known signal (e.g. a torque guess vs MCU_MOTOR_RPM) to confirm it tracks.

    When ``cross_ref_series`` (a list[TimePoint] from another ECU/PID) is given,
    every local param is time-aligned against it by nearest timestamp instead of
    the fast same-payload positional pairing. ``transform`` optionally reshapes
    the reference series first (e.g. ``delta`` to test level vs rate); ``method``
    selects pearson (linear) or spearman (monotone/rank).
    """
    from canlib.align import DEFAULT_JOIN_TOL_S, join_nearest

    cross = cross_ref_series is not None
    ref_label = cross_ref_label if cross else ref
    tol = tol_s if tol_s is not None else DEFAULT_JOIN_TOL_S

    if cross and transform and transform != "raw":
        cross_ref_series = _transform_series(cross_ref_series, transform)

    rows = []
    for name in param_names:
        if not cross and name == ref:
            continue
        if cross:
            local = _local_series(all_results, name)
            # cross implies cross_ref_series is not None (set together above)
            assert cross_ref_series is not None
            # join_nearest(ref, cand): keep ref as the external signal
            xs, ys, n = join_nearest(cross_ref_series, local, tol_s=tol)
            r = _correlation(xs, ys, method)
            rows.append((name, r, n))
        else:
            if transform and transform != "raw":
                # delta/cumsum are order-sensitive: pair in time order, not
                # capture-list order, so the reference transform is meaningful.
                xs, ys = _paired_timed(all_results, ref, name)
                xs = apply_transform(xs, transform)
            else:
                xs, ys = _paired(all_results, ref, name)
            r = _correlation(xs, ys, method)
            rows.append((name, r, len(xs)))
    # Strongest absolute correlations first; undefined (None) last.
    rows.sort(key=lambda t: (t[1] is None, -abs(t[1]) if t[1] is not None else 0))

    coeff = {
        "spearman": "Spearman ρ",
        "cramers_v": "Cramér's V",
        "mutual_info": "norm. MI",
    }.get(method, "Pearson r")
    header = f"  {ansi.BOLD}Correlation vs {ref_label}{ansi.RESET} {ansi.DIM}({coeff}"
    if transform and transform != "raw":
        header += f", ref {transform}"
    if cross:
        header += f", nearest-join ≤{tol:g}s"
    header += f"){ansi.RESET}"
    print(header)
    for name, r, n in rows:
        mark = _mark_for(name, parameters, candidate_names)
        try_tag = f" {ansi.CYAN}(try){ansi.RESET}" if name in candidate_names else ""
        if r is None:
            print(f"    {mark} {name}{try_tag}  {ansi.DIM}r=n/a  n={n}{ansi.RESET}")
            continue
        color = ansi.GREEN if abs(r) >= 0.7 else ansi.YELLOW if abs(r) >= 0.3 else ansi.DIM
        print(
            f"    {mark} {name}{try_tag}  {color}r={r:+.3f}{ansi.RESET}  {ansi.DIM}n={n}{ansi.RESET}"
        )
    print()
