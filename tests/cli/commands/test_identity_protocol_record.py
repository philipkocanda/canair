"""``canair identity`` offers to record a protocol its reading proved, and writes only on a yes."""

from __future__ import annotations

import io

import pytest

from canair.cli import profile_context
from canair.cli.live.handlers.reads import offer_protocol_record
from canair.cli.profile_context import active
from canair.ecus.pids import clear_cache, load_raw_pids
from canair.live.identity.read import ProtocolFinding

PROFILE_YAML = 'car_model: "T"\ninit: "ATSP6;"\n'
ECU = "IGPM:\n  tx_id: 0x770\n  identity:\n    description: Gateway\n"
REVISED = (
    "VCU:\n  tx_id: 0x7E2\n  identity:\n    description: VCU\n"
    "  revisions:\n    KWP:\n      identity: {id_protocol: KWP2000}\n"
)
FINDING = ProtocolFinding(0x770, "uds", "3 UDS identity field(s) read, KWP2000 1A refused")


class _Stdin(io.StringIO):
    def __init__(self, text: str, tty: bool):
        super().__init__(text)
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


@pytest.fixture
def prof(tmp_path):
    saved = profile_context._active
    root = tmp_path / "prof"
    (root / "ecus").mkdir(parents=True)
    (root / "profile.yaml").write_text(PROFILE_YAML)
    (root / "ecus" / "igpm.yaml").write_text(ECU)
    profile_context.set_active(str(root))
    clear_cache()
    yield root
    profile_context._active = saved
    clear_cache()


def _recorded(ecu="IGPM"):
    clear_cache()
    return load_raw_pids(active())["ecus"][ecu]["identity"].get("id_protocol")


def test_yes_writes_the_protocol(prof, capsys):
    offer_protocol_record(FINDING, yes=True, as_json=False, profile=active())
    assert _recorded() == "UDS"
    out = capsys.readouterr().out
    assert "IGPM identity.id_protocol set to UDS" in out and str(prof) in out


def test_a_yes_at_the_question_writes_it(prof, monkeypatch):
    monkeypatch.setattr("sys.stdin", _Stdin("y\n", tty=True))
    offer_protocol_record(FINDING, yes=False, as_json=False, profile=active())
    assert _recorded() == "UDS"


def test_a_no_writes_nothing(prof, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", _Stdin("n\n", tty=True))
    offer_protocol_record(FINDING, yes=False, as_json=False, profile=active())
    assert _recorded() is None
    assert "Record id_protocol: UDS?" in capsys.readouterr().err


def test_without_a_terminal_a_hint_names_the_flag(prof, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", _Stdin("y\n", tty=False))
    offer_protocol_record(FINDING, yes=False, as_json=False, profile=active())
    assert _recorded() is None
    assert "pass --set-protocol to record it" in capsys.readouterr().err


def test_with_json_the_confirmation_goes_to_stderr(prof, capsys):
    offer_protocol_record(FINDING, yes=True, as_json=True, profile=active())
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "identity.id_protocol set to UDS" in captured.err


def test_a_revision_dependent_ecu_is_not_written(prof, capsys):
    (prof / "ecus" / "vcu.yaml").write_text(REVISED)
    clear_cache()
    offer_protocol_record(
        ProtocolFinding(0x7E2, "kwp", "evidence"), yes=True, as_json=False, profile=active()
    )
    assert _recorded("VCU") is None
    assert "depends on its revision" in capsys.readouterr().err


def test_the_question_names_the_value_it_replaces(prof, monkeypatch, capsys):
    (prof / "ecus" / "igpm.yaml").write_text(ECU + "    id_protocol: none\n")
    clear_cache()
    monkeypatch.setattr("sys.stdin", _Stdin("y\n", tty=True))
    offer_protocol_record(FINDING, yes=False, as_json=False, profile=active())
    assert "Record id_protocol: UDS, replacing none?" in capsys.readouterr().err
    assert _recorded() == "UDS"
