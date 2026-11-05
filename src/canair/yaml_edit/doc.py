"""Round-trip editing of one section of one YAML file: :func:`edit_doc` and :class:`Target`.

A :class:`Target` says which file an editor works on and how to check it: where it lives in a
profile, which top-level section it owns, the error it raises, and the checks a written file
must pass. :func:`edit_doc` opens it, hands the editor a :class:`Doc`, and — when the editor
returns without raising — writes the file, re-parses it, checks it and reverts on any failure
(:func:`canair.yaml_edit.commit.commit_text`). Comments, quoting and field order survive because
the tree is ruamel's round-trip tree.

    with edit_doc(GROUPS, profile) as doc:
        remove_key(doc.section, "charging", parent=doc.data, parent_key="groups")
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ruamel.yaml.comments import CommentedMap, CommentedSeq

from canair.core import yaml_io
from canair.core import yaml_rt as rt
from canair.core.edit_errors import EditError

from .commit import commit_text, noop, resolve_validate

if TYPE_CHECKING:
    from canair.profile import Profile


@dataclass(frozen=True)
class Target:
    """One kind of YAML file an editor works on, and the rules for writing it back."""

    key: str
    """Short name of the target (``"groups"``); how tests and the registry refer to it."""
    path: Callable[..., Path] | None
    """``(profile, **route) -> file``; ``None`` for a target addressed by an explicit path."""
    error: type[EditError]
    """What a rejected edit raises."""
    section: str | None = None
    """The top-level key whose value is the part the editor changes (``None``: the whole file)."""
    kind: type = CommentedMap
    """``CommentedMap`` or ``CommentedSeq``: the type of ``section`` (made if absent)."""
    check: Callable[[Any], None] | None = None
    """Structural check of the parsed (PyYAML) document, run on every write; raises ``error``."""
    validate: Callable[[Path], None] | None = None
    """Heavier schema check of the written file; skipped by ``edit_doc(..., validate=False)``."""
    header: str = ""
    """Text written first when the file is created."""
    sequence: tuple[int, int] = (4, 2)
    """Block-sequence (indent, dash offset) for a file with no list to copy the layout from."""
    after_write: Callable[[], None] = noop
    """Runs after a write and after a revert (drops caches)."""

    @property
    def noun(self) -> str:
        return "list" if self.kind is CommentedSeq else "mapping"


class Doc:
    """An open document: its path, its original text, its tree and the part to edit."""

    def __init__(
        self, target: Target, path: Path, original: str | None, data: CommentedMap, section: Any
    ) -> None:
        self.target = target
        self.path = path
        self.original = original
        self.data = data
        self.section = section
        self.delete_file = False
        """Set by an editor that leaves the file empty and wants it removed."""
        self.discard = False
        """Set by an editor that found nothing to change: the file is left untouched."""

    @property
    def created(self) -> bool:
        return self.original is None

    def render(self) -> str:
        """The tree as YAML text, in the file's own block-sequence layout."""
        seq, off = rt.detect_sequence_indent(self.original or "") or self.target.sequence
        buf = StringIO()
        rt.dump(self.data, buf, sequence=seq, offset=off)
        text = buf.getvalue()
        return self.target.header + text if self.original is None else text

    def commit(self, validate: bool = True) -> None:
        """Write the document, or delete the file when :attr:`delete_file` is set."""
        t = self.target
        if self.discard:
            return
        if self.delete_file:
            self.path.unlink(missing_ok=True)
            t.after_write()
            return
        check = None
        if t.check is not None:
            doc_check = t.check
            check = lambda p: doc_check(yaml_io.safe_load(p.read_text()))  # noqa: E731
        commit_text(
            self.path,
            self.render(),
            self.original,
            check=check,
            error=t.error,
            validate=t.validate if resolve_validate(validate) else None,
            after_write=t.after_write,
        )


def load_doc(
    target: Target,
    path: Path,
    *,
    create: bool = False,
    section_of: Callable | None = None,
    missing: str | None = None,
) -> Doc:
    """Read ``path`` into a :class:`Doc`, scaffolding the file's section when it is absent.

    A missing file is an error unless ``create``; the scaffold of a new file is an empty
    document holding an empty ``section``.
    """
    error = target.error
    original = path.read_text() if path.exists() else None
    if original is None and not create:
        raise error(missing or f"no {path.name} to edit")
    data = rt.round_trip_yaml().load(original) if original else None
    if data is None:
        data = CommentedMap()
    if not isinstance(data, dict):
        raise error(f"{path.name}: top-level must be a mapping")
    section: Any = data
    if target.section is not None:
        if data.get(target.section) is None:
            data[target.section] = target.kind()
        section = data[target.section]
        want = dict if target.kind is CommentedMap else list
        if not isinstance(section, want):
            raise error(f"{path.name}: '{target.section}:' must be a {target.noun}")
    if section_of is not None:
        section = section_of(data)
    return Doc(target, path, original, data, section)


@contextmanager
def edit_doc(
    target: Target,
    profile: Profile | None = None,
    *,
    path: Path | None = None,
    create: bool = False,
    validate: bool = True,
    section_of: Callable | None = None,
    missing: str | None = None,
    **route: Any,
) -> Iterator[Doc]:
    """Open ``target`` for editing; leaving the block without an exception writes it back.

    ``path`` overrides the profile lookup (an explicit ``--dir``); ``route`` is passed to
    ``target.path`` (a signals editor routes by ``bus=``). ``create`` allows a file that does not
    exist yet (``missing`` words the error when it must). ``validate=False`` skips the target's schema check but not its structural one.
    """
    if path is None:
        if target.path is None:
            raise TypeError(f"target {target.key!r} needs an explicit path")
        if profile is None:
            from canair.profile import active

            profile = active()
        path = target.path(profile, **route)
    doc = load_doc(target, path, create=create, section_of=section_of, missing=missing)
    yield doc
    doc.commit(validate)


__all__ = ["Doc", "Target", "edit_doc", "load_doc"]
