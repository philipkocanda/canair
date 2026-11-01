"""``canair discover --functional``: the allow-list, reception on both transports, the report.

Device-free. The raw path is driven through a fake bus that answers the request
frame; the ELM327 path through the real engine over a queued channel; the mode
through the shared fake terminal and ``dispatch_mode``.
"""

from __future__ import annotations

import argparse
import json

import can
import pytest

from canlib import profile
from canlib.addressing import EcuAddress, physical_request_id
from canlib.commands import discover as discover_cmd
from canlib.modes.functional_sweep import classify_round, group_by_bus, round_to_capture
from canlib.pids import clear_cache
from canlib.safety import SafetyBlocked, check_functional_request
from canlib.transport.elm327_addressing import AdapterState
from canlib.transport.elm327_functional import elm_functional_exchange, parse_header_lines
from canlib.transport.elm327_terminal import Elm327Terminal
from canlib.transport.functional import (
    FunctionalCollector,
    FunctionalMessage,
    FunctionalResult,
    single_frame,
)
from canlib.transport.functional_raw import raw_functional_exchange

from ._fakes import FakeTerminal, QueuedChannel

# --------------------------------------------------------------------------- allow-list


class TestAllowList:
    @pytest.mark.parametrize(
        "req", ["3E00", "22F18C", "1A87", "190208", "1800FF00", "3e 00", "0100", "010D"]
    )
    def test_reads_are_allowed(self, req):
        assert check_functional_request(req) is None

    @pytest.mark.parametrize(
        "req",
        [
            "1001",  # session control, every sub-function
            "1003",
            "1081",
            "1101",  # reset
            "14FFFFFF",  # clear DTCs (UDS)
            "14FF00",  # clear DTCs (KWP)
            "2802",  # communication control
            "8502",  # DTC setting
            "2EF1900000",  # write
            "2FBC0303",  # IOControl
            "31010203",  # routine
            "3E80",  # suppressed TesterPresent: draws no replies
            "22F18CF18D",  # more than one DID
            "1901FF",  # a different ReadDTCInformation sub-function
            "2101",
            "04",  # OBD-II clear emission DTCs
            "01",  # mode 01 with no PID
            "010C0D",  # several mode 01 PIDs in one request
            "0902",  # mode 09 is not on the list
            "0800",  # OBD-II control of an on-board system
            "ZZ",
        ],
    )
    def test_everything_else_is_refused(self, req):
        assert check_functional_request(req) is not None

    def test_unsafe_does_not_widen_it(self):
        args = _discover_args(functional=True, request="1003", unsafe=True)
        assert "may not be sent functionally" in discover_cmd.functional_args_error(args)

    def test_help_and_docs_list_every_allowed_shape(self):
        import argparse
        from pathlib import Path

        from canlib.safety import FUNCTIONAL_READS

        parser = discover_cmd.add_parser(argparse.ArgumentParser().add_subparsers())
        epilog = " ".join((parser.epilog or "").split())
        doc = Path(__file__).parents[1] / "docs/bring-your-own-car/02-discover-ecus.md"
        text = " ".join(doc.read_text().split())
        for shape, _ in FUNCTIONAL_READS:
            assert shape in epilog, shape
            assert f"`{shape}`" in text, shape

    @pytest.mark.asyncio
    async def test_the_terminal_refuses_too(self):
        with pytest.raises(SafetyBlocked):
            await FakeTerminal().send_functional(0x7DF, "1101")

    @pytest.mark.asyncio
    async def test_only_a_functional_id_is_accepted(self):
        with pytest.raises(ValueError):
            await FakeTerminal().send_functional(0x7E4, "3E00")


def _discover_args(**kw) -> argparse.Namespace:
    base = {
        "functional": False,
        "request": None,
        "window": None,
        "repeat": 1,
        "interval": 1.0,
        "range": None,
        "unsafe": False,
    }
    base.update(kw)
    return argparse.Namespace(**base)


class TestArgumentChecks:
    def test_functional_flags_need_functional(self):
        assert "need --functional" in discover_cmd.functional_args_error(
            _discover_args(request="3E00")
        )

    def test_range_conflicts(self):
        args = _discover_args(functional=True, range="700-7EF")
        assert "--range" in discover_cmd.functional_args_error(args)

    def test_interval_minimum(self):
        args = _discover_args(functional=True, repeat=2, interval=0.1)
        assert "at least 1" in discover_cmd.functional_args_error(args)

    def test_defaults_pass(self):
        assert discover_cmd.functional_args_error(_discover_args(functional=True)) is None
        assert discover_cmd.functional_args_error(_discover_args()) is None


# --------------------------------------------------------------------------- collector


class TestCollector:
    def test_single_frames_from_several_ecus(self):
        c = FunctionalCollector(0x7DF, b"\x3e\x00", started=0.0, window=0.3)
        assert c.feed(0x7EC, bytes.fromhex("027E00AAAAAAAAAA"), 0.010) is False
        assert c.feed(0x778, bytes.fromhex("027E00AAAAAAAAAA"), 0.020) is False
        c.feed(0x7DF, bytes.fromhex("023E00AAAAAAAAAA"), 0.021)  # the request itself
        c.feed(0x18DAF110, bytes.fromhex("027E00"), 0.022)  # a 29-bit frame on an 11-bit sweep
        assert not c.done(0.2)
        assert c.done(0.31)
        result = c.result()
        assert [(m.rx_id, m.payload.hex()) for m in result.messages] == [
            (0x7EC, "7e00"),
            (0x778, "7e00"),
        ]
        assert result.messages[0].elapsed_ms == 10

    def test_a_first_frame_asks_for_flow_control_and_holds_the_window(self):
        c = FunctionalCollector(0x7DF, bytes.fromhex("22F18C"), started=0.0, window=0.3)
        assert c.feed(0x7EC, bytes.fromhex("100A62F18C313233"), 0.29) is True
        assert not c.done(0.35)  # mid-message: wait for the consecutive frame
        assert c.feed(0x7EC, bytes.fromhex("21343536373839AA"), 0.36) is False
        assert c.done(0.36)
        assert c.result().messages[0].payload == bytes.fromhex("62F18C31323334353637")

    def test_response_pending_extends_the_window(self):
        c = FunctionalCollector(0x7DF, bytes.fromhex("190208"), started=0.0, window=0.3)
        c.feed(0x7EC, bytes.fromhex("037F1978AAAAAAAA"), 0.05)
        assert not c.done(1.0)
        c.feed(0x7EC, bytes.fromhex("035902FFAAAAAAAA"), 1.2)
        assert [m.payload.hex() for m in c.result().messages] == ["7f1978", "5902ff"]

    def test_an_abandoned_message_is_lost_not_returned(self):
        c = FunctionalCollector(0x7DF, bytes.fromhex("22F18C"), started=0.0, window=0.3)
        c.feed(0x7EC, bytes.fromhex("100A62F18C313233"), 0.01)
        assert c.done(1.5)
        result = c.result()
        assert result.messages == [] and result.lost == 1

    def test_single_frame_is_dlc_8_with_the_padding(self):
        assert single_frame(b"\x3e\x00", 0x55) == bytes.fromhex("023E005555555555")
        with pytest.raises(ValueError):
            single_frame(bytes(8), 0xAA)


# --------------------------------------------------------------------------- raw path


class _ScriptedBus:
    """Answers the functional request like two ECUs, one of them multi-frame."""

    def __init__(self, notifier: _Notifier):
        self.notifier = notifier
        self.sent: list[can.Message] = []

    def send(self, msg: can.Message, timeout=None) -> None:
        self.sent.append(msg)
        if msg.arbitration_id == 0x7DF:
            self.notifier.deliver(0x7EC, "100A62F18C313233")
            self.notifier.deliver(0x778, "027E00AAAAAAAAAA")
        elif msg.data[0] == 0x30:  # flow control: send the rest
            self.notifier.deliver(0x7EC, "21343536373839AA")


class _Notifier:
    def __init__(self):
        self.listeners = []

    def add_listener(self, cb):
        self.listeners.append(cb)

    def remove_listener(self, cb):
        self.listeners.remove(cb)

    def deliver(self, can_id: int, hex_: str) -> None:
        msg = can.Message(arbitration_id=can_id, data=bytes.fromhex(hex_), is_extended_id=False)
        for cb in list(self.listeners):
            cb(msg)


class TestRawExchange:
    @pytest.mark.asyncio
    async def test_flow_control_goes_to_the_physical_request_id(self):
        notifier = _Notifier()
        bus = _ScriptedBus(notifier)
        by_tx = {0x7E4: EcuAddress(0x7E4, 0x7EC)}
        result = await raw_functional_exchange(
            bus,  # type: ignore[arg-type]
            notifier,  # type: ignore[arg-type]
            tx_id=0x7DF,
            request=bytes.fromhex("22F18C"),
            padding=0x55,
            window=0.05,
            flow_control_to=lambda rx: physical_request_id(rx, by_tx),
        )
        request, flow_control = bus.sent
        assert request.arbitration_id == 0x7DF
        assert bytes(request.data) == bytes.fromhex("0322F18C55555555")
        assert flow_control.arbitration_id == 0x7E4  # never 0x7DF
        assert bytes(flow_control.data) == bytes.fromhex("3000005555555555")
        assert {m.rx_id: m.payload.hex().upper() for m in result.messages} == {
            0x778: "7E00",
            0x7EC: "62F18C31323334353637",
        }
        assert notifier.listeners == []

    def test_an_unknown_responder_derives_its_request_id(self):
        assert physical_request_id(0x7C8, {}) == 0x7C0
        assert physical_request_id(0x18DAF110, {}, mode=_fixed_29()) == 0x18DA10F1

    def test_a_registered_flow_control_override_wins(self):
        by_tx = {0x7A0: EcuAddress(0x7A0, 0x7C0, fc_id=0x7A5)}
        assert physical_request_id(0x7C0, by_tx) == 0x7A5


def _fixed_29():
    from canlib.addressing import AddressingMode

    return AddressingMode.NORMAL_FIXED_29BIT


# --------------------------------------------------------------------------- ELM327 path


class TestElmPath:
    def test_header_lines_are_parsed_and_echoes_skipped(self):
        text = "3E00\r7EC 02 7E 00\r778027E00\r\rNO DATA\r>"
        assert parse_header_lines(text, extended=False) == [
            (0x7EC, bytes.fromhex("027E00")),
            (0x778, bytes.fromhex("027E00")),
        ]
        assert parse_header_lines("18DAF11002 7E 00", extended=True) == [
            (0x18DAF110, bytes.fromhex("027E00"))
        ]

    def test_adapter_state_tracks_headers(self):
        state = AdapterState()
        assert state.headers_command(True) == "ATH1"
        state.observe("ATH1", "OK")
        assert state.headers_command(True) is None
        assert state.headers_command(False) == "ATH0"
        state.forget()
        assert state.headers_command(False) == "ATH0"
        state.observe("ATZ", "ELM327 v1.5")
        assert state.headers_command(False) is None

    @pytest.mark.asyncio
    async def test_headers_on_for_the_request_and_off_after(self):
        ch = QueuedChannel(
            "OK\r\r>",  # ATSH7DF
            "OK\r\r>",  # ATFCSH7DF
            "OK\r\r>",  # ATH1
            "7EC037F3E12\r778027E00\r\r>",  # the request
            "OK\r\r>",  # ATH0
        )
        term = Elm327Terminal(ch, timeout=2.0)
        result = await term.send_functional(0x7DF, "3E00", 0.3)
        sent = [s.strip() for s in ch.sent]
        assert sent == ["ATSH7DF", "ATFCSH7DF", "ATH1", "3E00", "ATH0"]
        assert {m.rx_id: m.payload.hex().upper() for m in result.messages} == {
            0x7EC: "7F3E12",
            0x778: "7E00",
        }
        assert all(m.elapsed_ms is None for m in result.messages)
        assert term.headers_command(False) is None

    @pytest.mark.asyncio
    async def test_a_long_window_raises_the_adapter_wait_and_restores_it(self):
        ch = QueuedChannel(*["OK\r\r>"] * 4, "778027E00\r\r>", *["OK\r\r>"] * 2)
        term = Elm327Terminal(ch, timeout=2.0)
        await elm_functional_exchange(term, 0x7DF, b"\x3e\x00", window=0.9)
        sent = [s.strip() for s in ch.sent]
        assert sent[3:] == ["ATSTDC", "3E00", "ATH0", "ATST96"]


# --------------------------------------------------------------------------- report


ECUS = {
    0x7E4: {
        "name": "BMS",
        "rx_id": 0x7EC,
        "can_bus": ["P-CAN"],
        "address": EcuAddress(0x7E4, 0x7EC),
        "id_protocol": "KWP2000",
    },
    0x770: {
        "name": "IGPM",
        "rx_id": 0x778,
        "can_bus": ["B-CAN"],
        "address": EcuAddress(0x770, 0x778),
    },
    0x780: {
        "name": "AVN",
        "rx_id": 0x788,
        "can_bus": ["MM-CAN"],
        "address": EcuAddress(0x780, 0x788),
    },
}


def _result(*rows: tuple[int, str], request: str = "3E00") -> FunctionalResult:
    return FunctionalResult(
        0x7DF,
        bytes.fromhex(request),
        [FunctionalMessage(rx, bytes.fromhex(h), 10) for rx, h in rows],
    )


class TestReport:
    def test_grouped_by_bus_with_unknown_and_silent(self):
        sweep = classify_round(
            _result((0x7EC, "7F3E12"), (0x778, "7E00"), (0x7C8, "7E00"), (0x7E4, "2101")),
            ECUS,
        )
        groups = group_by_bus(sweep.responders)
        assert list(groups) == ["B-CAN", "P-CAN", "not in profile"]
        assert groups["P-CAN"][0].status == "NRC 0x12"
        unknown = groups["not in profile"][0]
        assert (unknown.known, unknown.tx_id) == (False, 0x7C0)
        assert [s.name for s in sweep.silent] == ["AVN"]
        assert [(o.rx_id, o.payload_hex) for o in sweep.other] == [(0x7E4, "2101")]

    def test_response_pending_is_counted_not_shown(self):
        sweep = classify_round(
            _result((0x7EC, "7F1978"), (0x7EC, "5902FF"), request="190208"), ECUS
        )
        (bms,) = sweep.responders
        assert (bms.status, bms.pending, bms.payload_hex) == ("positive", 1, "5902FF")

    def test_a_did_reply_must_echo_the_did(self):
        sweep = classify_round(
            _result((0x7EC, "62F18C31"), (0x778, "62F18D31"), request="22F18C"), ECUS
        )
        assert [r.name for r in sweep.responders] == ["BMS"]
        assert [o.rx_id for o in sweep.other] == [0x778]

    def test_capture_round_trips_through_validate_captures(self, tmp_path):
        from jsonschema import Draft202012Validator

        from canlib.captures import build_manual_session
        from canlib.captures import io as capture_io
        from canlib.commands.validate._common import CAPTURES_SCHEMA_FILE
        from canlib.commands.validate.captures import validate_captures_file

        sweep = classify_round(_result((0x7EC, "7E00"), (0x7C8, "7E00")), ECUS, time="12:00:00")
        capture = round_to_capture(sweep, 0x7DF, "3E00")
        assert capture["pid"] == "discover functional 7DF 3E00"
        entries = capture["scan_results"]["responding"]
        assert entries[0] == {
            "tx": "0x7E4",
            "rx": "0x7EC",
            "response": "BMS (positive)",
            "notes": "Raw: 7E00",
        }
        assert capture["scan_results"]["notes"] == "Silent: AVN, IGPM"
        session = build_manual_session(
            [capture], label="t", date="2026-10-31", transport="slcan-tcp"
        )
        path = tmp_path / "2026-10-31.json"
        capture_io.dump_capture_file(path, {"sessions": [session]})
        validator = Draft202012Validator(json.loads(CAPTURES_SCHEMA_FILE.read_text()))
        assert validate_captures_file(path, validator, set()) == []
        reloaded = capture_io.load_capture_file(path)["sessions"][0]["captures"][0]
        assert reloaded == capture


# --------------------------------------------------------------------------- the mode


@pytest.fixture
def gateway_profile(tmp_path):
    saved = profile._active
    root = tmp_path / "prof"
    (root / "ecus").mkdir(parents=True)
    (root / "captures").mkdir()
    (root / "profile.yaml").write_text('car_model: "T"\ninit: "ATSP6;"\n')
    for name, tx, bus in (
        ("BMS", "0x7E4", "P-CAN"),
        ("IGPM", "0x770", "B-CAN"),
        ("AVN", "0x780", "MM-CAN"),
    ):
        (root / "ecus" / f"{name.lower()}.yaml").write_text(
            f"{name}:\n  tx_id: {tx}\n  can_bus: [{bus}]\n"
        )
    profile.set_active(str(root))
    clear_cache()
    yield root
    profile._active = saved
    clear_cache()


def _mode_args(**kw) -> argparse.Namespace:
    from canlib.commands._live import CANAIR_DEFAULTS

    base = {
        **CANAIR_DEFAULTS,
        "discover": True,
        "functional": True,
        "request": None,
        "window": None,
        "repeat": 1,
        "interval": 1.0,
        "identify": False,
    }
    return argparse.Namespace(**{**base, **kw})


class TestMode:
    @pytest.mark.asyncio
    async def test_dispatch_reports_buses_unknowns_and_silent(self, gateway_profile, capsys):
        from canlib.modes.dispatch import dispatch_mode
        from canlib.pids import load_pids

        term = FakeTerminal()
        term.functional_replies = {"3E00": [(0x7EC, "7E00"), (0x778, "7E00"), (0x7C8, "7E00")]}
        await dispatch_mode(_mode_args(json=True), term, load_pids(), "1.2.3.4")
        assert term.calls[0] == ("send_functional", (0x7DF, "3E00", 0.3))
        out = capsys.readouterr()
        doc = json.loads(out.out)
        (rnd,) = doc["rounds"]
        assert {(r["name"], tuple(r["can_bus"])) for r in rnd["responders"]} == {
            ("BMS", ("P-CAN",)),
            ("IGPM", ("B-CAN",)),
            (None, ()),
        }
        assert [s["name"] for s in rnd["silent"]] == ["AVN"]
        assert "not in profile" in out.err and "MM-CAN: AVN" in out.err

    @pytest.mark.asyncio
    async def test_repeat_sends_each_round(self, gateway_profile, monkeypatch, capsys):
        from canlib.modes import discover_functional
        from canlib.modes.dispatch import dispatch_mode
        from canlib.pids import load_pids

        async def _no_sleep(_s):
            return None

        monkeypatch.setattr(discover_functional.asyncio, "sleep", _no_sleep)
        term = FakeTerminal()
        term.functional_replies = {"22F18C": [(0x7EC, "62F18C31")]}
        await dispatch_mode(_mode_args(request="22F18C", repeat=3), term, load_pids(), "1.2.3.4")
        assert [c for c in term.calls if c[0] == "send_functional"] == [
            ("send_functional", (0x7DF, "22F18C", 0.3))
        ] * 3
        assert "BMS: 3/3" in capsys.readouterr().out
