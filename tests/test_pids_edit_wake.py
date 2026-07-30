"""Tests for canlib.pids_edit.set_wake (per-ECU wake-ritual editing)."""

import textwrap

import pytest
import yaml

from canlib.pids_edit import PidsEditError, set_wake


@pytest.fixture
def pids_dir(tmp_path):
    (tmp_path / "_meta.yaml").write_text('car_model: "Test"\ninit: "ATSP6;"\n')
    (tmp_path / "test.yaml").write_text(
        textwrap.dedent(
            """\
            # Header comment that must survive edits
            TESTECU:
              tx_id: 0x7A5
              can_bus: [B-CAN]
              identity:
                description: Test ECU
                id_protocol: UDS
              pids:
                2101:
                  status: active
                  parameters: {}
            """
        )
    )
    return tmp_path


def _ecu(pids_dir):
    return yaml.safe_load((pids_dir / "test.yaml").read_text())["TESTECU"]


def test_adds_full_block(pids_dir):
    set_wake(
        "TESTECU",
        {
            "method": "rapid_read",
            "prime_pid": "22B003",
            "attempts": 8,
            "interval_ms": 80,
            "sleep_timer_ms": 2000,
        },
        pids_dir=pids_dir,
    )
    wake = _ecu(pids_dir)["wake"]
    assert wake["method"] == "rapid_read"
    assert wake["prime_pid"] == "22B003"
    # Numeric knobs are real ints (not strings) so the schema int-check passes.
    assert wake["attempts"] == 8 and isinstance(wake["attempts"], int)
    assert wake["interval_ms"] == 80 and isinstance(wake["interval_ms"], int)
    assert wake["sleep_timer_ms"] == 2000
    text = (pids_dir / "test.yaml").read_text()
    assert "Header comment that must survive edits" in text
    # Placed with the other top-level ECU fields (after can_bus), before identity.
    assert text.index("wake:") < text.index("identity:")
    assert text.index("can_bus:") < text.index("wake:")


def test_method_only(pids_dir):
    set_wake("TESTECU", {"method": "session"}, pids_dir=pids_dir)
    wake = _ecu(pids_dir)["wake"]
    assert wake == {"method": "session"}


def test_replaces_in_place(pids_dir):
    set_wake("TESTECU", {"method": "rapid_read", "attempts": 4}, pids_dir=pids_dir)
    set_wake("TESTECU", {"method": "rapid_read", "attempts": 10}, pids_dir=pids_dir)
    wake = _ecu(pids_dir)["wake"]
    assert wake["attempts"] == 10
    assert (pids_dir / "test.yaml").read_text().count("wake:") == 1


def test_notes_folded(pids_dir):
    long = "This is a fairly long note about why this ECU needs a rapid-read wake " * 2
    set_wake("TESTECU", {"method": "rapid_read", "notes": long}, pids_dir=pids_dir)
    got = _ecu(pids_dir)["wake"]["notes"]
    assert " ".join(got.split()) == " ".join(long.split())


def test_rejects_missing_method(pids_dir):
    with pytest.raises(PidsEditError):
        set_wake("TESTECU", {"attempts": 4}, pids_dir=pids_dir)


def test_missing_ecu(tmp_path):
    (tmp_path / "_meta.yaml").write_text('car_model: "T"\ninit: "x"\n')
    with pytest.raises(PidsEditError):
        set_wake("NOPE", {"method": "rapid_read"}, pids_dir=tmp_path)
