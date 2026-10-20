"""Tests for ``canair search`` — real parser, captures written into ``tmp_path``."""

from __future__ import annotations

import argparse
import json

import pytest

import canlib.capture_store as capture_store
from canlib.byteindex import payload_to_wican_bytes
from canlib.cli import _inject_default_subcommand
from canlib.commands import search
from canlib.commands.search.findings import PayloadRow, inventory_payloads
from canlib.decode_value import decode_typed


def _payload(did: str, body: bytes) -> str:
    return (bytes.fromhex("62" + did) + body).hex().upper()


def _write(tmp_path, captures, *, session=None, name="2026-07-24"):
    doc = {
        "sessions": [
            {
                "date": name,
                "vehicle_states": ["ready"],
                "keep_mode": "unique",
                **(session or {}),
                "captures": captures,
            }
        ]
    }
    (tmp_path / f"{name}.json").write_text(json.dumps(doc))


def _cap(ecu, pid, payload, **extra):
    return {"ecu": ecu, "pid": pid, "payload": payload, "time": "09:00:00", **extra}


def _run(tmp_path, monkeypatch, argv):
    orig = capture_store.load_all_captures
    monkeypatch.setattr(
        "canlib.commands.search.uds.load_all_captures", lambda *a, **k: orig(tmp_path)
    )
    parser = search.add_parser(argparse.ArgumentParser().add_subparsers())
    args = parser.parse_args(_inject_default_subcommand(["search", *argv])[1:])
    return args.func(args)


@pytest.fixture
def corpus(tmp_path):
    ver = _payload("F188", b"95400G7470")
    # The string sits at a fixed offset; only its neighbours change between captures.
    mcu = _payload("21F2", b"\x01\x02 #EHEL-MS2\x00\x00")
    mcu_b = _payload("21F2", b"\x09\x02\x03\x04EHEL-MS2\x00\x00\x07")
    _write(
        tmp_path,
        [
            _cap("BCM", "22F188", ver),
            _cap("BCM", "22F188", ver),
            _cap("MCU", "21F2", mcu),
            _cap("MCU", "21F2", mcu_b),
            _cap("MCU", "21F2", mcu_b),
            _cap("VCU", "2101", _payload("0101", b"\x01\x02\x03\x04")),
        ],
        session={"label": "bench start", "notes": "checked KMHC marking on the plate"},
    )
    return tmp_path


class TestNeedle:
    def test_literal_is_widened_to_the_whole_string(self, corpus, monkeypatch, capsys):
        assert _run(corpus, monkeypatch, ["EHEL"]) == 0
        out = capsys.readouterr().out
        assert "'EHEL-MS2'" in out or "EHEL-MS2" in out
        assert "MCU" in out and "21F2" in out

    def test_one_row_per_location_not_per_capture(self, corpus, monkeypatch, capsys):
        assert _run(corpus, monkeypatch, ["95400G7470", "--json"]) == 0
        env = json.loads(capsys.readouterr().out)
        payload = [f for f in env["findings"] if f["source"] == "payload"]
        assert len(payload) == 1
        assert payload[0]["hits"] == 2  # two captures, one location
        assert payload[0]["ecu"] == "BCM" and payload[0]["pid"] == "22F188"

    def test_overlapping_runs_collapse_to_the_stable_core(self, corpus, monkeypatch, capsys):
        _run(corpus, monkeypatch, ["EHEL", "--json", "--in", "payload"])
        (finding,) = json.loads(capsys.readouterr().out)["findings"]
        assert finding["text"] == "EHEL-MS2"  # not ' #EHEL-MS2' nor 'EHEL-MS2\x07'
        assert finding["hits"] == 3
        assert finding["variants"] == 1

    def test_case_sensitive(self, corpus, monkeypatch, capsys):
        assert _run(corpus, monkeypatch, ["ehel", "--in", "payload", "--case-sensitive"]) == 1
        assert _run(corpus, monkeypatch, ["ehel", "--in", "payload"]) == 0

    def test_regex(self, corpus, monkeypatch, capsys):
        assert _run(corpus, monkeypatch, ["--regex", r"\d{5}G\d{4}", "--json"]) == 0
        env = json.loads(capsys.readouterr().out)
        assert [f["text"] for f in env["findings"] if f["source"] == "payload"] == ["95400G7470"]

    def test_hex_pattern_reports_exact_bytes(self, corpus, monkeypatch, capsys):
        assert _run(corpus, monkeypatch, ["--hex", "45 48 45 4C", "--json"]) == 0
        (finding,) = json.loads(capsys.readouterr().out)["findings"]
        assert finding["text"] == "4548454C"
        assert finding["length"] == 4
        assert finding["expression"] is None  # bytes are not an ascii run

    def test_vin_searches_payloads_and_text(self, tmp_path, monkeypatch, capsys):
        vin = "KMHC851HFHU012435"
        _write(
            tmp_path,
            [_cap("VCU", "1A90", (bytes([0x5A, 0x90]) + vin.encode()).hex())],
            session={"notes": f"typed {vin} by hand"},
        )
        assert _run(tmp_path, monkeypatch, ["--vin", "--json"]) == 0
        env = json.loads(capsys.readouterr().out)
        assert {f["source"] for f in env["findings"]} == {"payload", "text"}

    def test_no_match_exits_1(self, corpus, monkeypatch, capsys):
        assert _run(corpus, monkeypatch, ["nothing-like-this"]) == 1
        assert "No matches" in capsys.readouterr().out


class TestTextHaystack:
    def test_session_text_is_counted_once_per_session(self, corpus, monkeypatch, capsys):
        _run(corpus, monkeypatch, ["bench start", "--json"])
        (finding,) = json.loads(capsys.readouterr().out)["findings"]
        assert finding["field"] == "session label"
        assert finding["hits"] == 1  # six captures share the label; one session

    def test_in_payload_skips_text(self, corpus, monkeypatch):
        assert _run(corpus, monkeypatch, ["bench start", "--in", "payload"]) == 1

    def test_in_text_skips_payloads(self, corpus, monkeypatch, capsys):
        assert _run(corpus, monkeypatch, ["KMHC", "--in", "text", "--json"]) == 0
        env = json.loads(capsys.readouterr().out)
        assert {f["source"] for f in env["findings"]} == {"text"}

    def test_scan_notes_with_raw_hex_are_flagged_lossy(self, tmp_path, monkeypatch, capsys):
        scan = {"responding": [{"did": "F190", "response": "7E00", "notes": "Raw: 62F190AABB"}]}
        _write(tmp_path, [{"rx": "0x7EA", "pid": "F190", "scan_results": scan}])
        assert _run(tmp_path, monkeypatch, ["F190AA", "--json"]) == 0
        (finding,) = json.loads(capsys.readouterr().out)["findings"]
        assert finding["field"] == "scan notes"
        assert finding["lossy"] is True

    def test_non_hex_payload_is_never_decoded(self, tmp_path, monkeypatch):
        _write(tmp_path, [_cap("BCM", "22F188", "NO DATA")])
        assert _run(tmp_path, monkeypatch, ["NO", "--in", "payload"]) == 1


class TestInventory:
    def test_lists_plausible_strings_only(self, corpus, monkeypatch, capsys):
        assert _run(corpus, monkeypatch, ["--json"]) == 0
        env = json.loads(capsys.readouterr().out)
        assert env["mode"] == "inventory"
        assert {f["text"] for f in env["findings"]} == {"95400G7470", "EHEL-MS2"}

    def test_all_runs_disables_the_filter(self, tmp_path, monkeypatch, capsys):
        _write(tmp_path, [_cap("BCM", "22F188", _payload("F188", b"\x00&H&H&H&H\x00"))])
        assert _run(tmp_path, monkeypatch, ["--json"]) == 1
        capsys.readouterr()
        assert _run(tmp_path, monkeypatch, ["--all-runs", "--json"]) == 0

    def test_min_len(self, tmp_path, monkeypatch, capsys):
        _write(
            tmp_path,
            [
                _cap("BCM", "22F188", _payload("F188", b"95400G7470")),
                _cap("MCU", "21F2", _payload("21F2", b"\x00EHEL-MS2\x00")),
            ],
        )
        _run(tmp_path, monkeypatch, ["--min-len", "9", "--json"])
        env = json.loads(capsys.readouterr().out)
        assert [f["text"] for f in env["findings"]] == ["95400G7470"]


class TestExpressionRoundTrip:
    """Every emitted expression must decode back to the string it was found from.

    This is the assertion that makes the paste-ready command trustworthy, and the one
    that would catch a WiCAN/ISO-TP off-by-one.
    """

    @staticmethod
    def _round_trip(hex_payload: str, finding) -> str:
        wican = payload_to_wican_bytes(hex_payload)
        decoded = decode_typed({"type": "ascii", "expression": finding.expression}, wican)
        return decoded.text

    @pytest.mark.parametrize(
        "did,body,min_len",
        [
            ("F188", b"95400G7470", 6),  # multi-frame, spans the first PCI boundary
            ("F100", b"AEE MFC  AT EUR LHD 1.00 1.00 95740-G2200 161014", 6),  # many CFs
            ("1A80", b"\x00\x01M3062000307 EAEAAFP201\xaa\xaa", 6),
            ("21F2", b"\x00" * 60 + b"EHEL-MS2\x00\x00\x00", 6),  # deep offset
            ("F1", b"ABCDE", 3),  # single frame: one PCI byte, not two
        ],
    )
    def test_decodes_back_to_the_found_string(self, did, body, min_len):
        hex_payload = _payload(did, body)
        row = PayloadRow("ECU", "PID", bytes.fromhex(hex_payload), 1, "", "")
        (finding,) = inventory_payloads([row], min_len=min_len, plausible_only=True)
        assert finding.expression is not None
        assert self._round_trip(hex_payload, finding) == finding.text.strip()

    def test_group_mixing_frame_layouts_gets_no_expression(self):
        short = bytes.fromhex(_payload("F1", b"ABCDE"))  # single frame
        long_ = bytes.fromhex(_payload("F1", b"ABCDE" + b"\x00" * 8))  # multi-frame
        rows = [PayloadRow("E", "P", short, 1, "", ""), PayloadRow("E", "P", long_, 1, "", "")]
        (finding,) = inventory_payloads(rows, min_len=3, plausible_only=True)
        assert finding.expression is None


class TestScopeAndQuery:
    def test_query_narrows_to_an_ecu(self, corpus, monkeypatch, capsys):
        _run(corpus, monkeypatch, ["--query", "BCM", "--json", "--in", "payload"])
        env = json.loads(capsys.readouterr().out)
        assert {f["ecu"] for f in env["findings"]} == {"BCM"}

    def test_scope_flags_apply(self, corpus, monkeypatch):
        assert _run(corpus, monkeypatch, ["EHEL", "--date", "2026-07-24"]) == 0
        assert _run(corpus, monkeypatch, ["EHEL", "--date", "2020-01-01"]) == 1

    def test_defs_searches_identity_fields(self, corpus, monkeypatch, capsys):
        monkeypatch.setattr(
            "canlib.pids.load_pids",
            lambda *a, **k: {"ecus": {"BCM": {"identity": {"part_number": "95400G7470"}}}},
        )
        assert _run(corpus, monkeypatch, ["95400", "--defs", "--in", "payload", "--json"]) == 0
        env = json.loads(capsys.readouterr().out)
        assert "identity.part_number" in {f["field"] for f in env["findings"]}


class TestOutput:
    def test_default_limit_footer_is_loud(self, tmp_path, monkeypatch, capsys):
        caps = [
            _cap("BCM", f"22{n:04X}", _payload(f"{n:04X}", f"PART{n:05d}".encode()))
            for n in range(8)
        ]
        _write(tmp_path, caps)
        assert _run(tmp_path, monkeypatch, ["--limit", "3"]) == 0
        out = capsys.readouterr().out
        assert "5 more not shown" in out and "--limit 0" in out

    def test_limit_zero_shows_everything(self, tmp_path, monkeypatch, capsys):
        caps = [
            _cap("BCM", f"22{n:04X}", _payload(f"{n:04X}", f"PART{n:05d}".encode()))
            for n in range(8)
        ]
        _write(tmp_path, caps)
        _run(tmp_path, monkeypatch, ["--limit", "0", "--json"])
        env = json.loads(capsys.readouterr().out)
        assert env["matched"] == env["shown"] == 8 and env["truncated"] is False

    def test_json_envelope_reports_truncation(self, tmp_path, monkeypatch, capsys):
        caps = [
            _cap("BCM", f"22{n:04X}", _payload(f"{n:04X}", f"PART{n:05d}".encode()))
            for n in range(8)
        ]
        _write(tmp_path, caps)
        _run(tmp_path, monkeypatch, ["--limit", "3", "--json"])
        env = json.loads(capsys.readouterr().out)
        assert (env["matched"], env["shown"], env["truncated"], env["limit"]) == (8, 3, True, 3)
        assert {"profile", "mode", "pattern", "query", "hits", "findings"} <= set(env)

    def test_paste_ready_command_names_the_profile(self, corpus, monkeypatch, capsys):
        _run(corpus, monkeypatch, ["95400G7470", "--json", "--in", "payload"])
        (finding,) = json.loads(capsys.readouterr().out)["findings"]
        assert finding["command"].startswith("canair --profile ioniq-2017 pids upsert-param BCM")
        assert "'[B5:B15]'" in finding["command"] and "--type ascii" in finding["command"]

    def test_no_ansi_when_piped(self, corpus, monkeypatch, capsys):
        _run(corpus, monkeypatch, ["EHEL"])
        assert "\033[" not in capsys.readouterr().out

    def test_notation_changes_the_offset_label(self, corpus, monkeypatch, capsys):
        _run(corpus, monkeypatch, ["95400G7470", "--in", "payload", "--notation", "isotp"])
        out = capsys.readouterr().out
        assert "i3" in out and "(i3-12)" not in out  # no redundant ISO-TP range


class TestUsageErrors:
    @pytest.mark.parametrize(
        "argv",
        [
            ["abc", "--hex", "AA"],
            ["abc", "--vin"],
            ["--hex", "AA", "--vin"],
            ["--hex", "AA", "--regex"],
            ["--hex", "AA", "--case-sensitive"],
            ["--hex", "AA", "--in", "text"],
            ["--regex"],
            ["--regex", "("],
            ["--hex", "zz"],
            ["--defs"],
            ["--in", "text"],
        ],
    )
    def test_exit_code_2(self, corpus, monkeypatch, capsys, argv):
        assert _run(corpus, monkeypatch, argv) == 2
        assert "error:" in capsys.readouterr().err


class TestRouting:
    def test_bare_search_routes_to_uds(self):
        assert _inject_default_subcommand(["search", "VIN"]) == ["search", "uds", "VIN"]
        assert _inject_default_subcommand(["search"]) == ["search", "uds"]

    def test_help_and_explicit_kind_are_untouched(self):
        assert _inject_default_subcommand(["search", "--help"]) == ["search", "--help"]
        assert _inject_default_subcommand(["search", "uds", "x"]) == ["search", "uds", "x"]

    def test_a_pattern_named_can_is_a_pattern(self):
        # `can` is not a kind yet, so it must not be swallowed as one.
        assert _inject_default_subcommand(["search", "can"]) == ["search", "uds", "can"]
