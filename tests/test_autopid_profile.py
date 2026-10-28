"""Tests for the pure AutoPID profile transforms (canlib.autopid_profile).

Complements tests/test_status_vocab.py (which exercises generate_profile's
shipping gate and duplicate-name rejection) by covering the device-format
conversion and the device→grouped normalization round-trip.
"""

import pytest

from canlib.addressing import AddressBook
from canlib.commands.wican import show_diff
from canlib.transport.elm327_addressing import AdapterState, target_in
from canlib.wican.autopid_profile import (
    InvalidSendToError,
    generate_profile,
    make_pid_init,
    normalize_device_profile,
    to_device_format,
    with_flow_control,
)
from canlib.wican.autopid_send_to import topic_problem


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

    def test_receive_filter_precedes_the_session_request(self):
        init = make_pid_init(0x79B, session=True, rx_filter=0x7BB)
        assert init == "ATSH79B;ATFCSH79B;ATCRA7BB;1003;"

    def test_29bit_flow_control_header(self):
        init = make_pid_init(0x18DB33F1, fc_header=0x18DADBF1, rx_filter=0x18DAF1DB)
        assert init == "ATSH18DB33F1;ATFCSH18DADBF1;ATCRA18DAF1DB;"


def _ecu(tx_id: int, signal: str, **extra) -> dict:
    return {
        "tx_id": tx_id,
        **extra,
        "pids": {
            "2101": {
                "status": "active",
                "parameters": {signal: {"expression": "B09", "verified": True}},
            }
        },
    }


class TestAddressing:
    """Receive filter and flow control in the generated profile."""

    def _profile(self, *ecus: tuple[str, dict], **meta) -> dict:
        return generate_profile({"car_model": "Car", "init": "ATSP6;", "ecus": dict(ecus), **meta})

    def test_conventional_profile_is_unchanged(self):
        prof = self._profile(("BMS", _ecu(0x7E4, "SOC")), ("VCU", _ecu(0x7E2, "GEAR")))
        assert prof["init"] == "ATSP6;"
        assert [p["pid_init"] for p in prof["pids"]] == ["ATSH7E4;ATFCSH7E4;", "ATSH7E2;ATFCSH7E2;"]

    def test_one_offset_ecu_pins_every_entry(self):
        prof = self._profile(
            ("LBC", _ecu(0x79B, "SOH")),
            ("VCM", _ecu(0x797, "GEAR", rx_id=0x79A)),
            ("BMS", _ecu(0x7E4, "SOC", rx_id=0x7EC)),
            addressing={"rx_offset": 0x20},
            isotp={"stmin": 5},
        )
        assert [p["pid_init"] for p in prof["pids"]] == [
            "ATSH79B;ATFCSH79B;ATCRA7BB;",
            "ATSH797;ATFCSH797;ATCRA79A;",
            "ATSH7E4;ATFCSH7E4;ATCRA7EC;",
        ]
        assert prof["init"] == "ATSP6;ATFCSH79B;ATFCSD300005;ATFCSM1;"

    def test_functional_request_ecu_uses_its_flow_control_id(self):
        renault = _ecu(
            0x18DB33F1,
            "SOC",
            rx_id=0x18DAF1DB,
            addressing={"mode": "normal_29bit", "fc_id": 0x18DADBF1},
        )
        prof = generate_profile({"car_model": "Car", "init": "ATSP7", "ecus": {"EVC": renault}})
        assert prof["pids"][0]["pid_init"] == "ATSH18DB33F1;ATFCSH18DADBF1;ATCRA18DAF1DB;"
        assert prof["init"] == "ATSP7;ATFCSH18DADBF1;ATFCSD300000;ATFCSM1;"

    def test_a_broadcast_ecu_in_a_pinned_profile_restores_the_auto_filter(self):
        prof = self._profile(
            ("LBC", _ecu(0x79B, "SOH")),
            ("OBD", _ecu(0x7DF, "SPEED")),
            addressing={"rx_offset": 0x20},
        )
        assert prof["pids"][1]["pid_init"] == "ATSH7DF;ATFCSH7DF;ATCRA;"

    def test_a_broadcast_ecu_alone_does_not_pin(self):
        prof = self._profile(("OBD", _ecu(0x7DF, "SPEED")), ("BMS", _ecu(0x7E4, "SOC")))
        assert prof["init"] == "ATSP6;"
        assert prof["pids"][0]["pid_init"] == "ATSH7DF;ATFCSH7DF;"

    def test_extended_11bit_ecus_are_left_alone(self):
        ecu = _ecu(0x6F1, "SOC", rx_id=0x607, addressing={"mode": "normal_extended_11bit"})
        prof = self._profile(("BMS", ecu))
        assert (prof["init"], prof["pids"][0]["pid_init"]) == ("ATSP6;", "ATSH6F1;ATFCSH6F1;")

    def test_an_unshipped_offset_ecu_does_not_pin_the_rest(self):
        draft = _ecu(0x79B, "SOH")
        draft["pids"]["2101"]["status"] = "draft"
        prof = self._profile(
            ("LBC", draft),
            ("BMS", _ecu(0x7E4, "SOC", rx_id=0x7EC)),
            addressing={"rx_offset": 0x20},
        )
        assert [p["pid_init"] for p in prof["pids"]] == ["ATSH7E4;ATFCSH7E4;"]
        assert prof["init"] == "ATSP6;"

    def test_mixed_profile_leaves_the_adapter_where_the_live_path_does(self):
        """Each entry's ATCRA matches the filter the live plan settles on, in order.

        An unaddressable (extended 0x6F1) or out-of-range-reply (0x7F0 -> 0x810)
        ECU used to emit no ATCRA, inheriting the previous PID's ATCRA7BB, while the
        live path restored the auto baseline.
        """
        ecus = {
            "LBC": _ecu(0x79B, "SOH"),
            "BMW": _ecu(0x6F1, "X", rx_id=0x607, addressing={"mode": "normal_extended_11bit"}),
            "BMS": _ecu(0x7E4, "SOC", rx_id=0x7EC),
            "FAR": _ecu(0x7F0, "Y"),
        }
        data = {
            "car_model": "Car",
            "init": "ATSP6;",
            "ecus": ecus,
            "addressing": {"rx_offset": 0x20},
        }
        prof = generate_profile(data)
        assert [p["pid_init"] for p in prof["pids"]] == [
            "ATSH79B;ATFCSH79B;ATCRA7BB;",
            "ATSH6F1;ATFCSH6F1;ATCRA;",
            "ATSH7E4;ATFCSH7E4;ATCRA7EC;",
            "ATSH7F0;ATFCSH7F0;ATCRA;",
        ]
        book = AddressBook.from_profile(data)
        state = AdapterState()
        for entry, tx_id in zip(prof["pids"], (0x79B, 0x6F1, 0x7E4, 0x7F0), strict=True):
            for cmd in state.plan(target_in(book, tx_id)):
                state.observe(cmd, "OK")
            atcra = [c for c in entry["pid_init"].split(";") if c.startswith("ATCRA")]
            pinned = None if atcra == ["ATCRA"] else int(atcra[0][5:], 16)
            # Where the live path pins a filter, AutoPID pins the same one; where
            # AutoPID restores auto, so does the live path. (A conventional ECU may
            # stay on auto live while AutoPID pins its own reply id: equivalent.)
            live = state.rx_filter
            if (isinstance(live, int) and live != pinned) or (pinned is None and live != "auto"):
                pytest.fail(f"0x{tx_id:X}: live {live!r}, AutoPID {atcra}")

    def test_29bit_ecu_with_a_small_id_uses_8_digit_ids(self):
        ecu = _ecu(0x7E0, "SOC", rx_id=0x7E8, addressing={"mode": "normal_29bit"})
        prof = generate_profile({"car_model": "Car", "init": "ATSP7", "ecus": {"E": ecu}})
        assert prof["pids"][0]["pid_init"] == "ATSH000007E0;ATFCSH000007E0;ATCRA000007E8;"
        assert prof["init"] == "ATSP7;ATFCSH000007E0;ATFCSD300000;ATFCSM1;"

    def test_shared_request_id_entries_each_pin_their_own_ecu(self):
        evc = _ecu(0x18DB33F1, "A", rx_id=0x18DAF1DB, addressing={"fc_id": 0x18DADBF1})
        bms = _ecu(0x18DB33F1, "B", rx_id=0x18DAF1DA, addressing={"fc_id": 0x18DADAF1})
        prof = generate_profile(
            {
                "car_model": "Car",
                "init": "ATSP7;",
                "addressing": {"mode": "normal_29bit"},
                "ecus": {"EVC": evc, "BMS": bms},
            }
        )
        assert [p["pid_init"] for p in prof["pids"]] == [
            "ATSH18DB33F1;ATFCSH18DADBF1;ATCRA18DAF1DB;",
            "ATSH18DB33F1;ATFCSH18DADAF1;ATCRA18DAF1DA;",
        ]


class TestWithFlowControl:
    def test_appends_after_an_init_with_a_trailing_separator(self):
        assert with_flow_control("ATSP6;", 0x79B, "300000") == (
            "ATSP6;ATFCSH79B;ATFCSD300000;ATFCSM1;"
        )

    def test_adds_the_missing_separator(self):
        assert (
            with_flow_control("ATSP6", 0x79B, "300000") == "ATSP6;ATFCSH79B;ATFCSD300000;ATFCSM1;"
        )

    def test_empty_init(self):
        assert with_flow_control("", 0x18DADBF1, "300000", extended=True) == (
            "ATFCSH18DADBF1;ATFCSD300000;ATFCSM1;"
        )


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

    def test_on_by_default(self):
        assert self._pid(self._with(response_frames=4)) == "21014"

    def test_the_digit_can_be_opted_out(self):
        assert self._pid(self._with(response_frames=4), expected_responses=False) == "2101"

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


class TestSendTo:
    """A signal's ``send_to`` topic: streamed by the firmware on every poll.

    The firmware only publishes a signal per poll when its ``type`` is the exact
    string ``MQTT_Topic``; everything else parses as ``Default`` and is seen only in
    the batch snapshot. See plans/2026-10-18-per-signal-mqtt-streaming.md.
    """

    TOPIC = "car/stream/soc"

    def _data(self, **soc_fields):
        data = _data()
        data["ecus"]["BMS"]["pids"]["2101"]["parameters"]["SOC"].update(soc_fields)
        return data

    def _device_params(self, profile) -> dict:
        params = to_device_format(profile)["cars"][0]["pids"][0]["parameters"]
        return {p["name"]: p for p in params}

    def test_a_streamed_signal_is_grouped_under_send_to(self):
        entry = generate_profile(self._data(send_to=self.TOPIC))["pids"][0]
        assert entry["send_to"] == {"SOC": self.TOPIC}
        assert entry["parameters"] == {"SOC": "B09/2", "PACK_V": "[B12:B13]/10"}

    def test_no_stream_leaves_the_entry_unchanged(self):
        assert "send_to" not in generate_profile(_data())["pids"][0]

    def test_device_format_marks_the_stream_as_mqtt_topic(self):
        params = self._device_params(generate_profile(self._data(send_to=self.TOPIC)))
        assert params["SOC"]["type"] == "MQTT_Topic"
        assert params["SOC"]["send_to"] == self.TOPIC

    def test_device_format_keeps_unstreamed_signals_default(self):
        # Regression guard for the output before send_to existed.
        params = self._device_params(generate_profile(self._data(send_to=self.TOPIC)))
        assert params["PACK_V"]["type"] == "Default"
        assert params["PACK_V"]["send_to"] == ""

    def test_an_unshipped_signal_carries_no_stream(self):
        # Verified-only (the default build) drops the unverified PACK_V — and its topic.
        data = _data()
        data["ecus"]["BMS"]["pids"]["2101"]["parameters"]["PACK_V"]["send_to"] = "car/v"
        entry = generate_profile(data, verified_only=True)["pids"][0]
        assert "send_to" not in entry

    def test_the_stream_round_trips_through_normalization(self):
        profile = generate_profile(self._data(send_to=self.TOPIC))
        norm = normalize_device_profile(to_device_format(profile))
        assert norm["pids"][0]["send_to"] == {"SOC": self.TOPIC}

    def test_an_inert_send_to_under_default_is_not_a_stream(self):
        # The firmware ignores send_to unless type is MQTT_Topic.
        device = to_device_format(generate_profile(_data()))
        device["cars"][0]["pids"][0]["parameters"][0]["send_to"] = "car/x"
        assert "send_to" not in normalize_device_profile(device)["pids"][0]

    def test_a_malformed_topic_is_refused(self):
        with pytest.raises(InvalidSendToError, match="wildcard"):
            generate_profile(self._data(send_to="car/#"))

    def test_a_shared_topic_is_refused(self):
        data = self._data(send_to=self.TOPIC)
        data["ecus"]["BMS"]["pids"]["2101"]["parameters"]["PACK_V"]["send_to"] = self.TOPIC
        with pytest.raises(InvalidSendToError, match="also used by 'SOC'"):
            generate_profile(data)


class TestTopicProblem:
    @pytest.mark.parametrize("topic", ["car/stream/soc", "soc", "a/b-c_d.e"])
    def test_accepts(self, topic):
        assert topic_problem(topic) is None

    @pytest.mark.parametrize(
        "topic",
        ["", None, 5, "car/#", "car/+/soc", "/car/soc", "car/soc/", "car//soc", "car/s oc"],
    )
    def test_rejects(self, topic):
        assert topic_problem(topic)


class TestDiffSeesSendTo:
    """``autopid diff`` must not report "No differences" when only a stream changed."""

    def _profile(self, topic=None):
        data = _data()
        if topic:
            data["ecus"]["BMS"]["pids"]["2101"]["parameters"]["SOC"]["send_to"] = topic
        return generate_profile(data)

    def test_an_added_stream_is_a_change(self, capsys):
        device = to_device_format(self._profile())
        assert show_diff(device, self._profile("car/soc")) is True
        out = capsys.readouterr().out
        assert "~ CHANGED" in out
        assert "SOC: send_to (none) → car/soc" in out

    def test_a_removed_stream_is_a_change(self, capsys):
        device = to_device_format(self._profile("car/soc"))
        assert show_diff(device, self._profile()) is True
        assert "SOC: send_to car/soc → (none)" in capsys.readouterr().out

    def test_an_identical_stream_is_no_change(self, capsys):
        profile = self._profile("car/soc")
        assert show_diff(to_device_format(profile), profile) is False
        assert "No differences" in capsys.readouterr().out
