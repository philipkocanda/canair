"""PID validation and AutoPID must agree on inherited signal verification."""

import textwrap

from canair.ecus.pids import load_pids
from canair.interop.autopid.profile import generate_profile, ship_profile
from canair.interop.autopid.v6 import to_v6
from canair.profile.model import Profile
from canair.validation.pids import collect_pids_validation


def test_inherited_verification_matches_autopid_shipping(tmp_path):
    (tmp_path / "profile.yaml").write_text('car_model: "Test"\ninit: "ATSP6;"\n')
    ecu_dir = tmp_path / "ecus"
    ecu_dir.mkdir()
    ecu_file = ecu_dir / "bms.yaml"
    ecu_file.write_text(
        textwrap.dedent("""\
            BMS:
              tx_id: 0x7E4
              pids:
                2101:
                  status: active
                  verified: true
                  parameters:
                    INHERITED_POWER:
                      expression: B10
                      send_to: car/power
                    UNVERIFIED_POWER:
                      expression: B11
                      verified: false
                      send_to: car/candidate
                2102:
                  status: draft
                2103:
                  status: static
                2104:
                  status: active
            """)
    )
    profile = Profile("test-car", tmp_path)
    errors, warnings, stats = collect_pids_validation([ecu_file], profile=profile)
    assert errors == []
    assert len(warnings) == 1 and "UNVERIFIED_POWER" in warnings[0]
    assert stats["pids"] == 4 and stats["verified"] == 1 and stats["unverified"] == 1

    definitions = load_pids(profile)
    verified = generate_profile(definitions, verified_only=True)["pids"]
    assert len(verified) == 1
    assert verified[0]["parameters"] == {"INHERITED_POWER": "B10"}
    assert verified[0]["send_to"] == {"INHERITED_POWER": "car/power"}

    table = to_v6(ship_profile(definitions, verified_only=True)).document
    assert [pid["name"] for pid in table["pids"]] == ["BMS_2101"]
    assert [signal["name"] for signal in table["pids"][0]["parameters"]] == ["INHERITED_POWER"]

    all_signals = generate_profile(definitions, verified_only=False)["pids"]
    assert len(all_signals) == 1
    assert all_signals[0]["send_to"] == {
        "INHERITED_POWER": "car/power",
        "UNVERIFIED_POWER": "car/candidate",
    }
