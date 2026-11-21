"""Single-attempt SecurityAccess with synthetic ECU responses."""

import asyncio
import json
import sys

import pytest

from canair.live.read.multi import mode_multi
from canair.live.read.security import unlock
from canair.profile.model import Profile
from canair.safety.blocklist import SessionPolicy
from canair.transport.session_manager import SessionManager
from canair.uds.protocol import KWP, UDS
from canair.uds.security import SecurityAccessError
from tests.support.fakes import FakeTerminal, nrc, ok


def test_fixed_key_unlock_once_and_restore_policy(tmp_path):
    key_file = tmp_path / "key"
    key_file.write_bytes(bytes.fromhex("A1B2"))
    key_file.chmod(0o600)
    terminal = FakeTerminal(responses={"2701": ok("67011234"), "2702A1B2": ok("6702")})
    original = terminal.session_policy
    sm = SessionManager(terminal)
    sm.track(0x720, 3)
    asyncio.run(unlock(sm, ecu="TEST", tx_id=0x720, level=1, provider=None, key_file=str(key_file)))
    assert terminal.addressed == [(0x720, "2701"), (0x720, "2702A1B2")]
    assert terminal.session_policy is original
    assert all(call["retries"] == 0 for call in terminal.uds_kwargs)
    assert terminal.uds_kwargs[0]["expected_echo"] == b"\x01"
    assert terminal.uds_kwargs[1]["expected_echo"] == b"\x02"


def test_rejected_seed_never_submits_key(tmp_path):
    key_file = tmp_path / "key"
    key_file.write_bytes(b"\xa1\xb2")
    key_file.chmod(0o600)
    terminal = FakeTerminal(responses={"2701": nrc(0x33)})
    sm = SessionManager(terminal)
    sm.track(0x720, 3)
    with pytest.raises(SecurityAccessError):
        asyncio.run(
            unlock(sm, ecu="TEST", tx_id=0x720, level=1, provider=None, key_file=str(key_file))
        )
    assert terminal.sent == ["2701"]


def test_no_session_sends_nothing():
    terminal = FakeTerminal()
    sm = SessionManager(terminal)
    with pytest.raises(SecurityAccessError):
        asyncio.run(unlock(sm, ecu="TEST", tx_id=0x720, level=1, provider=None, key_file=None))
    assert terminal.sent == []


@pytest.mark.parametrize("tx_id,level", [(0x7DF, 1), (0x720, 2), (0x720, 0xFF)])
def test_invalid_target_or_level_sends_nothing(tx_id, level):
    terminal = FakeTerminal()
    sm = SessionManager(terminal)
    sm.track(tx_id, 3)
    with pytest.raises(SecurityAccessError):
        asyncio.run(unlock(sm, ecu="TEST", tx_id=tx_id, level=level, provider=None, key_file=None))
    assert terminal.sent == []


def test_missing_key_source_is_rejected_before_seed(tmp_path):
    terminal = FakeTerminal()
    sm = SessionManager(terminal)
    sm.track(0x720, 3)
    with pytest.raises(SecurityAccessError):
        asyncio.run(
            unlock(
                sm,
                ecu="TEST",
                tx_id=0x720,
                level=1,
                provider=str(tmp_path / "missing"),
                key_file=None,
            )
        )
    assert terminal.sent == []


def test_zero_uds_seed_is_already_unlocked_without_key(tmp_path):
    key_file = tmp_path / "unused"
    key_file.write_bytes(b"\xa1\xb2")
    key_file.chmod(0o600)
    terminal = FakeTerminal(responses={"2701": ok("67010000")})
    terminal.session_policy = SessionPolicy(protocols={0x720: UDS})
    sm = SessionManager(terminal)
    sm.track(0x720, 3)
    result = asyncio.run(
        unlock(sm, ecu="TEST", tx_id=0x720, level=1, provider=None, key_file=str(key_file))
    )
    assert result == "already unlocked"
    assert terminal.sent == ["2701"]


def test_zero_kwp_seed_is_not_assumed_already_unlocked(tmp_path):
    key_file = tmp_path / "key"
    key_file.write_bytes(bytes.fromhex("A1B2"))
    key_file.chmod(0o600)
    terminal = FakeTerminal(responses={"2701": ok("67010000"), "2702A1B2": ok("6702")})
    terminal.session_policy = SessionPolicy(protocols={0x720: KWP})
    sm = SessionManager(terminal)
    sm.track(0x720, 0x90)
    result = asyncio.run(
        unlock(sm, ecu="TEST", tx_id=0x720, level=1, provider=None, key_file=str(key_file))
    )
    assert result == "accepted"
    assert terminal.sent == ["2701", "2702A1B2"]


def test_pipeline_unlock_then_read_saves_only_the_read(tmp_path, capsys):
    root = tmp_path / "profile"
    root.mkdir()
    (root / "ecus").mkdir()
    (root / "profile.yaml").write_text('car_model: "Test"\ninit: ""\n')
    profile = Profile("test", root)
    key_file = tmp_path / "key"
    key_file.write_bytes(bytes.fromhex("A1B2"))
    key_file.chmod(0o600)
    terminal = FakeTerminal(
        responses={
            "2701": ok("67011234"),
            "2702A1B2": ok("6702"),
            "2101": ok("6101ABCD"),
        }
    )
    data = {"ecus": {"TEST": {"tx_id": 0x720, "pids": {"2101": {"parameters": {}}}}}}
    asyncio.run(
        mode_multi(
            terminal,
            ["session TEST --mode 03", "unlock TEST --level 01", "query TEST:2101"],
            data,
            False,
            no_repl=True,
            save=True,
            label="synthetic",
            as_json=True,
            security_key_file=str(key_file),
            security_confirmed=True,
            profile=profile,
        )
    )
    output = capsys.readouterr()
    assert "1234" not in output.out + output.err
    assert "A1B2" not in output.out + output.err
    result = json.loads(output.out)["results"]
    assert result[0]["security_access"] == "accepted"
    assert result[1]["raw"] == "6101ABCD"
    saved = "".join(path.read_text() for path in profile.captures_dir.rglob("*.json"))
    assert "6101ABCD" in saved
    assert "1234" not in saved and "A1B2" not in saved


def test_provider_runs_between_seed_and_one_key_submission(tmp_path):
    program = tmp_path / "provider"
    program.write_text(
        f"#!{sys.executable}\n"
        "import json,sys\n"
        "request=json.load(sys.stdin)\n"
        "assert request['version'] == 1\n"
        "print(json.dumps({'version':1,'key':'A1B2'}))\n"
    )
    program.chmod(0o700)
    terminal = FakeTerminal(responses={"2701": ok("67011234"), "2702A1B2": ok("6702")})
    sm = SessionManager(terminal)
    sm.track(0x720, 3)
    result = asyncio.run(
        unlock(sm, ecu="TEST", tx_id=0x720, level=1, provider=str(program), key_file=None)
    )
    assert result == "accepted"
    assert terminal.sent == ["2701", "2702A1B2"]


def test_failed_keepalive_aborts_before_key_submission(tmp_path, monkeypatch, capsys):
    program = tmp_path / "provider"
    program.write_text(
        f"#!{sys.executable}\n"
        "import time\ntime.sleep(1)\n"
        'print(\'{"version": 1, "key": "A1B2"}\')\n'
    )
    program.chmod(0o700)
    terminal = FakeTerminal(
        responses={
            "2701": ok("67011234"),
            "3E00": {"ok": False, "error": "unexpected 1234"},
        }
    )
    sm = SessionManager(terminal, verbose=True)
    sm.track(0x720, 3)
    monkeypatch.setattr("canair.live.read.security.PROVIDER_KEEPALIVE_INTERVAL", 0.05)
    with pytest.raises(SecurityAccessError, match="keepalive failed"):
        asyncio.run(
            unlock(sm, ecu="TEST", tx_id=0x720, level=1, provider=str(program), key_file=None)
        )
    assert terminal.sent == ["2701", "3E00"]
    output = capsys.readouterr()
    assert "1234" not in output.out + output.err


def test_pipeline_stops_after_uncertain_key_result(tmp_path, capsys):
    root = tmp_path / "profile"
    root.mkdir()
    (root / "ecus").mkdir()
    (root / "profile.yaml").write_text('car_model: "Test"\ninit: ""\n')
    profile = Profile("test", root)
    key_file = tmp_path / "key"
    key_file.write_bytes(bytes.fromhex("A1B2"))
    key_file.chmod(0o600)
    terminal = FakeTerminal(responses={"2701": ok("67011234"), "2101": ok("6101ABCD")})
    data = {"ecus": {"TEST": {"tx_id": 0x720, "pids": {"2101": {"parameters": {}}}}}}
    with pytest.raises(SystemExit) as exc:
        asyncio.run(
            mode_multi(
                terminal,
                ["session TEST --mode 03", "unlock TEST --level 01", "query TEST:2101"],
                data,
                False,
                no_repl=True,
                security_key_file=str(key_file),
                security_confirmed=True,
                profile=profile,
            )
        )
    assert exc.value.code == 1
    assert terminal.sent.count("2702A1B2") == 1
    assert "2101" not in terminal.sent
    captured = capsys.readouterr()
    assert "outcome is unknown" in captured.err
    assert "1234" not in captured.out + captured.err
    assert "A1B2" not in captured.out + captured.err
