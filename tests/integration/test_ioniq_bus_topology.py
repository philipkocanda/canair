"""The Ioniq EV's private H-CAN must not acquire the gateway through ALL."""

from canair.core.paths import BUNDLED_PROFILES_DIR
from canair.ecus.can_buses import expand_bus_membership, load_can_buses
from canair.ecus.registry import load_ecus
from canair.profile.model import Profile


def test_ioniq_private_h_can_members():
    profile = Profile("hyundai-ioniq-ae-ev", BUNDLED_PROFILES_DIR / "hyundai-ioniq-ae-ev")
    buses = load_can_buses(profile)
    codes = [bus.code for bus in buses]
    private = [bus.code for bus in buses if bus.private]
    assert "H-CAN" in private
    ecus = load_ecus(profile).values()
    members = {
        ecu["name"]
        for ecu in ecus
        if "H-CAN" in expand_bus_membership(ecu.get("can_bus", []), codes, private)
    }
    assert members == {"MCU", "VCU"}
