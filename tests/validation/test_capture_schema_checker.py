"""The row-caching capture schema check gives the same errors as plain jsonschema."""

import json

from jsonschema import Draft202012Validator

from canair.captures import io as capture_io
from canair.validation._common import CAPTURES_SCHEMA_FILE
from canair.validation.capture_schema import CaptureSchemaChecker

SCHEMA = json.loads(CAPTURES_SCHEMA_FILE.read_text())


def _doc(*rows):
    return {"sessions": [{"date": "2026-01-01", "label": "t", "captures": list(rows)}]}


def _row(**extra):
    return {"rx": "0x7EC", "pid": "2101", "payload": "6101AA", "time": "10:00:00", **extra}


def _plain(doc):
    return sorted(
        ((list(e.absolute_path), e.message) for e in Draft202012Validator(SCHEMA).iter_errors(doc)),
        key=lambda found: found[0],
    )


def _checked(doc, checker=None):
    checker = checker or CaptureSchemaChecker(SCHEMA)
    return sorted(((at, e.message) for at, e in checker.errors(doc)), key=lambda found: found[0])


def test_matches_plain_jsonschema_on_a_mixed_document():
    doc = _doc(
        _row(),
        _row(time="10:00:01", payload="6101BB"),
        _row(rx="ECU"),
        _row(elapsed_ms=-1),
        _row(bogus=1),
        {"rx": "0x7EC", "pid": "2101"},
        "not a row",
    )
    doc["sessions"].append({"date": "2026-01-01", "captures": "nope"})
    assert _checked(doc) == _plain(doc)
    assert _checked(doc)  # the document really has errors


def test_a_remembered_pass_does_not_hide_a_failing_row():
    checker = CaptureSchemaChecker(SCHEMA)
    assert checker.errors(_doc(_row(elapsed_ms=1))) == []
    # True == 1 and hashes alike, but "integer" rejects a boolean.
    bad = _doc(_row(elapsed_ms=True))
    assert _checked(bad, checker) == _plain(bad) != []


def test_a_failing_row_quotes_its_own_value():
    checker = CaptureSchemaChecker(SCHEMA)
    checker.errors(_doc(_row(rx="first")))
    [(at, message)] = _checked(_doc(_row(rx="second")), checker)
    assert at == ["sessions", 0, "captures", 0, "rx"]
    assert "'second'" in message


def test_reading_once_parses_a_file_once(tmp_path, monkeypatch):
    path = tmp_path / "2026-01-01.json"
    capture_io.dump_capture_file(path, _doc(_row()))
    parses = []
    real = capture_io.parse_capture_text
    monkeypatch.setattr(capture_io, "parse_capture_text", lambda t: parses.append(1) or real(t))

    with capture_io.reading_once():
        first = capture_io.load_capture_file(path)
        assert capture_io.load_capture_file(path) is first
    assert len(parses) == 1
    assert capture_io.load_capture_file(path) is not first
    assert len(parses) == 2
