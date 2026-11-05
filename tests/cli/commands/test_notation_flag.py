"""The ``--notation`` flag: explicit value, then ``display.byte_notation``, then wican."""

from __future__ import annotations

from canair.cli.args.notation import resolve_notation
from canair.uds.notation import ByteNotation


class TestResolveNotation:
    def test_explicit_value_wins(self):
        assert resolve_notation("isotp") is ByteNotation.ISOTP
        assert resolve_notation("torque") is ByteNotation.TORQUE

    def test_defaults_to_wican(self, monkeypatch):
        import canair.config as cfg

        # No flag, no config -> wican.
        monkeypatch.setattr(cfg, "get_config_key", lambda k: None)
        assert resolve_notation(None) is ByteNotation.WICAN

    def test_config_default_used_when_no_flag(self, monkeypatch):
        import canair.config as cfg

        monkeypatch.setattr(
            cfg, "get_config_key", lambda k: "isotp" if k == "display.byte_notation" else None
        )

        assert resolve_notation(None) is ByteNotation.ISOTP

    def test_invalid_config_falls_back_to_wican(self, monkeypatch):
        import canair.config as cfg

        monkeypatch.setattr(cfg, "get_config_key", lambda k: "bogus")

        assert resolve_notation(None) is ByteNotation.WICAN
