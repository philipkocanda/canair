"""scripts/check/protocol_literals.py: which literals count as a re-declared protocol byte."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check" / "protocol_literals.py"

spec = importlib.util.spec_from_file_location("protocol_literals", SCRIPT)
assert spec and spec.loader
checker = importlib.util.module_from_spec(spec)
sys.modules["protocol_literals"] = checker
spec.loader.exec_module(checker)


def _scan(tmp_path: Path, source: str, module: str = "mod") -> list[str]:
    root = tmp_path / "canair"
    path = root / f"{module.replace('.', '/')}.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf8")
    return [f.kind for f in checker.scan(root)]


@pytest.mark.parametrize(
    ("source", "kind"),
    [
        ("ok = resp[0] == 0x7F\n", "compare"),
        ("ok = resp.get('nrc') in (0x13, 0x31)\n", "compare"),
        ("ok = response['nrc'] == 0x31\n", "compare"),
        ("ok = service_byte == 0x10\n", "compare"),
        ("sid = req + 0x40\n", "offset"),
        ("req = resp_bytes[0] - 0x40\n", "offset"),
        ("NRC_WRONG_SESSION = 0x7F\n", "constant"),
        ("_READ_SERVICE = 0x22\n", "constant"),
        ("_SERVICE_REFUSALS = frozenset({0x11, 0x7F, 0x80})\n", "constant"),
        ("class C:\n    SF_START = 0x01\n", "constant"),
        ("x = send('3E00')\n", "hex string"),
        ("hit = '7F2F78' in clean\n", "hex string"),
    ],
)
def test_flags_a_protocol_byte_written_as_a_literal(tmp_path, source, kind):
    assert _scan(tmp_path, source) == [kind]


@pytest.mark.parametrize(
    "source",
    [
        "t = sub & 0x7F\n",  # a mask
        "ok = 0x20 <= b < 0x7F\n",  # a byte range on a plain name
        "ok = 0 <= service <= 0xFF\n",  # a range bound
        "ok = resp[0] == 127\n",  # not written as hex
        "TIMEOUT_SID = 5\n",  # not a hex literal
        "DEFAULT_TX_PADDING = 0xAA\n",  # no protocol word in the name
        "def f():\n    NRC = 0x7F\n    return NRC\n",  # a local, not a declared constant
        '"""Replies start 7F."""\n',  # a docstring
        "def f():\n    '''3E00'''\n",  # a function docstring
        "x = '7F2'\n",  # not whole bytes
        "x = from_name(SERVICE) + RESPONSE_SID_OFFSET\n",  # read by name
    ],
)
def test_ignores_literals_that_are_not_re_declared_protocol_bytes(tmp_path, source):
    assert _scan(tmp_path, source) == []


def test_the_named_homes_are_not_scanned(tmp_path):
    assert _scan(tmp_path, "NEGATIVE_RESPONSE_SID = 0x7F\n", module="uds.services") == []
    assert _scan(tmp_path, "NRC_RESPONSE_PENDING = 0x78\n", module="uds.nrc") == []


def test_counts_group_findings_by_module(tmp_path):
    root = tmp_path / "canair"
    root.mkdir()
    (root / "a.py").write_text("NRC_X = 0x11\nok = resp[0] == 0x7F\n", encoding="utf8")
    (root / "b.py").write_text("x = '3E00'\n", encoding="utf8")
    assert checker.counts(checker.scan(root)) == {"canair.a": 2, "canair.b": 1}
