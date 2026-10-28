"""captures/ validation — multi-DID replies stored whole under one DID (cross-file pass).

Detection lives in ``canlib.uds.unsplit_batches``; this module words it and names the repair,
``canair captures split-batched``, which keeps the readings the row carried.
"""

from __future__ import annotations

from pathlib import Path

from canlib.captures.payload_corpus import PayloadCorpus, load_payload_corpus
from canlib.uds.unsplit_batches import UnsplitBatch, find_unsplit_batches

from .captures import CaptureWarning


def _message(u: UnsplitBatch) -> str:
    return (
        f"payload is a {len(u.parts)}-DID reply ({' + '.join(u.pids)}) stored whole as "
        f"{u.sample.pid.upper()}; split it into one capture per DID with "
        "`canair captures split-batched`"
    )


def unsplit_batch_warnings(
    files: list[Path],
    names: dict[int, str] | None = None,
    corpus: PayloadCorpus | None = None,
) -> dict[Path, list[CaptureWarning]]:
    """Per-file warnings for service-22 captures that hold a whole multi-DID reply.

    ``names`` and ``corpus`` work as in :func:`.misfiled.misfile_warnings`.
    """
    if names is None:
        from canlib.ecus import build_rx_index

        names = build_rx_index()
    if corpus is None:
        corpus = load_payload_corpus(files)
    out: dict[Path, list[CaptureWarning]] = {}
    for u in find_unsplit_batches(corpus.samples):
        at = corpus.where[u.sample.handle]
        ecu = names.get(u.sample.rx, hex(u.sample.rx))
        out.setdefault(at.path, []).append(
            CaptureWarning(at.describe(ecu, u.sample.pid), _message(u))
        )
    return out
