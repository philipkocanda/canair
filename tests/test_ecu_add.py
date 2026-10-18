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
from canlib.commands.ecu import cmd_add, cmd_rename
from canlib.ecus_edit import register_ecu
from canlib.pids import clear_cache


@pytest.fixture(autouse=True)
def _restore_active_profile():
    from canlib import config

    saved = profile._active
    clear_cache()
    config.load_config.cache_clear()
    yield
    profile._active = saved
    clear_cache()
    config.load_config.cache_clear()


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
        "rx_id": None,
        "mode": None,
        "target_address": None,
        "source_address": None,
        "fc_id": None,
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

    def test_rx_id_override_written(self, tmp_path):
        # A non-standard response address (XPeng: 0x704 -> 0x784) is written as a
        # top-level rx_id field (sibling of tx_id).
        root = _mk_profile(tmp_path)
        rc = cmd_add(_args(tx="704", name="BMS", rx_id="0x784", dir=root / "ecus"))
        assert rc == 0
        data = yaml.safe_load((root / "ecus" / "bms.yaml").read_text())
        assert data["BMS"]["tx_id"] == 0x704
        assert data["BMS"]["rx_id"] == 0x784

    def test_invalid_rx_id_is_error(self, tmp_path, capsys):
        root = _mk_profile(tmp_path)
        rc = cmd_add(_args(tx="704", name="BMS", rx_id="ZZ", dir=root / "ecus"))
        assert rc == 1
        assert "--rx-id expects hex" in capsys.readouterr().err

    def test_29bit_functional_tx_with_fc_id(self, tmp_path):
        # Gap G-J: a functional-TX 29-bit ECU seeded in one command (the mode makes
        # the 29-bit tx_id pass the width check), with a physical FC override.
        root = _mk_profile(tmp_path)
        rc = cmd_add(
            _args(
                tx="18DB33F1",
                name="EVC",
                mode="normal_29bit",
                rx_id="0x18DAF1DB",
                fc_id="0x18DADBF1",
                dir=root / "ecus",
            )
        )
        assert rc == 0
        data = yaml.safe_load((root / "ecus" / "evc.yaml").read_text())
        assert data["EVC"]["tx_id"] == 0x18DB33F1
        assert data["EVC"]["addressing"]["mode"] == "normal_29bit"
        assert data["EVC"]["addressing"]["fc_id"] == 0x18DADBF1

    def test_extended_11bit_target_address(self, tmp_path):
        # Gap G-I: BMW/PSA extended-11-bit ECU with a target extension byte.
        root = _mk_profile(tmp_path)
        rc = cmd_add(
            _args(
                tx="6F1",
                name="DME",
                mode="normal_extended_11bit",
                target_address="0x12",
                rx_id="0x612",
                dir=root / "ecus",
            )
        )
        assert rc == 0
        data = yaml.safe_load((root / "ecus" / "dme.yaml").read_text())
        assert data["DME"]["addressing"]["mode"] == "normal_extended_11bit"
        assert data["DME"]["addressing"]["target_address"] == 0x12


class TestEcuRename:
    """`canair ecu rename` resolves the old ECU against the active profile, then
    rewrites its key + file via the comment-preserving writer."""

    def _activate(self, tmp_path, monkeypatch, name="prof"):
        from canlib import config

        root = _mk_profile(tmp_path, name)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        monkeypatch.setenv("CANAIR_PROFILES_DIR", str(tmp_path))
        monkeypatch.setenv("CANAIR_PROFILE", name)
        config.load_config.cache_clear()
        profile._active = None
        clear_cache()
        return root

    def _rargs(self, **kw):
        base = {"ecu": None, "new_name": None, "dir": None}
        base.update(kw)
        return argparse.Namespace(**base)

    def test_rename_by_name(self, tmp_path, monkeypatch, capsys):
        root = self._activate(tmp_path, monkeypatch)
        register_ecu(0x7D5, "Unknown-7D5", ecus_dir=root / "ecus")
        clear_cache()
        rc = cmd_rename(self._rargs(ecu="Unknown-7D5", new_name="EPB"))
        assert rc == 0
        assert (root / "ecus" / "epb.yaml").exists()
        assert not (root / "ecus" / "unknown-7d5.yaml").exists()
        assert "EPB" in yaml.safe_load((root / "ecus" / "epb.yaml").read_text())

    def test_rename_by_hex(self, tmp_path, monkeypatch):
        root = self._activate(tmp_path, monkeypatch)
        register_ecu(0x7D5, "Unknown-7D5", ecus_dir=root / "ecus")
        clear_cache()
        rc = cmd_rename(self._rargs(ecu="0x7D5", new_name="EPB"))
        assert rc == 0
        assert (root / "ecus" / "epb.yaml").exists()

    def test_unknown_ecu_is_error(self, tmp_path, monkeypatch, capsys):
        self._activate(tmp_path, monkeypatch)
        clear_cache()
        rc = cmd_rename(self._rargs(ecu="NOPE", new_name="EPB"))
        assert rc == 1
        assert "Unknown ECU" in capsys.readouterr().err

    def test_collision_is_error(self, tmp_path, monkeypatch, capsys):
        root = self._activate(tmp_path, monkeypatch)
        register_ecu(0x7D5, "Unknown-7D5", ecus_dir=root / "ecus")
        register_ecu(0x7E0, "VCU", ecus_dir=root / "ecus")
        clear_cache()
        rc = cmd_rename(self._rargs(ecu="Unknown-7D5", new_name="VCU"))
        assert rc == 1
        assert "already exists" in capsys.readouterr().err


class TestOfflineValidationProfileScoping:
    """register_ecu must validate against the file's own profile, not active()."""

    def test_write_succeeds_with_multiple_profiles_and_no_active(self, tmp_path, monkeypatch):
        # Two discoverable profiles + no default => active() would raise.
        root_a = _mk_profile(tmp_path, "car-a")
        _mk_profile(tmp_path, "car-b")
        # Isolate config so a real ~/.config/canair default_profile can't leak in.
        from canlib import config

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        monkeypatch.setenv("CANAIR_PROFILES_DIR", str(tmp_path))
        monkeypatch.delenv("CANAIR_PROFILE", raising=False)
        config.load_config.cache_clear()
        profile._active = None

        # active() is genuinely ambiguous here...
        with pytest.raises(profile.ProfileError):
            profile.resolve_profile(profiles_dir=str(tmp_path))

        # ...but a scoped write still validates and persists.
        wrote = register_ecu(0x7C6, name="CLU", ecus_dir=root_a / "ecus")
        assert wrote is True
        assert (root_a / "ecus" / "clu.yaml").exists()
