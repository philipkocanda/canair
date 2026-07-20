"""Tests for validate-pids.py PCI-byte detection (check_pci_bytes)."""

import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "validate_pids", Path(__file__).resolve().parent.parent / "validate-pids.py"
)
validate_pids = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(validate_pids)
check_pci_bytes = validate_pids.check_pci_bytes


def _warn(expr):
    return check_pci_bytes(expr, "P", "2101", "ECU")


class TestPciByteDetection:
    @pytest.mark.parametrize("expr", ["B8*0.02", "B16", "S24", "B32/2", "B0", "B1"])
    def test_flags_single_pci_byte(self, expr):
        assert _warn(expr), f"{expr} should be flagged as PCI"

    @pytest.mark.parametrize("expr", ["B9*0.02", "B7", "S10", "B44*0.02", "B17:0"])
    def test_passes_valid_single_byte(self, expr):
        assert not _warn(expr), f"{expr} should NOT be flagged"

    def test_flags_range_spanning_pci(self):
        # [B7:B9] includes B8 (PCI); [B15:B17] includes B16
        assert _warn("[B7:B9]/10")
        assert _warn("[B15:B17]/10")

    def test_passes_clean_range(self):
        assert not _warn("[B18:B19]/10")
        assert not _warn("[S12:S13]/100")
        assert not _warn("[B45:B46]/100")

    def test_message_is_clear(self):
        msg = _warn("B8*0.02")[0]
        assert "PCI" in msg and "B8" in msg


class TestRealPidsHaveNoPciBytes:
    """The shipped pids/ must not read PCI bytes."""

    def test_no_pci_in_any_pid(self):
        import glob

        import yaml

        offenders = []
        for path in glob.glob(str(Path(__file__).resolve().parent.parent / "pids" / "*.yaml")):
            data = yaml.safe_load(open(path))
            if not isinstance(data, dict):
                continue
            for ecu, ecud in data.items():
                if not isinstance(ecud, dict) or "pids" not in ecud:
                    continue
                for pid, pidd in (ecud["pids"] or {}).items():
                    for pname, pmeta in ((pidd or {}).get("parameters") or {}).items():
                        if not isinstance(pmeta, dict):
                            continue
                        expr = pmeta.get("expression", "") or ""
                        offenders += check_pci_bytes(expr, pname, str(pid), ecu)
        assert not offenders, "PCI bytes referenced:\n" + "\n".join(offenders)
