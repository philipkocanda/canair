from canair.core.terminal import truncate_text


def test_truncate_text_ends_in_one_ellipsis_character():
    assert truncate_text("abcdefghij", 6) == "abcde…"
    assert len(truncate_text("abcdefghij", 6)) == 6


def test_truncate_text_leaves_a_fitting_string_alone():
    assert truncate_text("abc", 3) == "abc"
    assert truncate_text("abc", 10) == "abc"


def test_truncate_text_with_no_room_is_empty():
    assert truncate_text("abc", 0) == ""
    assert truncate_text("abc", 1) == "…"
