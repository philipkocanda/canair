"""Tests for `canair discover --register` (canlib.modes.discover._register_discovered)."""

from __future__ import annotations

import pytest
import yaml

from canlib import profile
from canlib.modes.discover import _register_discovered


@pytest.fixture(autouse=True)
def _restore_active_profile():
    """Don't let set_active() in these tests leak into other test modules."""
    saved = profile._active
    yield
    profile._active = saved


def _mk_profile(tmp_path):
    root = tmp_path / "prof"
    (root / "pids").mkdir(parents=True)
    (root / "captures").mkdir()
    (root / "pids" / "_meta.yaml").write_text('car_model: "T"\ninit: "ATSP6;"\n')
    (root / "ecus.yaml").write_text("ecus:\n")
    profile.set_active(str(root))
    return root


def _load(root) -> dict:
    return yaml.safe_load((root / "ecus.yaml").read_text())


ALIVE = [
    (0x7E0, "positive", "5001..."),
    (0x7A0, "NRC 0x11", "serviceNotSupported"),
]


class TestRegisterDiscovered:
    def test_registers_new_ecus(self, tmp_path):
        root = _mk_profile(tmp_path)
        _register_discovered(ALIVE, dry_run=False)
        ecus = _load(root)["ecus"]
        assert ecus[0x7E0]["name"] == "Unknown-7E0"
        assert ecus[0x7A0]["name"] == "Unknown-7A0"
        assert "Discovered" in ecus[0x7E0]["notes"]

    def test_no_id_protocol_guess(self, tmp_path):
        root = _mk_profile(tmp_path)
        _register_discovered(ALIVE, dry_run=False)
        # 10 01 can't distinguish UDS/KWP — must not fabricate id_protocol.
        assert "id_protocol" not in _load(root)["ecus"][0x7E0]

    def test_dry_run_writes_nothing(self, tmp_path):
        root = _mk_profile(tmp_path)
        before = (root / "ecus.yaml").read_text()
        _register_discovered(ALIVE, dry_run=True)
        assert (root / "ecus.yaml").read_text() == before

    def test_idempotent_skips_known(self, tmp_path):
        root = _mk_profile(tmp_path)
        _register_discovered(ALIVE, dry_run=False)
        after_first = (root / "ecus.yaml").read_text()
        _register_discovered(ALIVE, dry_run=False)
        assert (root / "ecus.yaml").read_text() == after_first

    def test_preserves_existing_named_ecu(self, tmp_path):
        root = _mk_profile(tmp_path)
        (root / "ecus.yaml").write_text(
            "ecus:\n  0x7E0:\n    name: ECM\n    id_protocol: UDS\n"
        )
        _register_discovered(ALIVE, dry_run=False)
        ecus = _load(root)["ecus"]
        assert ecus[0x7E0]["name"] == "ECM"  # not clobbered
        assert ecus[0x7A0]["name"] == "Unknown-7A0"  # new one added

    def test_handles_missing_ecus_file(self, tmp_path):
        root = tmp_path / "prof"
        (root / "pids").mkdir(parents=True)
        (root / "pids" / "_meta.yaml").write_text('car_model: "T"\ninit: "x"\n')
        profile.set_active(str(root))
        _register_discovered(ALIVE, dry_run=False)
        assert (root / "ecus.yaml").exists()
        assert _load(root)["ecus"][0x7E0]["name"] == "Unknown-7E0"
