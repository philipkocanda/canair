"""Tests for validate-pids.py PCI-byte detection (check_pci_bytes)."""

import pytest

from canlib.commands import validate as validate_pids

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
    """The shipped ecus/ must not read PCI bytes."""

    def test_no_pci_in_any_pid(self):
        import glob

        import yaml

        offenders = []
        from canlib.profile import active

        for path in glob.glob(str(active().ecus_dir / "*.yaml")):
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


class TestDuplicateParamNames:
    """_duplicate_param_errors flags a shipped signal name used by >1 PID."""

    def _write(self, tmp_path, second_status="active", second_enabled=True):
        import textwrap

        (tmp_path / "a.yaml").write_text(
            textwrap.dedent(
                """\
                ECUA:
                  tx_id: 0x7E0
                  pids:
                    2101:
                      status: active
                      parameters:
                        SHARED:
                          expression: "B3"
                          verified: true
                """
            )
        )
        (tmp_path / "b.yaml").write_text(
            textwrap.dedent(
                f"""\
                ECUB:
                  tx_id: 0x7E1
                  pids:
                    2102:
                      status: {second_status}
                      parameters:
                        SHARED:
                          expression: "B3"
                          verified: true
                          enabled: {"true" if second_enabled else "false"}
                """
            )
        )
        return sorted(tmp_path.glob("*.yaml"))

    def test_flags_duplicate_shipped_name(self, tmp_path):
        errs = validate_pids._duplicate_param_errors(self._write(tmp_path))
        assert any("SHARED" in e and "ECUB 2102" in e for e in errs)

    def test_ignores_when_second_pid_not_active(self, tmp_path):
        errs = validate_pids._duplicate_param_errors(self._write(tmp_path, second_status="draft"))
        assert not errs

    def test_ignores_when_second_param_disabled(self, tmp_path):
        errs = validate_pids._duplicate_param_errors(self._write(tmp_path, second_enabled=False))
        assert not errs
