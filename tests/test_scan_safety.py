"""Safety of the KWP2000 scanners and the session-type scan (findings from review)."""

from __future__ import annotations

import asyncio
import io
import json
from types import SimpleNamespace

import pytest

from canlib.modes import kwp_iocontrol_scan, kwp_routines_scan
from canlib.modes.sessions_scan import mode_sessions_scan
from canlib.pids import normalize_session_mode
from tests._fakes import FakeTerminal, nrc

KWP_BCM = {"BCM": {"tx_id": 0x745, "identity": {"id_protocol": "KWP2000"}}}


def _data(quirks=None, ecus=None):
    data: dict = {"ecus": dict(ecus or KWP_BCM)}
    if quirks:
        data["quirks"] = quirks
    return data


# ── scan sessions ────────────────────────────────────────────────────────────


def test_session_scan_probes_c0_on_a_nissan_kwp_ecu():
    term = FakeTerminal(default=nrc(0x12))
    asyncio.run(
        mode_sessions_scan(
            term, _data(["nissan_c0_session"]), ["BCM"], throttle_ms=0, write_yaml=False
        )
    )
    assert term.sent == ["1081", "1082", "1083", "10C0"]


def test_session_scan_without_the_quirk_keeps_the_kwp_set():
    term = FakeTerminal(default=nrc(0x12))
    asyncio.run(mode_sessions_scan(term, _data(), ["BCM"], throttle_ms=0, write_yaml=False))
    assert term.sent == ["1081", "1082", "1083"]


def test_session_scan_skips_explicit_modes_the_guard_refuses(capsys):
    ecus = {"IGPM": {"tx_id": 0x770, "identity": {"id_protocol": "UDS"}}}
    term = FakeTerminal(default=nrc(0x12))
    asyncio.run(
        mode_sessions_scan(
            term,
            _data(ecus=ecus),
            ["IGPM"],
            modes=(0x01, 0x82, 0x03, 0x02),
            throttle_ms=0,
            write_yaml=False,
        )
    )
    assert term.sent == ["1001", "1003"]
    err = capsys.readouterr().err
    assert "skipping 10 82" in err and "skipping 10 02" in err


# ── KWP2000 IOControl scan ───────────────────────────────────────────────────


def _io_scan(data, **kw):
    term = FakeTerminal(**({"default": nrc(0x31)} | kw))
    asyncio.run(
        kwp_iocontrol_scan.mode_kwp_iocontrol_scan(
            term, data, ["BCM"], lid_range=(0x07, 0x07), throttle_ms=0, write_yaml=False
        )
    )
    return term


def test_unknown_make_gets_a_warning_but_the_same_probe(capsys):
    term = _io_scan(_data())
    assert term.sent == ["300700"]
    assert "not known to be non-actuating" in capsys.readouterr().err


@pytest.mark.parametrize("quirk", ["hk_f1xx_minus_one", "nissan_c0_session"])
def test_known_conventions_do_not_warn(quirk, capsys):
    _io_scan(_data([quirk]))
    assert "non-actuating" not in capsys.readouterr().err


def test_kwp_wrong_session_nrc_escalates_lazily_on_a_non_nissan_profile():
    # 0x80 serviceNotSupportedInActiveDiagnosticMode is a session problem, not a
    # hit: the engine opens the default session and retries.
    term = _io_scan(_data(["hk_f1xx_minus_one"]), responses={"300700": nrc(0x80)})
    assert term.sessions == [(False, "03")]
    assert term.sent == ["300700", "300700"]


# ── KWP2000 routine scan ─────────────────────────────────────────────────────


def test_nissan_routine_scan_is_refused_unconfirmed(capsys):
    term = FakeTerminal(default=nrc(0x31))
    out = asyncio.run(
        kwp_routines_scan.mode_kwp_routines_scan(
            term, _data(["nissan_c0_session"]), ["BCM"], throttle_ms=0, write_yaml=False
        )
    )
    assert out == {}
    assert term.sent == []
    assert "--yes" in capsys.readouterr().err


def test_nissan_routine_scan_runs_once_confirmed():
    term = FakeTerminal(default=nrc(0x31))
    asyncio.run(
        kwp_routines_scan.mode_kwp_routines_scan(
            term,
            _data(["nissan_c0_session"]),
            ["BCM"],
            lid_range=(0x01, 0x01),
            throttle_ms=0,
            write_yaml=False,
            confirmed=True,
        )
    )
    assert term.sent == ["3301"]


def test_other_profiles_need_no_confirmation():
    assert not kwp_routines_scan.needs_confirmation(_data(["hk_f1xx_minus_one"]))


class TestRoutinesScanDispatch:
    def _run(self, monkeypatch, *, yes, stdin):
        from canlib.modes.dispatch import scans

        calls: list[dict] = []

        async def _kwp(*a, **kw):
            calls.append(kw)
            return {}

        monkeypatch.setattr(scans, "split_ecus_by_protocol", lambda names: ([], ["BCM"]))
        monkeypatch.setattr(kwp_routines_scan, "mode_kwp_routines_scan", _kwp)
        monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
        args = SimpleNamespace(
            routines_scan=["BCM"], rid_range="00-FF", throttle_ms=0, verbose=False, yes=yes
        )
        asyncio.run(
            scans.handle_routines_scan(args, FakeTerminal(), _data(["nissan_c0_session"]), "h")
        )  # type: ignore[arg-type]
        return calls

    def test_declined_prompt_sends_nothing(self, monkeypatch):
        assert self._run(monkeypatch, yes=False, stdin="n\n") == []

    def test_non_interactive_without_yes_is_refused(self, monkeypatch):
        assert self._run(monkeypatch, yes=False, stdin="") == []

    def test_accepted_prompt_confirms(self, monkeypatch):
        calls = self._run(monkeypatch, yes=False, stdin="y\n")
        assert calls and calls[0]["confirmed"] is True

    def test_yes_skips_the_prompt(self, monkeypatch):
        calls = self._run(monkeypatch, yes=True, stdin="")
        assert calls and calls[0]["confirmed"] is True


def test_iocontrol_scan_defaults_uds_ecus_to_03_in_a_mixed_scan(monkeypatch):
    from canlib.modes.dispatch import scans

    seen: dict[str, object] = {}

    async def _uds(*a, **kw):
        seen["uds"] = kw["session_mode"]

    async def _kwp(*a, **kw):
        seen["kwp"] = kw["session_mode"]

    monkeypatch.setattr(scans, "split_ecus_by_protocol", lambda names: (["IGPM"], ["BMS"]))
    monkeypatch.setattr(scans, "mode_iocontrol_scan", _uds)
    monkeypatch.setattr(kwp_iocontrol_scan, "mode_kwp_iocontrol_scan", _kwp)
    args = SimpleNamespace(
        iocontrol_scan=["IGPM", "BMS"], did_range=None, throttle_ms=0, verbose=False
    )
    asyncio.run(scans.handle_iocontrol_scan(args, FakeTerminal(), {}, "h"))  # type: ignore[arg-type]
    # UDS falls back to 03; KWP gets None so its scanner picks the profile's session.
    assert seen == {"uds": "03", "kwp": None}


# ── session_mode normalization ───────────────────────────────────────────────


@pytest.mark.parametrize("value,expected", [(None, "03"), ("c0", "C0"), ("0x81", "81"), ("", "03")])
def test_normalize_session_mode_accepts_one_hex_byte(value, expected):
    assert normalize_session_mode(value) == expected


def test_an_unquoted_yaml_hex_int_is_rejected_with_a_quote_hint():
    # session_mode: 0xC0 loads as 192 and used to become the request 10192.
    with pytest.raises(ValueError, match="quote it"):
        normalize_session_mode(192)


@pytest.mark.parametrize("value", ["3", "1003", "zz", True])
def test_normalize_session_mode_rejects_anything_else(value):
    with pytest.raises(ValueError):
        normalize_session_mode(value)


def test_build_iocontrol_index_reports_a_bad_session_mode_as_a_profile_error():
    from canlib.pids import build_iocontrol_index
    from canlib.profile import ProfileError

    data = {"ecus": {"BCM": {"tx_id": 0x745, "iocontrol": {"6900": {"session_mode": 192}}}}}
    with pytest.raises(ProfileError, match="BCM/iocontrol/6900"):
        build_iocontrol_index(data)


# ── --json listing errors ────────────────────────────────────────────────────


@pytest.mark.parametrize("command", ["io", "routines"])
def test_json_listing_of_an_unknown_ecu_is_a_json_error(command, monkeypatch, capsys):
    import canlib
    from canlib.commands import io as io_cmd
    from canlib.commands import routines as routines_cmd

    monkeypatch.setattr(canlib, "load_pids", lambda: {"ecus": {}})
    mod = io_cmd if command == "io" else routines_cmd
    args = SimpleNamespace(json=True, did=None, rid=None, iocontrol="NOPE", routines="NOPE")
    assert mod.run(args) == 1
    out = json.loads(capsys.readouterr().out)
    assert "NOPE" in out["error"] and out["available"] == []
