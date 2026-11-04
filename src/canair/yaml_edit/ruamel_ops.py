"""Pure helpers on ruamel ``CommentedMap`` / ``CommentedSeq`` trees.

Every editor of a hand-curated YAML file changes the same few things in the same few ways: it
builds flow-style lists, finds an entry whose key it has to normalise first, renames or inserts
a key at its canonical position, and removes an entry. The helpers here do those once, and do
them without losing the comments around the entry. Nothing here reads or writes a file.

**Where ruamel keeps a comment.** A comment block *above* an entry is not stored on that entry.
It rides on the *tail* of the entry before it: on the slot of the last scalar of the previous
entry's value (``ca.items[last_key][2]`` of a mapping, ``ca.items[i][0]`` of a sequence), after
that scalar's own inline comment. The block above the *first* entry sits on the parent key's
pre-comment slot (``parent.ca.items[key][3]``) and on the section's ``ca.comment``. A bare
``del`` therefore removes the comment of the *next* entry together with the victim and leaves
the victim's own comment on the next one, so :func:`remove_key` and :func:`remove_item`
transplant the victim's tail onto the entry before it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import CommentMark
from ruamel.yaml.scalarstring import DoubleQuotedScalarString
from ruamel.yaml.tokens import CommentToken

# ── builders and lookups ─────────────────────────────────────────────────────────────────────


def flow_seq(items: Sequence[Any]) -> CommentedSeq:
    """``items`` as an inline list (``[BMS:2101, OBC]``)."""
    seq = CommentedSeq(list(items))
    seq.fa.set_flow_style()
    return seq


def flow_map(pairs: Mapping[str, Any]) -> CommentedMap:
    """``pairs`` as an inline mapping (``{to: functional, send: 10D1}``)."""
    m = CommentedMap(pairs)
    m.fa.set_flow_style()
    return m


def find_key(mapping: Mapping, wanted: Any, norm: Callable[[Any], Any] = lambda k: k) -> Any:
    """The key of ``mapping`` whose ``norm(key)`` equals ``wanted``, or ``None``.

    The editors compare normalised names (lower-cased group names, upper-cased PIDs) but must
    write back the key as the file spells it, so this returns the file's own key.
    """
    return next((k for k in mapping if norm(k) == wanted), None)


# ── placing a key ────────────────────────────────────────────────────────────────────────────


def place_in_order(mapping: CommentedMap, key: str, value: Any, order: Sequence[str]) -> None:
    """Set ``mapping[key]``; a new key goes before the first present key that ``order`` ranks after it.

    A plain assignment appends, so a ``name`` added to a message that already has ``signals``
    would land after the whole signal block. A key outside ``order`` goes last. Existing keys
    keep their place.
    """
    if key in mapping:
        mapping[key] = value
        return
    successors = order[order.index(key) + 1 :] if key in order else ()
    present = [k for k in successors if k in mapping]
    if not present:
        mapping[key] = value
        return
    mapping.insert(list(mapping).index(present[0]), key, value)


def place_after(mapping: CommentedMap, key: str, value: Any, after: Sequence[str]) -> None:
    """Set ``mapping[key]``; a new key goes straight after the last of ``after`` present, else last."""
    if key in mapping:
        mapping[key] = value
        return
    keys = list(mapping)
    pos = max((keys.index(a) + 1 for a in after if a in mapping), default=len(keys))
    mapping.insert(pos, key, value)


def rename_key(mapping: CommentedMap, old: Any, new: Any) -> None:
    """Rename ``old`` to ``new`` in place: same position, same value, same comments.

    Rebuilding the mapping instead would drop every comment between its entries, which ruamel
    keys by the entry's name.
    """
    if old == new:
        return
    pos = list(mapping).index(old)
    value = mapping[old]
    slot = mapping.ca.items.pop(old, None)
    del mapping[old]
    mapping.insert(pos, new, value)
    if slot is not None:
        mapping.ca.items[new] = slot


# ── comment slots ────────────────────────────────────────────────────────────────────────────

_Slot = tuple[dict, Any, int]  # (a ``ca.items`` dict, the key within it, the slot index)


def _is_block(node: Any) -> bool:
    return (
        isinstance(node, (CommentedMap, CommentedSeq))
        and len(node) > 0
        and not node.fa.flow_style()
    )


def _tail_slots(node: Any, items: dict, key: Any, slot: int) -> list[_Slot]:
    """Candidate slots for the comment after an entry, deepest first.

    ``node`` is the entry's value, held in ``items[key]``'s container. A flow collection
    counts as a scalar: its trailing comment is on the key that holds it.
    """
    chain: list[_Slot] = [(items, key, slot)]
    while _is_block(node):
        if isinstance(node, CommentedMap):
            last = list(node)[-1]
            chain.append((node.ca.items, last, 2))
            node = node[last]
        else:
            last = len(node) - 1
            chain.append((node.ca.items, last, 0))
            node = node[last]
    return chain[::-1]


def _entry_tail(container: Any, key: Any) -> _Slot:
    """Where the comment after ``container[key]`` is (or would be) stored."""
    slot = 2 if isinstance(container, CommentedMap) else 0
    chain = _tail_slots(container[key], container.ca.items, key, slot)
    for items, k, s in chain:
        cell = items.get(k)
        if cell is not None and cell[s] is not None:
            return items, k, s
    return chain[0]


def _token(items: dict, key: Any, slot: int) -> CommentToken | None:
    cell = items.get(key)
    tok = cell[slot] if cell is not None else None
    return tok if isinstance(tok, CommentToken) else None


def _split_tail(value: str) -> tuple[str, str]:
    """A tail comment as (rest of the entry's own line, the lines that follow).

    ``"# inline\\n  # lead of the next\\n"`` -> ``("# inline\\n", "  # lead of the next\\n")``.
    """
    cut = value.find("\n") + 1
    return value[:cut], value[cut:]


def _make_token(value: str, column: int = 2) -> CommentToken:
    return CommentToken(value, CommentMark(column))


def _retoken(old: CommentToken | None, value: str) -> CommentToken:
    """A token with ``value`` that sits where ``old`` did (an inline comment keeps its column)."""
    if old is None:
        return _make_token(value)
    return CommentToken(value, old.start_mark)


def _pre_tokens(rest: str) -> list[CommentToken] | None:
    """``rest`` as the per-line token list a first entry's pre-comment slot holds."""
    toks = []
    for line in rest.splitlines():
        if line.strip():
            toks.append(_make_token(f"{line.strip()}\n", len(line) - len(line.lstrip())))
        else:
            toks.append(_make_token("\n", 0))  # a blank line
    return toks or None


def _without_blank_head(rest: str) -> str:
    """``rest`` minus its leading blank lines (indentation of the first real line is kept)."""
    lines = rest.splitlines(keepends=True)
    while lines and not lines[0].strip():
        lines.pop(0)
    return "".join(lines)


def _set_cell(items: dict, key: Any, slot: int, tok: Any) -> None:
    cell = items.get(key)
    if cell is None:
        if tok is None:
            return
        cell = items[key] = [None, None, None, None]
    cell[slot] = tok
    if all(c is None for c in cell):
        del items[key]


def _remove_entry(container: Any, ident: Any, parent: Any | None, parent_key: Any | None) -> None:
    """Remove ``container[ident]`` (a key or an index) keeping every other entry's comments.

    The victim's own leading comment goes with it. The comment above the entry after it, which
    ruamel keeps on the victim's tail, moves to the tail of the entry before the victim (or,
    for a first entry, to the section's head), so each surviving entry keeps the comment
    written above it.
    """
    is_map = isinstance(container, CommentedMap)
    keys = list(container) if is_map else list(range(len(container)))
    idx = keys.index(ident)
    v_items, v_key, v_slot = _entry_tail(container, ident)
    v_tok = _token(v_items, v_key, v_slot)
    _, lead_of_next = _split_tail(str(v_tok.value)) if v_tok is not None else ("", "")

    if idx > 0:
        p_items, p_key, p_slot = _entry_tail(container, keys[idx - 1])
        p_tok = _token(p_items, p_key, p_slot)
        head = _split_tail(str(p_tok.value))[0] if p_tok is not None else "\n"
        new = head + lead_of_next
        # A lone line break says nothing; two or more are a blank line worth keeping.
        keep = new.strip("\n") or new.count("\n") > 1
        _set_cell(p_items, p_key, p_slot, _retoken(p_tok, new) if keep else None)
    # The victim's own pre-comment on its key (the shape a flow-style neighbour leaves).
    if is_map:
        container.ca.items.pop(ident, None)
    if idx == 0:
        # A first entry has nothing above it to separate from, so blank lines are dropped.
        pre = _pre_tokens(_without_blank_head(lead_of_next))
        if is_map and hasattr(container.ca, "comment"):
            container.ca.comment = [None, pre] if pre else None
        if parent is not None and parent_key is not None:
            _set_cell(parent.ca.items, parent_key, 3, pre)
    del container[ident]  # a CommentedSeq re-indexes its own ca.items


def remove_key(
    mapping: Any, key: Any, *, parent: Any | None = None, parent_key: Any | None = None
) -> None:
    """Remove ``mapping[key]`` and the comment above it; every other entry keeps its own.

    Pass ``parent`` and ``parent_key`` (``parent[parent_key] is mapping``) so removing the first
    entry can also fix the comment block held on the parent's key.
    """
    _remove_entry(mapping, key, parent, parent_key)


def remove_item(
    seq: CommentedSeq, index: int, *, parent: Any | None = None, parent_key: Any | None = None
) -> None:
    """Remove ``seq[index]`` and the comment above it; every other item keeps its own."""
    _remove_entry(seq, index, parent, parent_key)


def insert_item_before_last(parent: Any, key: Any, entry: CommentedMap) -> None:
    """Insert ``entry`` just before the last item of ``parent[key]``, keeping that item's comment.

    The comment above the last item (a catch-all kept last, such as ``ALL``) stays above it:
    it moves from the previous item's tail (or the section's head) to the new entry's tail.
    """
    seq = parent[key]
    last = len(seq) - 1
    if last >= 1:
        p_items, p_key, p_slot = _entry_tail(seq, last - 1)
        p_tok = _token(p_items, p_key, p_slot)
        head, rest = _split_tail(str(p_tok.value)) if p_tok is not None else ("\n", "")
        if p_tok is not None and rest:
            _set_cell(p_items, p_key, p_slot, _retoken(p_tok, head))
    else:
        head_toks = parent.ca.items.get(key, [None] * 4)[3] or []
        rest = "".join(
            (" " * tok.start_mark.column + str(tok.value)) if str(tok.value).strip() else "\n"
            for tok in head_toks
        )
        _set_cell(parent.ca.items, key, 3, None)
        if hasattr(seq.ca, "comment"):
            seq.ca.comment = None
    seq.insert(last, entry)
    if rest:
        e_items, e_key, e_slot = _entry_tail(seq, last)
        _set_cell(e_items, e_key, e_slot, _make_token("\n" + rest))


def state_token(token: str):
    """A vehicle-state token as the round-trip dumper should write it.

    A token YAML 1.1 reads as a boolean (``ON``, ``OFF``, ``YES``, ``NO``…) is
    double-quoted; ruamel would otherwise pick single quotes, and the hand-written
    files and the rename's line rewriter use double. Any other token stays plain.
    """
    from canair.states import needs_yaml_quotes

    return DoubleQuotedScalarString(token) if needs_yaml_quotes(token) else token
