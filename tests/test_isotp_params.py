"""Tests for the profile-configurable client-side ISO-TP parameters."""

from __future__ import annotations

from canlib.transport.isotp_params import (
    DEFAULT_TX_PADDING,
    ISOTP_FIELDS,
    build_isotp_params,
)


class TestBuildIsotpParams:
    def test_defaults_reproduce_historical_behaviour(self):
        # None config -> the exact hard-coded values both raw clients used before.
        params = build_isotp_params(None)
        assert params == {
            "tx_padding": DEFAULT_TX_PADDING,
            "blocksize": 0,
            "stmin": 0,
            "rx_flowcontrol_timeout": 1000,
            "rx_consecutive_frame_timeout": 1000,
            "can_fd": False,
            "tx_data_length": 8,
        }

    def test_empty_config_is_defaults(self):
        assert build_isotp_params({}) == build_isotp_params(None)

    def test_overrides_apply(self):
        params = build_isotp_params({"tx_padding": 0x00, "can_fd": True, "tx_data_length": 64})
        assert params["tx_padding"] == 0x00
        assert params["can_fd"] is True
        assert params["tx_data_length"] == 64
        # Untouched keys keep their defaults.
        assert params["blocksize"] == 0
        assert params["stmin"] == 0

    def test_none_valued_key_keeps_default(self):
        # A key present but explicitly null should not clobber the default.
        assert build_isotp_params({"tx_padding": None})["tx_padding"] == DEFAULT_TX_PADDING

    def test_unknown_key_ignored_at_runtime(self):
        # Validation flags unknown keys; the builder stays lenient.
        params = build_isotp_params({"bogus": 123})
        assert "bogus" not in params
        assert params == build_isotp_params(None)

    def test_fields_tuple_matches_defaults(self):
        assert set(ISOTP_FIELDS) == set(build_isotp_params(None))
