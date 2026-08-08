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


class TestLinkScaledTimeouts:
    """The two flow-control budgets must cover the network, not just the ECU."""

    def test_no_measurement_leaves_the_lan_defaults(self):
        assert build_isotp_params(None, None) == build_isotp_params(None)
        assert build_isotp_params(None, 0.0) == build_isotp_params(None)

    def test_the_link_budget_is_added_to_the_flow_control_waits(self):
        params = build_isotp_params(None, 1.5)
        assert params["rx_flowcontrol_timeout"] == 1000 + 1500
        assert params["rx_consecutive_frame_timeout"] == 1000 + 1500

    def test_it_widens_rather_than_replaces_a_profile_value(self):
        # The configured value is the ECU's share and the measurement is the
        # network's; a profile that needed 3s from a slow ECU still gets it.
        params = build_isotp_params({"rx_flowcontrol_timeout": 3000}, 0.4)
        assert params["rx_flowcontrol_timeout"] == 3400

    def test_unrelated_fields_are_untouched(self):
        params = build_isotp_params({"tx_padding": 0x00, "stmin": 5}, 2.0)
        assert params["tx_padding"] == 0x00
        assert params["stmin"] == 5
        assert params["tx_data_length"] == 8

    def test_a_lan_measurement_barely_moves_the_budget(self):
        params = build_isotp_params(None, 0.002)
        assert params["rx_flowcontrol_timeout"] == 1002


class TestBlocksizeAdvice:
    """A non-zero BlockSize buys a round trip per block — say so once, on a slow link."""

    def _logged(self, monkeypatch, config, budget):
        from canlib.transport import isotp_params

        seen: list[tuple[str, str]] = []
        monkeypatch.setattr(
            isotp_params,
            "log_event",
            lambda category, detail="", **kw: seen.append((category, detail)),
        )
        isotp_params.build_isotp_params(config, budget)
        return seen

    def test_a_slow_link_with_blocksize_is_flagged(self, monkeypatch):
        seen = self._logged(monkeypatch, {"blocksize": 4}, 1.2)
        assert len(seen) == 1
        assert seen[0][0] == "config"
        assert "blocksize=4" in seen[0][1]
        assert "1200ms" in seen[0][1]

    def test_the_default_blocksize_is_never_flagged(self, monkeypatch):
        assert self._logged(monkeypatch, None, 5.0) == []

    def test_a_fast_link_is_not_worth_a_word(self, monkeypatch):
        assert self._logged(monkeypatch, {"blocksize": 4}, 0.002) == []

    def test_an_unmeasured_link_says_nothing(self, monkeypatch):
        assert self._logged(monkeypatch, {"blocksize": 4}, None) == []
