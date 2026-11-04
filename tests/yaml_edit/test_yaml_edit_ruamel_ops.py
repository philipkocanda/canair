"""Tests for canlib.yaml_edit.ruamel_ops — comment-safe edits of ruamel trees."""

from __future__ import annotations

import io
import itertools

import pytest

from canlib.core.yaml_rt import dump, round_trip_yaml
from canlib.yaml_edit import ruamel_ops as ops


def _load(text: str):
    return round_trip_yaml(sequence=4, offset=2).load(text)


def _dump(data) -> str:
    buf = io.StringIO()
    dump(data, buf, sequence=4, offset=2)
    return buf.getvalue()


# One block per entry: the comment above it, then the entry. Deleting entry j must leave exactly
# the other blocks.
MAP_BLOCKS = {
    "plain": "  # lead {n}\n  {n}:\n    x: 1\n",
    "inline": "  # lead {n}\n  {n}:\n    x: 1  # inline {n}\n",
    "blank": "\n  # lead {n}\n  {n}:\n    x: 1\n",
    "nolead": "  {n}:\n    x: 1\n",
    "flow": "  # lead {n}\n  {n}:\n    members: [{n}]\n",
    "flow_inline": "  # lead {n}\n  {n}:\n    members: [{n}]  # inline {n}\n",
    "scalar": "  # lead {n}\n  {n}: 1\n",
    "multi": "  # lead {n}\n  {n}:\n    a: 1\n    b: 2  # inline {n}\n",
    "nested": "  # lead {n}\n  {n}:\n    inner:\n      k: v\n",
}
POSTS = ["", "# about after\nafter: 1\n", "\nafter: 1\n"]


def _without(blocks: list[str], j: int) -> list[str]:
    """``blocks`` minus entry ``j``; a new first entry loses the blank line that set it apart."""
    kept = [b for i, b in enumerate(blocks) if i != j]
    if j == 0 and kept:
        kept[0] = kept[0].lstrip("\n")
    return kept


def _map_doc(kinds: list[str], post: str) -> tuple[str, list[str]]:
    blocks = [MAP_BLOCKS[k].format(n="abcdef"[i]) for i, k in enumerate(kinds)]
    return "# header\nsection:\n", blocks


@pytest.mark.parametrize("post", POSTS)
@pytest.mark.parametrize("kinds", list(itertools.product(["plain", "inline", "blank"], repeat=3)))
def test_remove_key_keeps_every_other_comment(kinds, post):
    head, blocks = _map_doc(list(kinds), post)
    src = head + "".join(blocks) + post
    for j in range(len(blocks)):
        data = _load(src)
        ops.remove_key(data["section"], "abcdef"[j], parent=data, parent_key="section")
        want = head + "".join(_without(blocks, j)) + post
        assert _dump(data) == want, f"removed {'abcdef'[j]} from\n{src}"


@pytest.mark.parametrize("kind", list(MAP_BLOCKS))
@pytest.mark.parametrize("post", POSTS)
def test_remove_key_for_every_entry_shape(kind, post):
    head, blocks = _map_doc([kind, "plain", kind, "inline"], post)
    src = head + "".join(blocks) + post
    for j in range(len(blocks)):
        data = _load(src)
        ops.remove_key(data["section"], "abcdef"[j], parent=data, parent_key="section")
        want = head + "".join(_without(blocks, j)) + post
        assert _dump(data) == want, f"removed {'abcdef'[j]} from\n{src}"


def test_remove_only_key_leaves_an_empty_section():
    data = _load("section:\n  # lead a\n  a: 1\n")
    ops.remove_key(data["section"], "a", parent=data, parent_key="section")
    assert "a" not in data["section"]


SEQ_BLOCKS = {
    "map": "  # lead {n}\n  - name: {n}\n    x: 1\n",
    "map_inline": "  # lead {n}\n  - name: {n}\n    x: 1  # inline {n}\n",
    "scalar": "  # lead {n}\n  - {n}\n",
    "scalar_inline": "  # lead {n}\n  - {n}  # inline {n}\n",
}


@pytest.mark.parametrize("post", POSTS)
@pytest.mark.parametrize("kind", list(SEQ_BLOCKS))
def test_remove_item_keeps_every_other_comment(kind, post):
    blocks = [SEQ_BLOCKS[kind].format(n="abc"[i]) for i in range(3)]
    head = "# header\nsection:\n"
    src = head + "".join(blocks) + post
    for j in range(3):
        data = _load(src)
        ops.remove_item(data["section"], j, parent=data, parent_key="section")
        want = head + "".join(_without(blocks, j)) + post
        assert _dump(data) == want, f"removed item {j} from\n{src}"


def test_rename_key_keeps_position_and_comments():
    src = (
        "section:\n  # lead a\n  a:\n    x: 1  # inline a\n  # lead b\n  b:\n    x: 2\n"
        "  # lead c\n  c:\n    x: 3\n"
    )
    data = _load(src)
    ops.rename_key(data["section"], "b", "bee")
    assert _dump(data) == src.replace("  b:", "  bee:")
    data = _load(src)
    ops.rename_key(data["section"], "a", "alpha")
    assert _dump(data) == src.replace("  a:", "  alpha:")
    data = _load(src)
    ops.rename_key(data["section"], "c", "see")
    assert _dump(data) == src.replace("  c:", "  see:")


def test_rename_key_to_itself_is_a_no_op():
    src = "section:\n  a: 1\n  b: 2\n"
    data = _load(src)
    ops.rename_key(data["section"], "a", "a")
    assert _dump(data) == src


def test_place_in_order_goes_before_the_first_present_successor():
    order = ("name", "length", "extended", "signals")
    m = _load("name: x\nsignals:\n  s: 1\n")
    ops.place_in_order(m, "length", 8, order)
    assert list(m) == ["name", "length", "signals"]
    ops.place_in_order(m, "extended", True, order)
    assert list(m) == ["name", "length", "extended", "signals"]
    ops.place_in_order(m, "length", 9, order)
    assert m["length"] == 9 and list(m) == ["name", "length", "extended", "signals"]


def test_place_in_order_puts_an_unknown_key_last():
    m = _load("a: 1\nb: 2\n")
    ops.place_in_order(m, "zzz", 3, ("a", "b"))
    assert list(m) == ["a", "b", "zzz"]


def test_place_after_follows_the_last_present_predecessor():
    m = _load("tx_id: 1\nidentity:\n  a: 1\npids: {}\n")
    ops.place_after(m, "revisions", {}, ("tx_id", "rx_id", "identity", "can_bus"))
    assert list(m) == ["tx_id", "identity", "revisions", "pids"]
    ops.place_after(m, "other", 1, ("nothing",))
    assert list(m)[-1] == "other"


def test_find_key_returns_the_files_own_spelling():
    m = _load("Charging: 1\nDriving: 2\n")
    assert ops.find_key(m, "driving", lambda k: str(k).lower()) == "Driving"
    assert ops.find_key(m, "nope") is None


def test_flow_builders_render_inline():
    data = _load("x: 1\n")
    data["seq"] = ops.flow_seq(["a", "b"])
    data["map"] = ops.flow_map({"to": "functional", "send": "10D1"})
    assert _dump(data).splitlines()[1:] == ["seq: [a, b]", "map: {to: functional, send: 10D1}"]


@pytest.mark.parametrize("count", [1, 2, 3])
@pytest.mark.parametrize("post", POSTS)
def test_insert_item_before_last_keeps_the_last_items_comment(count, post):
    names = "abc"[: count - 1] + "z"
    blocks = [SEQ_BLOCKS["map"].format(n=n) for n in names]
    head = "# header\nsection:\n"
    data = _load(head + "".join(blocks) + post)
    entry = ops.CommentedMap()
    entry["name"] = "new"
    entry["x"] = 9
    ops.insert_item_before_last(data, "section", entry)
    new_block = "  - name: new\n    x: 9\n"
    want = head + "".join(blocks[:-1]) + new_block + blocks[-1] + post
    assert _dump(data) == want
