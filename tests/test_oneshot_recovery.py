"""One-shot (discover/scan/raw) journal recovery keeps the session's shape and provenance.

A one-shot producer journals a complete session; ``captures uds --recover`` rebuilds it from
the journal. The ``[recovered]`` note it adds must land with the other metadata above
``captures`` (not after the whole array), and the session's own ``transport`` and each
discover responder's ``tx`` must survive the round trip.
"""

from __future__ import annotations

import json

from canlib.capture_journal import CaptureJournal, list_orphans, recover


def _discover_session(**extra) -> dict:
    return {
        "date": "2026-07-22",
        "label": "sweep",
        **extra,
        "captures": [
            {
                "rx": "broadcast",
                "pid": "discover 700-7EF",
                "time": "10:00:00",
                "scan_results": {
                    "responding": [{"tx": "0x740", "rx": "0x760", "response": "ABS (positive)"}]
                },
            }
        ],
    }


def _orphan(tmp_path, session: dict, **meta) -> None:
    journal = CaptureJournal.open(tmp_path, label="sweep", source="oneshot", **meta)
    journal.append_session(session)
    journal.flush()  # a killed session: on disk, never reconciled


def _recovered(tmp_path) -> dict:
    [path] = list_orphans(tmp_path)
    written = recover(path)
    assert written is not None
    return json.loads(written.read_text())["sessions"][0]


def test_the_recovered_note_lands_above_captures(tmp_path):
    _orphan(tmp_path, _discover_session())
    session = _recovered(tmp_path)
    assert session["notes"] == "[recovered]"
    assert list(session)[-1] == "captures"


def test_a_discover_sessions_tx_and_transport_survive(tmp_path):
    _orphan(tmp_path, _discover_session(transport="wican-ws"), transport="slcan-tcp")
    session = _recovered(tmp_path)
    # The session's own transport (read off the live client) is kept over the meta's.
    assert session["transport"] == "wican-ws"
    [entry] = session["captures"][0]["scan_results"]["responding"]
    assert (entry["tx"], entry["rx"]) == ("0x740", "0x760")
    assert list(session)[-1] == "captures"


def test_meta_transport_fills_a_session_without_one(tmp_path):
    _orphan(tmp_path, _discover_session(), transport="slcan-tcp")
    session = _recovered(tmp_path)
    assert session["transport"] == "slcan-tcp"
    assert list(session)[-1] == "captures"
