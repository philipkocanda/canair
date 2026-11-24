"""SecurityAccess is withheld independently of profile sensitivity declarations."""

from canair.cli.profile_context import active
from canair.privacy.sensitive import SensitiveSet, request_hidden, set_reveal, withhold
from canair.uds.security import displayed_reply, withhold_uncertain_response


def test_security_captures_and_refusals_are_never_saved():
    rows = [
        {"rx": "0x728", "pid": "2701", "payload": "67011234"},
        {"rx": "0x728", "pid": "2702A1B2", "response": "NRC"},
        {"rx": "0x728", "pid": "2101", "payload": "6101ABCD"},
        {"rx": "0x728", "pid": "2101", "payload": "67011234"},
    ]
    assert withhold(rows, SensitiveSet()) == [rows[2]]


def test_reveal_does_not_show_security_exchange():
    set_reveal(True)
    try:
        assert request_hidden(0x720, "2701", profile=active())
    finally:
        set_reveal(False)


def test_delayed_security_reply_and_decode_errors_are_withheld():
    stale = {"ok": False, "raw": "67011234", "hex": "67011234", "bytes": b"\x67\x01\x12\x34"}
    masked = withhold_uncertain_response(stale)
    assert "1234" not in str(masked)
    assert masked["ok"] is False and "hex" not in masked and "bytes" not in masked
    malformed = {"ok": False, "raw": "bad 1234", "error": "bad 1234"}
    assert "1234" not in str(withhold_uncertain_response(malformed))
    normal = {"ok": True, "raw": "6101ABCD", "bytes": bytes.fromhex("6101ABCD")}
    assert withhold_uncertain_response(normal) == {**normal, "raw": "[withheld]"}


def test_plain_non_answers_keep_their_wording_after_a_security_exchange():
    no_data = {
        "ok": False,
        "raw": "NO DATA",
        "error": "No response from ECU (NO DATA)",
        "error_kind": "no_data",
    }
    assert withhold_uncertain_response(no_data)["error"] == "No response from ECU (NO DATA)"
    stale = {"ok": False, "error": "mismatch: got 67011234", "error_kind": "stale"}
    shown = withhold_uncertain_response(stale)
    assert "1234" not in str(shown) and shown["error_kind"] == "stale"


def test_repl_reply_display_after_a_security_exchange():
    adapter = {"ok": False, "error": "Empty response"}
    assert displayed_reply("ATSH770", "OK", adapter, security_seen=True) == ("OK", False)
    no_data = {"ok": False, "error": "No response from ECU (NO DATA)"}
    assert displayed_reply("2101", "NO DATA", no_data, security_seen=True) == ("NO DATA", False)
    positive = {"ok": True, "hex": "6101ABCD", "bytes": bytes.fromhex("6101ABCD")}
    assert displayed_reply("2101", "6101 ABCD", positive, security_seen=True) == ("6101ABCD", False)
    late = {"ok": True, "hex": "67011234", "bytes": bytes.fromhex("67011234")}
    shown, withheld = displayed_reply("2101", "67 01 12 34", late, security_seen=True)
    assert withheld and "1234" not in shown
    garbage = {"ok": False, "error": "decode 1234"}
    shown, withheld = displayed_reply("2101", "XX 1234", garbage, security_seen=True)
    assert withheld and "1234" not in shown
    assert displayed_reply("2101", "XX 1234", garbage, security_seen=False)[0] == "XX 1234"
    assert displayed_reply("2701", "6701", positive, security_seen=False)[1] is True
