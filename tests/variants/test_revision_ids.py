from canair.variants.revision_ids import canonical_revision, revision_id_problem, same_revision
from canair.variants.revisions import declared, resolve


def test_capitalised_ids_are_valid():
    assert revision_id_problem("UDS") is None
    assert revision_id_problem("UDS-2018") is None
    assert revision_id_problem("2018") is not None


def test_declared_keeps_the_case_it_is_written_in():
    assert declared({"revisions": {"KWP": {}, "uds": {}}}) == ["KWP", "uds"]


def test_a_typed_id_matches_in_any_case():
    assert same_revision("uds", "UDS")
    assert canonical_revision("uds", ["KWP", "UDS"]) == "UDS"
    assert canonical_revision("other", ["KWP", "UDS"]) == "other"


def test_a_lower_case_pin_resolves_to_the_declared_id():
    ecus = {"BMS": {"revisions": {"KWP": {}, "UDS": {}}}}
    out = resolve(ecus, None, pins=[("BMS", "uds", "flag")])
    assert out["BMS"].revision == "UDS"
