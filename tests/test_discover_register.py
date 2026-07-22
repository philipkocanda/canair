"""Tests for `canair discover --register` (canlib.modes.discover._register_discovered)."""

from __future__ import annotations

import pytest
import yaml

from canlib import profile
from canlib.pids import clear_cache


@pytest.fixture(autouse=True)
def _restore_active_profile():
    """Don't let set_active() in these tests leak into other test modules."""
    saved = profile._active
    clear_cache()
    yield
    profile._active = saved
    clear_cache()


def _mk_profile(tmp_path, seed_ecus: str | None = None):
    root = tmp_path / "prof"
    (root / "ecus").mkdir(parents=True)
    (root / "captures").mkdir()
    (root / "profile.yaml").write_text('car_model: "T"\ninit: "ATSP6;"\n')
    if seed_ecus:
        (root / "ecus" / "ecm.yaml").write_text(seed_ecus)
    profile.set_active(str(root))
    clear_cache()
    return root


def _load(root, filename) -> dict:
    return yaml.safe_load((root / "ecus" / filename).read_text())


ALIVE = [
    (0x7E0, "positive", "5001..."),
    (0x7A0, "NRC 0x11", "serviceNotSupported"),
]


class TestRegisterDiscovered:
    def test_registers_new_ecus(self, tmp_path):
        from canlib.modes.discover import _register_discovered

        root = _mk_profile(tmp_path)
        _register_discovered(ALIVE, dry_run=False)
        e0 = _load(root, "unknown-7e0.yaml")["Unknown-7E0"]
        e1 = _load(root, "unknown-7a0.yaml")["Unknown-7A0"]
        assert e0["tx_id"] == 0x7E0
        assert e1["tx_id"] == 0x7A0
        assert "Discovered" in e0["identity"]["notes"]

    def test_no_id_protocol_guess(self, tmp_path):
        from canlib.modes.discover import _register_discovered

        root = _mk_profile(tmp_path)
        _register_discovered(ALIVE, dry_run=False)
        # 10 01 can't distinguish UDS/KWP — must not fabricate id_protocol.
        assert "id_protocol" not in _load(root, "unknown-7e0.yaml")["Unknown-7E0"]["identity"]

    def test_dry_run_writes_nothing(self, tmp_path):
        from canlib.modes.discover import _register_discovered

        root = _mk_profile(tmp_path)
        _register_discovered(ALIVE, dry_run=True)
        assert list((root / "ecus").glob("*.yaml")) == []

    def test_idempotent_skips_known(self, tmp_path):
        from canlib.modes.discover import _register_discovered

        root = _mk_profile(tmp_path)
        _register_discovered(ALIVE, dry_run=False)
        clear_cache()
        before = (root / "ecus" / "unknown-7e0.yaml").read_text()
        _register_discovered(ALIVE, dry_run=False)
        assert (root / "ecus" / "unknown-7e0.yaml").read_text() == before

    def test_preserves_existing_named_ecu(self, tmp_path):
        from canlib.modes.discover import _register_discovered

        root = _mk_profile(
            tmp_path, seed_ecus="ECM:\n  tx_id: 0x7E0\n  identity:\n    id_protocol: UDS\n"
        )
        _register_discovered(ALIVE, dry_run=False)
        # 0x7E0 already known (ECM) → untouched; only 0x7A0 added.
        assert "ECM" in _load(root, "ecm.yaml")
        assert (root / "ecus" / "unknown-7a0.yaml").exists()
        assert not (root / "ecus" / "unknown-7e0.yaml").exists()

    def test_handles_empty_ecus_dir(self, tmp_path):
        from canlib.modes.discover import _register_discovered

        root = _mk_profile(tmp_path)
        _register_discovered(ALIVE, dry_run=False)
        assert _load(root, "unknown-7e0.yaml")["Unknown-7E0"]["tx_id"] == 0x7E0
