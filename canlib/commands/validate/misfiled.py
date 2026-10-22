"""captures/ validation — replies saved under the wrong ECU (cross-file pass).

The per-file lints in ``captures.py`` can't see this: a misfile only shows against the rest of
the corpus. Detection lives in ``canlib.misfiled``; this module words it.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from canlib.misfiled import Misfile, find_misfiled
from canlib.payload_corpus import PayloadCorpus, load_payload_corpus

from .captures import CaptureWarning


def _message(m: Misfile, names: dict[int, str]) -> str:
    own = names.get(m.sample.rx, hex(m.sample.rx))
    likely = names.get(m.likely_rx, hex(m.likely_rx))
    pid = m.sample.pid
    if m.length_mismatch:
        evidence = (
            f"is {len(m.sample.payload)} bytes like {likely}'s {pid} replies "
            f"({m.likely_samples} captures), not {own}'s usual {m.own_length}"
        )
        if m.identical:
            evidence += f", and byte-identical to one of {likely}'s"
    else:
        evidence = f"is byte-identical to a {likely} {pid} reply"
    return (
        f"payload {evidence} — probably {likely}'s reply misfiled under {own}; "
        f"check it with `canair captures uds {own}:{pid} --diff --all` and remove it with "
        "`captures uds --delete`"
    )


def legacy_rx_aliases(ecus) -> dict[int, int]:
    """``legacy rx -> current rx`` for ECUs whose history may sit under ``TX + 0x08``.

    Before the profile ``addressing:`` block was honoured everywhere, a save could record
    the conventional ``TX + 0x08`` instead of the ECU's real response address, so one ECU's
    history is split across two RX values. Folding them stops the misfile check comparing
    the ECU with itself. An address that is some *other* ECU's real response address is
    never folded — that would hide exactly the misfile this check exists for.
    """
    from canlib.addressing import DEFAULT_RX_OFFSET
    from canlib.ecus import rx_for_tx

    current = {rx_for_tx(tx, ecus) for tx in ecus}
    out: dict[int, int] = {}
    for tx in ecus:
        legacy, rx = tx + DEFAULT_RX_OFFSET, rx_for_tx(tx, ecus)
        if legacy != rx and legacy not in current:
            out[legacy] = rx
    return out


def misfile_warnings(
    files: list[Path],
    names: dict[int, str] | None = None,
    corpus: PayloadCorpus | None = None,
    same_ecu: Mapping[int, int] | None = None,
) -> dict[Path, list[CaptureWarning]]:
    """Per-file warnings for captures that look like another ECU's reply.

    ``names`` maps an RX address to an ECU name for the message; it defaults to the active
    profile's registry, and then ``same_ecu`` defaults to that registry's
    :func:`legacy_rx_aliases`. ``corpus`` is the already-loaded payloads of ``files``, when
    the caller shares one load across several cross-file checks.
    """
    if names is None:
        from canlib.ecus import build_rx_index, load_ecus

        ecus = load_ecus()
        names = build_rx_index(ecus)
        if same_ecu is None:
            same_ecu = legacy_rx_aliases(ecus)
    if corpus is None:
        corpus = load_payload_corpus(files)
    out: dict[Path, list[CaptureWarning]] = {}
    for m in find_misfiled(corpus.samples, same_ecu):
        at = corpus.where[m.sample.handle]
        own = names.get(m.sample.rx, hex(m.sample.rx))
        location = at.describe(own, m.sample.pid)
        out.setdefault(at.path, []).append(CaptureWarning(location, _message(m, names)))
    return out
