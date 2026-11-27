"""The readback watch: notice a held output's readback go back to its value before the switch."""

from __future__ import annotations

from canair.live.monitor.readback import ReadbackWatch, readback_row, readback_text, readback_value

KEY = "IGPM/iocontrol/BC44"


def _frames(value, *, error=None):
    row = ("IG3_RELAY_OUTPUT", value, "", "B12:3", error, True, "")
    return [("IGPM (0x770)", [{"pid": "22BC07", "params": [row]}])]


def test_a_readback_that_moves_and_comes_back_warns_once():
    watch = ReadbackWatch()
    watch.switched(KEY, 0.0)
    assert watch.returned(KEY, 1.0) is False
    assert watch.returned(KEY, 0.0) is True
    assert watch.returned(KEY, 0.0) is False
    assert watch.returned(KEY, 1.0) is False
    assert watch.returned(KEY, 0.0) is False


def test_a_readback_that_has_not_moved_yet_says_nothing():
    # IG3_RELAY_OUTPUT took about 6 s to read 1 after the ON on 2026-11-20.
    watch = ReadbackWatch()
    watch.switched(KEY, 0.0)
    assert watch.returned(KEY, 0.0) is False
    assert watch.returned(KEY, 0.0) is False


def test_an_output_that_was_already_on_says_nothing():
    watch = ReadbackWatch()
    watch.switched(KEY, 1.0)
    assert watch.returned(KEY, 1.0) is False


def test_an_inverted_readback_is_watched_the_same_way():
    watch = ReadbackWatch()
    watch.switched(KEY, 1.0)
    assert watch.returned(KEY, 0.0) is False
    assert watch.returned(KEY, 1.0) is True


def test_no_readback_before_the_switch_means_no_watch():
    watch = ReadbackWatch()
    watch.switched(KEY, None)
    assert watch.returned(KEY, 1.0) is False
    assert watch.returned(KEY, None) is False


def test_a_released_output_is_forgotten_and_a_new_switch_starts_over():
    watch = ReadbackWatch()
    watch.switched(KEY, 0.0)
    watch.returned(KEY, 1.0)
    watch.released(KEY)
    assert watch.returned(KEY, 0.0) is False
    watch.switched(KEY, 0.0)
    assert watch.returned(KEY, 1.0) is False
    assert watch.returned(KEY, 0.0) is True


def test_a_missing_value_neither_moves_nor_warns():
    watch = ReadbackWatch()
    watch.switched(KEY, 0.0)
    assert watch.returned(KEY, None) is False
    assert watch.returned(KEY, 0.0) is False


def test_the_row_is_found_by_ecu_and_signal():
    row = readback_row(_frames(1.0), "IGPM", "IG3_RELAY_OUTPUT")
    assert row is not None and readback_value(row) == 1.0
    assert readback_text(row) == "1"
    assert readback_row(_frames(1.0), "BCM", "IG3_RELAY_OUTPUT") is None
    assert readback_row(_frames(1.0), "IGPM", None) is None


def test_a_failed_decode_has_no_value_but_shows_its_error():
    row = readback_row(_frames(None, error="short payload"), "IGPM", "IG3_RELAY_OUTPUT")
    assert row is not None
    assert readback_value(row) is None
    assert readback_text(row) == "short payload"
