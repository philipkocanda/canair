"""`--json` on the sweeps and one-shot reads: stdout is one JSON document, nothing else.

The human report (headings, tables, progress, the save banner) goes to stderr, so a script can
pipe stdout to a JSON parser while a person watching the terminal still sees the report.
"""

from __future__ import annotations

import json

import pytest

from canair import profile
from canair.ecus.pids import clear_cache
from canair.live import discover as disc
from canair.live.raw import mode_raw
from canair.live.scan import mode_scan
from tests.support.fakes import FakeTerminal, ok

PROFILE_YAML = 'car_model: "T"\ninit: "ATSP6;"\n'
BMS = "BMS:\n  tx_id: 0x7E4\n  identity:\n    id_protocol: UDS\n"


@pytest.fixture
def prof(tmp_path):
    saved = profile._active
    root = tmp_path / "prof"
    (root / "ecus").mkdir(parents=True)
    (root / "captures").mkdir()
    (root / "profile.yaml").write_text(PROFILE_YAML)
    (root / "ecus" / "bms.yaml").write_text(BMS)
    profile.set_active(str(root))
    clear_cache()
    yield root
    profile._active = saved
    clear_cache()


class _OneAnswer(FakeTerminal):
    """Only 0x7E4 answers the 10 01 probe."""

    async def send_uds(self, cmd, timeout=None, **kw):
        if self.headers and self.headers[-1] == 0x7E4:
            return ok("5001")
        return {"ok": False, "error": "NO DATA", "raw": "NO DATA"}


@pytest.mark.asyncio
async def test_discover_json_stdout_is_only_the_document(prof, capsys):
    await disc.mode_discover(
        _OneAnswer(), (0x7E4, 0x7E5), verbose=False, as_json=True, delay=0.0, identify=False
    )
    captured = capsys.readouterr()
    doc = json.loads(captured.out)
    assert [a["tx"] for a in doc["alive"]] == ["0x7E4"]
    assert "ECU discovery" in captured.err
    assert "Cross-reference" in captured.err


@pytest.mark.asyncio
async def test_discover_json_register_report_is_not_on_stdout(prof, capsys):
    await disc.mode_discover(
        _OneAnswer(), (0x7E4, 0x7E5), verbose=False, as_json=True, delay=0.0, register=True
    )
    captured = capsys.readouterr()
    json.loads(captured.out)
    assert "Register" in captured.err


@pytest.mark.asyncio
async def test_discover_without_json_reports_on_stdout(prof, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    await disc.mode_discover(_OneAnswer(), (0x7E4, 0x7E5), verbose=False, as_json=False, delay=0.0)
    captured = capsys.readouterr()
    assert "Cross-reference" in captured.out


@pytest.mark.asyncio
async def test_scan_range_json_stdout_is_only_the_document(prof, capsys):
    term = FakeTerminal({"220100": ok("620100AA")})
    await mode_scan(term, 0x7E4, 0x22, (0x0100, 0x0100), False, True, save=True, label="scan")
    captured = capsys.readouterr()
    doc = json.loads(captured.out)
    assert doc["positive"][0]["did"] == "0x0100"
    assert "Scan results" in captured.err
    assert "Saved 1 capture(s)" in captured.err  # the save banner is a status line


@pytest.mark.asyncio
async def test_raw_json_keeps_the_request_banner_off_stdout(prof, capsys):
    term = FakeTerminal({"2101": ok("6101AA")})
    await mode_raw(term, "7E4:2101", False, True)
    captured = capsys.readouterr()
    assert json.loads(captured.out)["hex"] == "6101AA"
    assert "Request: 2101" in captured.err


@pytest.mark.asyncio
async def test_raw_save_banner_is_on_stderr(prof, capsys):
    term = FakeTerminal({"2101": ok("6101AA")})
    await mode_raw(term, "7E4:2101", False, True, save=True, label="raw")
    captured = capsys.readouterr()
    json.loads(captured.out)
    assert "Saved 1 capture(s)" in captured.err
