"""Every stored hex payload at once, for the checks that only work across the corpus.

A misfiled reply (:mod:`canlib.misfiled`) or an unsplit multi-DID reply
(:mod:`canlib.unsplit_batches`) only shows against the rest of the corpus, so those checks need
every payload together. Loading it is the expensive part, so ``validate captures`` loads it once
here and hands the same corpus to each check; the repair that fixes unsplit replies loads it the
same way.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from canlib import capture_io
from canlib.misfiled import PayloadSample


@dataclass(frozen=True)
class Locator:
    """Where a sample lives on disk."""

    path: Path
    session: int
    capture: int
    time: str

    def describe(self, ecu: str, pid: str) -> str:
        """A warning's location column, e.g. ``sessions[0].captures[29] (MCU 21F2 @ 10:07)``."""
        return f"sessions[{self.session}].captures[{self.capture}] ({ecu} {pid} @ {self.time})"


@dataclass(frozen=True)
class PayloadCorpus:
    """Every hex payload capture, with ``where[sample.handle]`` locating each sample."""

    samples: list[PayloadSample]
    where: list[Locator]


def load_payload_corpus(files: list[Path]) -> PayloadCorpus:
    """Every hex payload capture in ``files``, in file/session/capture order."""
    from canlib.ecus import parse_ecu_ref

    samples: list[PayloadSample] = []
    where: list[Locator] = []
    for path in files:
        try:
            data = capture_io.load_capture_file(path)
        except (OSError, ValueError):
            continue  # unreadable or not JSON: the per-file schema check reports it
        if not isinstance(data, dict):
            continue
        for si, session in enumerate(data.get("sessions", []) or []):
            if not isinstance(session, dict):
                continue
            for ci, cap in enumerate(session.get("captures", []) or []):
                if not isinstance(cap, dict):
                    continue
                rx = parse_ecu_ref(capture_io.capture_rx(cap) or None)
                pid, payload = cap.get("pid"), cap.get("payload")
                if rx is None or not pid or not payload:
                    continue
                try:
                    raw = bytes.fromhex(str(payload))
                except ValueError:
                    continue  # the non-hex lint reports it
                samples.append(PayloadSample(len(samples), rx, str(pid), raw))
                where.append(Locator(path, si, ci, str(cap.get("time", "?"))))
    return PayloadCorpus(samples, where)
