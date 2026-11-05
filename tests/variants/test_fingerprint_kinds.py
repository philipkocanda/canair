"""Tests for the software: fingerprint — its shape, its editor input and its match."""

import pytest

from canair.variants.fingerprint_kinds import fingerprint_problem, new_fingerprint
from canair.variants.fingerprints import match, wants_software

ECU = {
    "revisions": {
        "OLD": {"fingerprints": [{"software": "F195:1.0*"}]},
        "NEW": {"fingerprints": [{"software": "F195:2.*"}, {"responds": "22F1A0"}]},
    }
}


def test_shape():
    assert fingerprint_problem({"software": "F195:1.0*"}, set()) is None
    assert "software" in fingerprint_problem({"software": ""}, set())


def test_new_fingerprint():
    assert new_fingerprint(software=" F195:1.0* ") == {"software": "F195:1.0*"}
    assert new_fingerprint(responds="22f1a0") == {"responds": "22F1A0"}
    with pytest.raises(ValueError, match="one fingerprint at a time"):
        new_fingerprint(responds="1A90", software="x")
    with pytest.raises(ValueError, match="give a fingerprint"):
        new_fingerprint()


def test_match_by_software():
    assert wants_software(ECU, ["OLD", "NEW"])
    assert match(ECU, software="F195:1.04") == ["OLD"]
    assert match(ECU, software="F195:2.10") == ["NEW"]
    assert match(ECU, software=None) == []
