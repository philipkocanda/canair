"""Tests for `canair ecu add` (offline ECU registration) and offline validation.

`canair ecu add` is the offline counterpart to `discover --register`: it writes
a new ecus/<name>.yaml into a profile without a live bus, validated via the
comment-preserving writer. The key regression it guards: validating (and thus
writing) an ECU file resolves the vehicle-state vocabulary from the *file's own
profile*, not the globally-active one — so it works even when several profiles
are discoverable (no spurious "Multiple profiles found").
"""

from __future__ import annotations

import argparse

import pytest
import yaml

from canlib import profile
from canlib.commands.ecu import cmd_add
from canlib.ecus_edit import register_ecu
from canlib.pids import clear_cache


@pytest.fixture(autouse=True)
def _restore_active_profile():
    saved = profile._active
    clear_cache()
    yield
    profile._active = saved
    clear_cache()


def _mk_profile(tmp_path, name="prof"):
    root = tmp_path / name
    (root / "ecus").mkdir(parents=True)
    (root / "captures").mkdir()
    (root / "profile.yaml").write_text('car_model: "T"\ninit: "ATSP6;"\n')
    return root


def _args(**kw):
    base = {
        "tx": "7C6",
        "name": None,
        "description": None,
        "id_protocol": None,
        "notes": None,
        "overwrite": False,
        "dir": None,
    }
    base.update(kw)
    return argparse.Namespace(**base)


class TestEcuAdd:
    def test_registers_new_ecu(self, tmp_path, capsys):
        root = _mk_profile(tmp_path)
        rc = cmd_add(_args(tx="7C6", name="CLU", description="Cluster", dir=root / "ecus"))
        assert rc == 0
        text = (root / "ecus" / "clu.yaml").read_text()
        assert "tx_id: 0x7C6" in text  # stored as hex
        data = yaml.safe_load(text)
        assert data["CLU"]["tx_id"] == 0x7C6
        assert data["CLU"]["identity"]["description"] == "Cluster"

    def test_default_name_is_unknown_tx(self, tmp_path):
        root = _mk_profile(tmp_path)
        rc = cmd_add(_args(tx="7C6", name=None, dir=root / "ecus"))
        assert rc == 0
        files = list((root / "ecus").glob("*.yaml"))
        assert files and "unknown-7c6" in files[0].name.lower()

    def test_idempotent_reads_returns_zero(self, tmp_path, capsys):
        root = _mk_profile(tmp_path)
        cmd_add(_args(tx="7C6", name="CLU", dir=root / "ecus"))
        rc = cmd_add(_args(tx="7C6", name="CLU", dir=root / "ecus"))
        assert rc == 0
        assert "already registered" in capsys.readouterr().out

    def test_invalid_hex_tx_is_error(self, tmp_path, capsys):
        root = _mk_profile(tmp_path)
        rc = cmd_add(_args(tx="ZZZ", dir=root / "ecus"))
        assert rc == 1
        assert "Invalid TX" in capsys.readouterr().err

    def test_out_of_range_tx_is_error(self, tmp_path, capsys):
        root = _mk_profile(tmp_path)
        rc = cmd_add(_args(tx="999", dir=root / "ecus"))
        assert rc == 1


class TestOfflineValidationProfileScoping:
    """register_ecu must validate against the file's own profile, not active()."""

    def test_write_succeeds_with_multiple_profiles_and_no_active(self, tmp_path, monkeypatch):
        # Two discoverable profiles + no default => active() would raise.
        root_a = _mk_profile(tmp_path, "car-a")
        _mk_profile(tmp_path, "car-b")
        monkeypatch.setenv("CANAIR_PROFILES_DIR", str(tmp_path))
        monkeypatch.delenv("CANAIR_PROFILE", raising=False)
        profile._active = None

        # active() is genuinely ambiguous here...
        with pytest.raises(profile.ProfileError):
            profile.resolve_profile(profiles_dir=str(tmp_path))

        # ...but a scoped write still validates and persists.
        wrote = register_ecu(0x7C6, name="CLU", ecus_dir=root_a / "ecus")
        assert wrote is True
        assert (root_a / "ecus" / "clu.yaml").exists()
