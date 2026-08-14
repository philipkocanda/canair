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


class TestExpectedResponseDigit:
    """Emitting a recorded ``response_frames`` count into the ``pid`` request.

    The firmware passes this string to its ELM327 co-processor verbatim and has no
    desync recovery — it accumulates into one static buffer cleared only after a
    parse — so an undercount's queued tail would silently prefix the next PID's
    response. Every guard below is what keeps that from happening.
    """

    def _with(self, **pid_fields):
        data = _data()
        data["ecus"]["BMS"]["pids"]["2101"].update(pid_fields)
        return data

    def _pid(self, data, **kw) -> str:
        return generate_profile(data, **kw)["pids"][0]["pid"]

    def test_off_by_default(self):
        # Opt-in: a user who has not asked for it keeps the firmware's own
        # behaviour, whatever the profile records.
        assert self._pid(self._with(response_frames=4)) == "2101"

    def test_the_digit_is_appended_when_requested(self):
        assert self._pid(self._with(response_frames=4), expected_responses=True) == "21014"

    def test_a_pid_with_no_recorded_count_is_left_plain(self):
        assert self._pid(self._with(), expected_responses=True) == "2101"

    def test_a_count_over_the_ceiling_is_left_plain(self):
        # Refused, never clamped: a clamp is a deliberate undercount.
        assert self._pid(self._with(response_frames=13), expected_responses=True) == "2101"

    def test_a_variable_length_pid_is_left_plain(self):
        data = self._with(response_frames=4, variable_length=True)
        assert self._pid(data, expected_responses=True) == "2101"

    def test_a_non_integer_count_is_ignored(self):
        assert self._pid(self._with(response_frames="4"), expected_responses=True) == "2101"
        assert self._pid(self._with(response_frames=True), expected_responses=True) == "2101"

    def test_an_int_pid_key_is_annotated(self):
        # ecus/ YAML leaves numeric PIDs unquoted, so the key is often an int.
        data = _data()
        pids = data["ecus"]["BMS"]["pids"]
        pids[2101] = {**pids.pop("2101"), "response_frames": 4}
        assert self._pid(data, expected_responses=True) == "21014"

    def test_an_odd_length_request_is_left_plain(self):
        # A request that is already odd would absorb the nibble as a data byte and
        # ask the ECU something else. This also catches an int key whose leading
        # zero str()s away (0100 -> "100").
        data = _data()
        pids = data["ecus"]["BMS"]["pids"]
        pids["100"] = {**pids.pop("2101"), "response_frames": 2}
        assert self._pid(data, expected_responses=True) == "100"

    def test_the_digit_survives_conversion_to_device_format(self):
        # to_device_format copies the request verbatim, so an upload carries it.
        profile = generate_profile(self._with(response_frames=4), expected_responses=True)
        device = to_device_format(profile)
        assert device["cars"][0]["pids"][0]["pid"] == "21014"

    def test_the_digit_round_trips_through_normalization(self):
        # So `autopid diff` compares like with like instead of reporting churn.
        profile = generate_profile(self._with(response_frames=4), expected_responses=True)
        device = to_device_format(profile)
        assert normalize_device_profile(device)["pids"][0]["pid"] == "21014"
