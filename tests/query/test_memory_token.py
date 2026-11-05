"""The memory token (23/ADDRESS+SIZE) in the query mini-language."""

import pytest

from canair.query import (
    QueryError,
    explicit_request,
    inferred_request,
    looks_like_pid,
    parse_selector,
    pids_named_by,
)
from canair.uds.memory import KWP_FORMAT, parse_format

UDS24 = parse_format("24")


def test_needs_the_ecu_format():
    with pytest.raises(QueryError, match="memory format"):
        explicit_request("23/FEDE0000+10")


def test_builds_the_request_with_the_format():
    assert inferred_request("23/FEDE0000+10", UDS24) == "2324FEDE00000010"
    assert inferred_request("23/100+4", KWP_FORMAT) == "2300010004"


def test_too_wide_for_the_format():
    with pytest.raises(QueryError, match="does not fit"):
        inferred_request("23/FEDE0000+10", KWP_FORMAT)


def test_picks_the_defined_memory_pid():
    keys = ["2324FEDE00000010", "2324FEDE00000020", "22B001"]
    assert pids_named_by("23/FEDE0000+10", keys) == ["2324FEDE00000010"]


def test_selector_and_pid_shape():
    assert parse_selector("BCM:23/FEDE0000+10").pids == ("23/FEDE0000+10",)
    assert looks_like_pid("23/FEDE0000+10")
    with pytest.raises(QueryError):
        parse_selector("BCM:23/FEDE0000")


def test_bare_token_never_infers_a_memory_read():
    with pytest.raises(QueryError):
        inferred_request("2324FEDE0000")
