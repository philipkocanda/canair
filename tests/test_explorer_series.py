"""Tests for the explorer's decoded capture series (canlib/explorer_series.py)."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from canlib import capture_io
from canlib.decoding import decode_param_rows
from canlib.explorer_export import export_profile
from canlib.explorer_series import (
    MIN_SAMPLES,
    _Clock,
    _clock_label,
    _number,
    _stream,
    export_sessions,
    rx_by_address,
)
from canlib.pids import load_pids

REPO_ROOT = Path(__file__).resolve().parent.parent
PROFILES = sorted(
    p
    for p in (REPO_ROOT / "profiles").iterdir()
    if (p / "ecus").is_dir() and (p / "captures").is_dir()
)

# Gzipped size ceilings: a future change must not quietly bloat the published site.
PROFILE_BUDGET_BYTES = 3_000_000
SESSION_BUDGET_BYTES = 400_000


def _gz(obj) -> int:
    return len(gzip.compress(json.dumps(obj, separators=(",", ":")).encode()))


@pytest.fixture(scope="module", params=PROFILES, ids=[p.name for p in PROFILES])
def exported(request):
    root: Path = request.param
    data = export_profile(root)
    index, docs = export_sessions(root, rx_by_address(data))
    return root, data, index, docs


def test_export_is_deterministic(exported):
    root, data, index, docs = exported
    again = export_sessions(root, rx_by_address(data))
    assert json.dumps((index, docs)) == json.dumps(again)


def test_index_and_documents_agree(exported):
    _root, _data, index, docs = exported
    assert [e["id"] for e in index] == list(docs)
    assert len({e["id"] for e in index}) == len(index)
    for entry in index:
        assert entry["samples"] >= MIN_SAMPLES
        assert set(entry["streams"]) == {
            f"{s['ecu']}:{s['pid']}" for s in docs[entry["id"]]["streams"]
        }


def test_documents_contain_only_decoded_values(exported):
    _root, _data, _index, docs = exported
    for doc in docs.values():
        assert set(doc) <= {"t0", "dur", "streams", "spans"}
        for stream in doc["streams"]:
            assert set(stream) == {"ecu", "pid", "t", "sig"}
            assert all(d >= 0 for d in stream["t"])
            for values in stream["sig"].values():
                assert isinstance(values, (int, float, list))
                if isinstance(values, list):
                    assert len(values) == len(stream["t"])
                    assert all(v is None or isinstance(v, (int, float)) for v in values)


def test_every_signal_is_defined_in_the_profile(exported):
    _root, data, _index, docs = exported
    defined = {
        (e["name"], p["id"]): {s["name"] for s in p["signals"]}
        for e in data["ecus"]
        for p in e["pids"]
    }
    for doc in docs.values():
        for stream in doc["streams"]:
            assert set(stream["sig"]) <= defined[(stream["ecu"], stream["pid"])]


def test_spans_lie_inside_the_session(exported):
    _root, _data, _index, docs = exported
    for doc in docs.values():
        for span in doc.get("spans", []):
            assert 0 <= span["t"] <= doc["dur"] + 1000


def test_values_match_the_shared_decoder(exported):
    root, data, index, docs = exported
    if not index:
        pytest.skip("profile has no plottable sessions")
    rx_names = rx_by_address(data)
    ecus = load_pids(root / "ecus")["ecus"]
    # The first timed capture that feeds the first stream of the first session.
    entry = index[0]
    stream = docs[entry["id"]]["streams"][0]
    target = (stream["ecu"], stream["pid"])
    for path in capture_io.iter_capture_files(root / "captures"):
        for session in capture_io.load_capture_file(path)["sessions"]:
            if session.get("date") != entry["date"]:
                continue
            for cap in session["captures"]:
                ecu = (
                    rx_names.get(int(capture_io.capture_rx(cap), 16))
                    if cap.get("payload")
                    else None
                )
                if (ecu, str(cap.get("pid"))) != target or not cap.get("time"):
                    continue
                params = next(
                    p["parameters"] for k, p in ecus[ecu]["pids"].items() if str(k) == target[1]
                )
                expected = {r[0]: _number(r[1]) for r in decode_param_rows(cap["payload"], params)}
                for name, values in stream["sig"].items():
                    got = values[0] if isinstance(values, list) else values
                    assert got == expected[name]
                return
    pytest.fail("no source capture found for the first stream")


def test_size_budget(exported):
    _root, _data, index, docs = exported
    assert sum(_gz(d) for d in docs.values()) + _gz(index) < PROFILE_BUDGET_BYTES
    assert all(_gz(d) < SESSION_BUDGET_BYTES for d in docs.values())


def test_clock_survives_midnight():
    clock = _Clock()
    assert clock("23:59:58.000") == 86_398_000
    assert clock("23:59:59.500") == 86_399_500
    assert clock("00:00:01.000") == 86_401_000
    assert clock("junk") is None


def test_clock_label_wraps_a_day():
    assert _clock_label(86_401_000) == "00:00:01"
    assert _clock_label(37_321_480) == "10:22:01"


def test_constant_signal_is_stored_once_and_gaps_stay_null():
    stream = _stream(
        "BMS", "2101", [0, 1000, 2000], [{"A": 5, "B": 1.5}, {"A": 5, "B": None}, {"A": 5, "B": 2}]
    )
    assert stream is not None
    assert stream["sig"] == {"A": 5, "B": [1.5, None, 2]}
    assert stream["t"] == [0, 1000, 1000]


def test_numbers_are_rounded_and_non_numbers_dropped():
    assert _number(3.14159265) == 3.14159
    assert _number(12.0) == 12
    assert _number(True) is None
    assert _number(float("nan")) is None
    assert _number("text") is None
