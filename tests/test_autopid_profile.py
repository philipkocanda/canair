"""Tests for the pure AutoPID profile transforms (canlib.autopid_profile).

Complements tests/test_status_vocab.py (which exercises generate_profile's
shipping gate and duplicate-name rejection) by covering the device-format
conversion and the device→grouped normalization round-trip.
"""

from canlib.autopid_profile import (
    generate_profile,
    make_pid_init,
    normalize_device_profile,
    to_device_format,
)


def _data():
    return {
        "car_model": "Test Car",
        "init": "ATSP6;",
        "ecus": {
            "BMS": {
                "tx_id": 0x7E4,
                "pids": {
                    "2101": {
                        "status": "active",
                        "period": 1000,
                        "parameters": {
                            "SOC": {
                                "expression": "B09/2",
                                "unit": "%",
                                "min": 0,
                                "max": 100,
                                "verified": True,
                            },
                            "PACK_V": {"expression": "[B12:B13]/10", "unit": "V"},
                        },
                    }
                },
            }
        },
    }


class TestMakePidInit:
    def test_header_only(self):
        assert make_pid_init(0x7E4) == "ATSH7E4;ATFCSH7E4;"

    def test_with_session(self):
        assert make_pid_init(0x7E4, session=True) == "ATSH7E4;ATFCSH7E4;1003;"


class TestGenerateAndDeviceFormat:
    def test_generate_grouped_shape(self):
        prof = generate_profile(_data())
        assert prof["car_model"] == "Test Car"
        assert len(prof["pids"]) == 1
        entry = prof["pids"][0]
        assert entry["pid"] == "2101"
        assert entry["pid_init"] == "ATSH7E4;ATFCSH7E4;"
        assert entry["parameters"] == {"SOC": "B09/2", "PACK_V": "[B12:B13]/10"}

    def test_to_device_format_shape(self):
        prof = generate_profile(_data())
        dev = to_device_format(prof, _data())
        assert "cars" in dev and len(dev["cars"]) == 1
        car = dev["cars"][0]
        assert car["car_model"] == "Test Car"
        params = car["pids"][0]["parameters"]
        assert isinstance(params, list)
        soc = next(p for p in params if p["name"] == "SOC")
        assert soc["expression"] == "B09/2"
        assert soc["unit"] == "%"
        assert soc["min"] == "0" and soc["max"] == "100"
        assert soc["type"] == "Default"

    def test_to_device_format_without_meta(self):
        # No YAML meta lookup -> empty unit/min/max, class defaults to "none".
        prof = generate_profile(_data())
        dev = to_device_format(prof)
        soc = next(p for p in dev["cars"][0]["pids"][0]["parameters"] if p["name"] == "SOC")
        assert soc["unit"] == "" and soc["min"] == "" and soc["max"] == ""
        assert soc["class"] == "none"


class TestNormalizeRoundTrip:
    def test_device_format_normalizes_back_to_grouped(self):
        prof = generate_profile(_data())
        dev = to_device_format(prof, _data())
        norm = normalize_device_profile(dev)
        assert norm["car_model"] == "Test Car"
        assert len(norm["pids"]) == 1
        # Parameters come back as grouped {name: expression}.
        assert norm["pids"][0]["parameters"] == {"SOC": "B09/2", "PACK_V": "[B12:B13]/10"}

    def test_normalizes_dict_format(self):
        # A profile POSTed as upstream dict-format parameters normalizes too.
        device_data = {
            "cars": [
                {
                    "car_model": "X",
                    "init": "I;",
                    "pids": [
                        {"pid_init": "ATSH7E4;", "pid": "2101", "parameters": {"SOC": "B09/2"}}
                    ],
                }
            ]
        }
        norm = normalize_device_profile(device_data)
        assert norm["pids"][0]["parameters"] == {"SOC": "B09/2"}

    def test_bare_car_without_cars_wrapper(self):
        device_data = {"car_model": "Y", "init": "I;", "pids": []}
        norm = normalize_device_profile(device_data)
        assert norm["car_model"] == "Y" and norm["pids"] == []
