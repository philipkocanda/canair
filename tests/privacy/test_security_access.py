"""SecurityAccess is withheld independently of profile sensitivity declarations."""

from canair.cli.profile_context import active
from canair.privacy.sensitive import SensitiveSet, request_hidden, set_reveal, withhold
from canair.uds.security import withhold_uncertain_response


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
