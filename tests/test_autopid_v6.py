"""Tests for the firmware v6 AutoPID table (canlib.wican.autopid_v6).

Firmware facts these pin (field sizes, the parser's number handling, the chains it
drops) are cited in plans/2026-10-31-wican-fw-v6-autopid-profile.md. Every bundled
car's generated table was also run through v6's own ``ap_config_parse``, compiled on
the host, when this was written.
"""

import pytest

from canlib.wican.autopid_profile import generate_profile, render_v4, ship_profile
from canlib.wican.autopid_v6 import V6LimitError, to_v6


def _param(expression="B9", **extra) -> dict:
    return {"expression": expression, "verified": True, **extra}


def _data(params=None, *, tx_id=0x7E4, ecu="BMS", pid="2101", init="ATSP6;", **extra):
    return {
        "car_model": "Test Car",
        "init": init,
        "ecus": {
            ecu: {
                "tx_id": tx_id,
                **extra,
                "pids": {
                    pid: {
                        "status": "active",
                        "period": 2500,
                        "parameters": params or {"SOC": _param("B9/2")},
                    }
                },
            }
        },
    }


def _v6(data):
    return to_v6(ship_profile(data))


def _row(data):
    return _v6(data).document["pids"][0]


class TestDocument:
    def test_shape(self):
        doc = _v6(_data()).document
        assert list(doc) == ["vehicle", "specific_init", "groups", "pids", "filters"]
        assert (doc["vehicle"], doc["groups"], doc["filters"]) == ("Test Car", [], [])

    def test_row(self):
        params = {
            "SOC": _param("B9/2", unit="%", category="state_of_charge", min=0, max=100),
            "PACK_V": _param("[B12:B13]/10", unit="V"),
        }
        assert _row(_data(params)) == {
            "name": "BMS_2101",
            "type": "specific",
            "cmd": "2101",
            "init": "ATSH7E4;ATFCSH7E4",
            "rxheader": "",
            "group": "default",
            "period_ms": 2500,
            "enabled": True,
            "parameters": [
                {
                    "name": "SOC",
                    "expression": "B9/2",
                    "unit": "%",
                    "class": "battery",
                    "min": 0,
                    "max": 100,
                },
                {"name": "PACK_V", "expression": "[B12:B13]/10", "unit": "V", "class": "voltage"},
            ],
        }

    def test_bounds_are_numbers(self):
        (param,) = _row(_data({"T": _param(min="-40", max="87.5")}))["parameters"]
        assert (param["min"], param["max"]) == (-40, 87.5)

    def test_an_unparseable_bound_is_left_out(self):
        (param,) = _row(_data({"T": _param(min="", max="hot")}))["parameters"]
        assert "min" not in param and "max" not in param

    def test_the_period_is_a_number(self):
        data = _data()
        data["ecus"]["BMS"]["pids"]["2101"]["period"] = "10000"
        assert _row(data)["period_ms"] == 10000

    def test_the_expected_response_digit_stays_on_the_request(self):
        data = _data()
        data["ecus"]["BMS"]["pids"]["2101"]["response_frames"] = 9
        assert _row(data)["cmd"] == "21019"

    def test_a_session_ecu_opens_it_in_the_row_init(self):
        assert _row(_data(session=True))["init"] == "ATSH7E4;ATFCSH7E4;1003"

    def test_print_format_commands_are_dropped_from_the_car_init(self):
        doc = _v6(_data(init="ATSP6;ATS0;ATAL;ath0;ATE1;ATST96;")).document
        assert doc["specific_init"] == "ATSP6;ATAL;ATST96"

    def test_the_4x_profile_is_rendered_from_the_same_shipping_decision(self):
        data = _data()
        assert render_v4(ship_profile(data)) == generate_profile(data)


class TestReceiveFilter:
    def _pinned(self):
        data = _data(tx_id=0x79B, ecu="LBC")
        data["addressing"] = {"rx_offset": 0x20}
        data["ecus"]["OBD"] = {
            "tx_id": 0x7DF,
            "pids": {"0105": {"status": "active", "parameters": {"COOLANT": _param("B3-40")}}},
        }
        return data

    def test_moves_from_the_init_into_rxheader(self):
        lbc, _ = _v6(self._pinned()).document["pids"]
        assert (lbc["init"], lbc["rxheader"]) == ("ATSH79B;ATFCSH79B", "7BB")

    def test_the_4x_profile_keeps_it_in_the_init(self):
        assert generate_profile(self._pinned())["pids"][0]["pid_init"] == (
            "ATSH79B;ATFCSH79B;ATCRA7BB;"
        )

    def test_a_broadcast_row_leaves_it_to_the_firmware(self):
        _, obd = _v6(self._pinned()).document["pids"]
        assert (obd["init"], obd["rxheader"]) == ("ATSH7DF;ATFCSH7DF", "")

    def test_flow_control_setup_goes_to_the_car_init(self):
        doc = _v6(self._pinned()).document
        assert doc["specific_init"] == "ATSP6;ATFCSH79B;ATFCSD300000;ATFCSM1"

    def test_a_29bit_reply_id_uses_8_digits(self):
        data = _data(tx_id=0x7E0, ecu="E", init="ATSP7", addressing={"mode": "normal_29bit"})
        data["ecus"]["E"]["rx_id"] = 0x7E8
        assert _row(data)["rxheader"] == "000007E8"


class TestStreams:
    def test_send_to_is_left_out_and_reported(self):
        v6 = _v6(_data({"P": _param(send_to="car/power"), "Q": _param("B10")}))
        assert v6.dropped_streams == ("P",)
        assert all("send_to" not in p for p in v6.document["pids"][0]["parameters"])


class TestLimits:
    """Refused, never truncated: the firmware would cut the string short silently."""

    def _refused(self, data) -> str:
        with pytest.raises(V6LimitError) as excinfo:
            _v6(data)
        return str(excinfo.value)

    def test_a_31_byte_signal_name_fits(self):
        assert _row(_data({"N" * 31: _param()}))["parameters"][0]["name"] == "N" * 31

    def test_a_32_byte_signal_name_is_refused(self):
        assert "name is 32 bytes, over the 31" in self._refused(_data({"N" * 32: _param()}))

    def test_bytes_not_characters(self):
        unit = "°" * 8  # 8 characters, 16 bytes
        assert "unit is 16 bytes, over the 15" in self._refused(_data({"T": _param(unit=unit)}))

    def test_the_row_name_counts(self):
        assert "BMSBMSBMS" in self._refused(_data(ecu="BMS" * 9))

    def test_a_long_expression(self):
        long = "+".join(["B9"] * 40)
        assert "expression is 119 bytes" in self._refused(_data({"X": _param(long)}))

    def test_a_long_car_init(self):
        assert "specific_init is" in self._refused(_data(init="ATAL;" * 20))

    def test_a_long_vehicle_name(self):
        data = _data()
        data["car_model"] = "M" * 64
        assert "vehicle: name is 64 bytes" in self._refused(data)

    @pytest.mark.parametrize("command", ["ATSD 01", "ATCV1240", "atpp 0c sv 23", "STWBR 115200"])
    def test_an_eeprom_write_is_refused(self, command):
        assert "writes the chip's EEPROM" in self._refused(_data(init=f"ATSP6;{command};"))

    def test_the_programmable_parameter_summary_is_a_read(self):
        assert _v6(_data(init="ATPPS;ATSP6")).document["specific_init"] == "ATPPS;ATSP6"

    def test_an_expression_the_firmware_refuses(self):
        assert "inverted range" in self._refused(_data({"X": _param("[B5:B4]")}))

    def test_too_many_signals_on_one_pid(self):
        params = {f"S{i}": _param() for i in range(257)}
        assert "257 signals, over the firmware's 256 per PID" in self._refused(_data(params))

    def test_a_zero_period_is_refused(self):
        data = _data()
        data["ecus"]["BMS"]["pids"]["2101"]["period"] = 0
        assert "at least 1 ms" in self._refused(data)

    def test_every_problem_is_reported_at_once(self):
        message = self._refused(_data({"N" * 32: _param("[B5:B4]")}, init="ATSD 01"))
        assert "32 bytes" in message and "inverted range" in message and "EEPROM" in message
