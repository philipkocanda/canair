"""captures/ validation — replies saved under the wrong ECU (cross-file pass).

The per-file lints in ``captures.py`` can't see this: a misfile only shows against the rest of
the corpus. Detection lives in ``canlib.misfiled``; this module words it.
"""

from __future__ import annotations

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


def misfile_warnings(
    files: list[Path],
    names: dict[int, str] | None = None,
    corpus: PayloadCorpus | None = None,
) -> dict[Path, list[CaptureWarning]]:
    """Per-file warnings for captures that look like another ECU's reply.

    ``names`` maps an RX address to an ECU name for the message; it defaults to the active
    profile's registry. ``corpus`` is the already-loaded payloads of ``files``, when the caller
    shares one load across several cross-file checks.
    """
    if names is None:
        from canlib.ecus import build_rx_index

        names = build_rx_index()
    if corpus is None:
        corpus = load_payload_corpus(files)
    out: dict[Path, list[CaptureWarning]] = {}
    for m in find_misfiled(corpus.samples):
        at = corpus.where[m.sample.handle]
        own = names.get(m.sample.rx, hex(m.sample.rx))
        location = at.describe(own, m.sample.pid)
        out.setdefault(at.path, []).append(CaptureWarning(location, _message(m, names)))
    return out
