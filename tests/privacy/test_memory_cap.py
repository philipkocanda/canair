"""A memory read bigger than a signal is a dump: never journaled, never saved, never shared."""

from __future__ import annotations

import pytest

from canair.captures import io as capture_io
from canair.cli import profile_context
from canair.cli.profile_context import active
from canair.ecus.pids import clear_cache
from canair.privacy import sensitive
from canair.uds.memory import MEMORY_SIGNAL_MAX_BYTES

META = 'car_model: "T"\ninit: "ATSP6;"\n'
BCM = """\
BCM:
  tx_id: 0x7A0
  identity:
    id_protocol: UDS
  memory:
    format: "24"
"""
SMALL = "2324FEDE00000004"  # 4 bytes: a signal
BIG = f"2324FEDE0000{MEMORY_SIGNAL_MAX_BYTES + 1:04X}"  # one byte over: a dump


@pytest.fixture(autouse=True)
def _reset():
    sensitive.take_withheld_note()
    yield
    sensitive.take_withheld_note()


@pytest.fixture
def prof(tmp_path):
    saved = profile_context._active
    root = tmp_path / "prof"
    (root / "captures").mkdir(parents=True)
    (root / "ecus").mkdir()
    (root / "profile.yaml").write_text(META)
    (root / "ecus" / "bcm.yaml").write_text(BCM)
    profile_context.set_active(str(root))
    clear_cache()
    yield root
    profile_context._active = saved
    clear_cache()


def _cap(pid: str, size: int) -> dict:
    return {"rx": "0x7A8", "pid": pid, "payload": "63" + "AB" * size, "time": "10:00:00.000"}


def test_save_session_drops_a_dump(prof, capsys):
    from canair.captures.save import save_session

    session = {
        "date": "2026-11-05",
        "label": "t",
        "captures": [_cap(SMALL, 4), _cap(BIG, MEMORY_SIGNAL_MAX_BYTES + 1)],
    }
    path = save_session(session, prof / "captures", profile=active())
    pids = [c["pid"] for s in capture_io.load_capture_file(path)["sessions"] for c in s["captures"]]
    assert pids == [SMALL]
    assert f"over {MEMORY_SIGNAL_MAX_BYTES} bytes" in capsys.readouterr().err


def test_the_journal_never_holds_a_dump(prof):
    from canair.captures.journal import CaptureJournal

    journal = CaptureJournal.open(prof / "captures", label="t", profile=active())
    journal.append("0x7A8", BIG, "63" + "AB" * (MEMORY_SIGNAL_MAX_BYTES + 1), "10:00:00.000")
    journal.append("0x7A8", SMALL, "63ABABABAB", "10:00:00.100")
    journal.append_response("0x7A8", BIG, "NRC 0x31")  # a refusal discloses nothing
    journal.flush()
    text = journal.path.read_text()
    assert text.count(BIG) == 1 and "NRC 0x31" in text and SMALL in text
    journal.reconcile()


def test_validate_captures_errors_on_a_stored_dump(prof):
    from canair.validation.captures import _oversized_memory_errors

    path = prof / "captures" / "2026-11-05.json"
    capture_io.dump_capture_file(
        path,
        {"sessions": [{"date": "2026-11-05", "label": "old", "captures": [_cap(BIG, 65)]}]},
    )
    errors = _oversized_memory_errors(path)
    assert len(errors) == 1 and "a dump" in errors[0]
