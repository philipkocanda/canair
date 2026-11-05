"""Tests for canair.validation.memory — the memory: section and memory PIDs."""

from __future__ import annotations

import pytest

from canair.validation.ecu_file import validate_ecu_file
from canair.validation.schema_fields import load_schema

BCM = """\
BCM:
  tx_id: 0x7A0
  identity:
    id_protocol: UDS  # keep me
  pids:
    22B003:
      status: active
      parameters:
        V:
          expression: B04
"""


@pytest.fixture
def ecus_dir(tmp_path):
    (tmp_path / "bcm.yaml").write_text(BCM)
    return tmp_path


def _validate(ecus_dir, extra: str) -> tuple[list[str], list[str]]:
    (ecus_dir / "bcm.yaml").write_text(BCM.replace("  pids:\n", extra + "  pids:\n"))
    errors, warnings, _ = validate_ecu_file(ecus_dir / "bcm.yaml", load_schema())
    return errors, warnings


MEMORY_PID = """\
    "2324FEDE00000004":
      status: active
      parameters:
        RAM_VALUE:
          expression: B04
"""


class TestValidation:
    def test_ungated_memory_pid_warns(self, ecus_dir):
        errors, warnings = _validate(ecus_dir, '  memory:\n    format: "24"\n')
        assert errors == []
        (ecus_dir / "bcm.yaml").write_text(
            (ecus_dir / "bcm.yaml").read_text().replace("  pids:\n", "  pids:\n" + MEMORY_PID)
        )
        errors, warnings, _ = validate_ecu_file(ecus_dir / "bcm.yaml", load_schema())
        assert errors == []
        assert any("not gated to a revision" in w for w in warnings)

    def test_bad_format_errors(self, ecus_dir):
        errors, _ = _validate(ecus_dir, '  memory:\n    format: "2"\n')
        assert any("memory format" in e for e in errors)

    def test_oversized_and_shipping_memory_pid_errors(self, ecus_dir):
        big = MEMORY_PID.replace("00000004", "00000080").replace(
            "expression: B04", "expression: B04\n          ship: true"
        )
        (ecus_dir / "bcm.yaml").write_text(
            BCM.replace("  pids:\n", '  memory:\n    format: "24"\n  pids:\n' + big)
        )
        errors, _, _ = validate_ecu_file(ecus_dir / "bcm.yaml", load_schema())
        assert any("reads 128 bytes" in e for e in errors)
        assert any("ship: true on a memory read" in e for e in errors)

    def test_format_mismatch_errors(self, ecus_dir):
        (ecus_dir / "bcm.yaml").write_text(
            BCM.replace("  pids:\n", '  memory:\n    format: "13"\n  pids:\n' + MEMORY_PID)
        )
        errors, _, _ = validate_ecu_file(ecus_dir / "bcm.yaml", load_schema())
        assert any("memory format 24" in e for e in errors)
