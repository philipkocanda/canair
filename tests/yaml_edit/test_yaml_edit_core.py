"""Tests for the shared write path: commit_text, edit_doc and the schema override."""

from __future__ import annotations

import dataclasses
import os
import stat

import pytest
from ruamel.yaml.comments import CommentedSeq

from canair.core.edit_errors import EditError, translate
from canair.yaml_edit import commit
from canair.yaml_edit import groups as groups_edit
from canair.yaml_edit.doc import Target, edit_doc


class DemoError(EditError):
    pass


def _reject(path):
    if "bad" in path.read_text():
        raise DemoError("rejected")


# ── commit_text ─────────────────────────────────────────────────────────────


def test_commit_writes_and_runs_after_write(tmp_path):
    path = tmp_path / "f.yaml"
    path.write_text("a: 1\n")
    calls = []
    commit.commit_text(
        path,
        "a: 2\n",
        "a: 1\n",
        check=_reject,
        error=DemoError,
        after_write=lambda: calls.append(1),
    )
    assert path.read_text() == "a: 2\n"
    assert calls == [1]


def test_commit_reverts_an_existing_file_on_a_failed_check(tmp_path):
    path = tmp_path / "f.yaml"
    path.write_text("a: 1\n")
    calls = []
    with pytest.raises(DemoError, match="rejected"):
        commit.commit_text(
            path,
            "bad: 1\n",
            "a: 1\n",
            check=_reject,
            error=DemoError,
            after_write=lambda: calls.append(1),
        )
    assert path.read_text() == "a: 1\n"
    assert calls == [1, 1], "the cache is refreshed after the write and after the revert"


def test_commit_removes_a_file_that_did_not_exist_on_a_failed_check(tmp_path):
    path = tmp_path / "sub" / "f.yaml"
    with pytest.raises(DemoError):
        commit.commit_text(path, "bad: 1\n", None, check=_reject, error=DemoError)
    assert not path.exists()


def test_commit_wraps_a_foreign_exception_in_the_editors_error(tmp_path):
    path = tmp_path / "f.yaml"
    path.write_text("a: 1\n")

    def boom(_path):
        raise KeyError("x")

    with pytest.raises(DemoError, match="reverted"):
        commit.commit_text(path, "a: 2\n", "a: 1\n", check=boom, error=DemoError)
    assert path.read_text() == "a: 1\n"


def test_commit_runs_the_schema_check_after_the_structural_one(tmp_path):
    path = tmp_path / "f.yaml"
    path.write_text("a: 1\n")

    def schema(_path):
        raise DemoError("schema says no")

    with pytest.raises(DemoError, match="schema says no"):
        commit.commit_text(path, "a: 2\n", "a: 1\n", check=None, error=DemoError, validate=schema)
    assert path.read_text() == "a: 1\n"


def test_commit_keeps_crlf_line_endings(tmp_path):
    path = tmp_path / "f.yaml"
    path.write_bytes(b"a: 1\r\nb: 2\r\n")
    commit.commit_text(path, "a: 1\nb: 3\n", "a: 1\nb: 2\n", check=None, error=DemoError)
    assert path.read_bytes() == b"a: 1\r\nb: 3\r\n"
    with pytest.raises(DemoError):
        commit.commit_text(path, "bad: 1\n", "a: 1\nb: 3\n", check=_reject, error=DemoError)
    assert path.read_bytes() == b"a: 1\r\nb: 3\r\n"


def test_a_failed_write_leaves_the_old_file_whole_and_no_temp_file(tmp_path, monkeypatch):
    path = tmp_path / "f.yaml"
    path.write_text("a: 1\n")

    def fail(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError):
        commit.write_atomic(path, "a: 2\n")
    assert path.read_text() == "a: 1\n"
    assert [p.name for p in tmp_path.iterdir()] == ["f.yaml"]


def test_write_keeps_the_files_permissions(tmp_path):
    path = tmp_path / "f.yaml"
    path.write_text("a: 1\n")
    path.chmod(0o640)
    commit.write_atomic(path, "a: 2\n")
    assert stat.S_IMODE(path.stat().st_mode) == 0o640


def test_a_new_file_gets_the_default_mode_not_the_temp_files(tmp_path):
    path = tmp_path / "new.yaml"
    commit.write_atomic(path, "a: 1\n")
    umask = os.umask(0)
    os.umask(umask)
    assert stat.S_IMODE(path.stat().st_mode) == 0o666 & ~umask


def test_schema_validation_override():
    assert commit.resolve_validate(True) is True
    with commit.schema_validation(False):
        assert commit.resolve_validate(True) is False
        with commit.schema_validation(True):
            assert commit.resolve_validate(False) is True
    assert commit.resolve_validate(False) is False


def test_translate_renames_the_error_and_keeps_the_message():
    with pytest.raises(DemoError, match="inner words"):
        with translate(ValueError, DemoError):
            raise ValueError("inner words")


# ── edit_doc ────────────────────────────────────────────────────────────────


def _target(**kw):
    def check(data):
        if "bad" in (data or {}).get("items", {}):
            raise DemoError("bad item")

    base = {
        "key": "demo",
        "path": lambda profile: profile.demo_file,
        "error": DemoError,
        "section": "items",
        "check": check,
    }
    return Target(**{**base, **kw})


class _Prof:
    def __init__(self, root):
        self.demo_file = root / "demo.yaml"


def test_edit_doc_refuses_a_missing_file_unless_asked_to_create(tmp_path):
    prof = _Prof(tmp_path)
    with pytest.raises(DemoError, match=r"no demo\.yaml to edit"):
        with edit_doc(_target(), prof):
            pass
    with edit_doc(_target(), prof, create=True) as doc:
        doc.section["a"] = 1
    assert prof.demo_file.read_text() == "items:\n  a: 1\n"


def test_edit_doc_writes_nothing_when_the_body_raises(tmp_path):
    prof = _Prof(tmp_path)
    prof.demo_file.write_text("items:\n  a: 1\n")
    with pytest.raises(RuntimeError):
        with edit_doc(_target(), prof) as doc:
            doc.section["b"] = 2
            raise RuntimeError("stop")
    assert prof.demo_file.read_text() == "items:\n  a: 1\n"


def test_edit_doc_reverts_when_the_structural_check_fails(tmp_path):
    prof = _Prof(tmp_path)
    prof.demo_file.write_text("items:\n  a: 1\n")
    with pytest.raises(DemoError, match="bad item"):
        with edit_doc(_target(), prof) as doc:
            doc.section["bad"] = 1
    assert prof.demo_file.read_text() == "items:\n  a: 1\n"


def test_edit_doc_discard_leaves_the_file_untouched(tmp_path):
    prof = _Prof(tmp_path)
    prof.demo_file.write_text("# keep   me\nitems:   {a:   1}\n")
    with edit_doc(_target(), prof) as doc:
        doc.discard = True
    assert prof.demo_file.read_text() == "# keep   me\nitems:   {a:   1}\n"


def test_edit_doc_delete_file(tmp_path):
    prof = _Prof(tmp_path)
    prof.demo_file.write_text("items:\n  a: 1\n")
    with edit_doc(_target(), prof) as doc:
        doc.delete_file = True
    assert not prof.demo_file.exists()


def test_edit_doc_writes_the_header_only_into_a_new_file(tmp_path):
    prof = _Prof(tmp_path)
    target = _target(header="# made by canair\n")
    with edit_doc(target, prof, create=True) as doc:
        doc.section["a"] = 1
    assert prof.demo_file.read_text() == "# made by canair\nitems:\n  a: 1\n"
    with edit_doc(target, prof) as doc:
        doc.section["b"] = 2
    assert prof.demo_file.read_text().count("# made by canair") == 1


@pytest.mark.parametrize("body", ["- a\n", "items: [1, 2]\n"])
def test_edit_doc_rejects_a_file_of_the_wrong_shape(tmp_path, body):
    prof = _Prof(tmp_path)
    prof.demo_file.write_text(body)
    with pytest.raises(DemoError, match=r"must be a|top-level"):
        with edit_doc(_target(), prof):
            pass


def test_edit_doc_keeps_the_files_own_sequence_layout(tmp_path):
    prof = _Prof(tmp_path)
    prof.demo_file.write_text("items:\n    - a\n    - b\n")
    with edit_doc(
        _target(
            section="items",
            kind=CommentedSeq,
            sequence=(2, 0),
        ),
        prof,
    ) as doc:
        doc.section.append("c")
    assert prof.demo_file.read_text() == "items:\n    - a\n    - b\n    - c\n"


def test_the_schema_check_follows_the_override(tmp_path):
    prof = _Prof(tmp_path)
    prof.demo_file.write_text("items:\n  a: 1\n")

    def schema(_path):
        raise DemoError("schema says no")

    target = _target(validate=schema)
    with pytest.raises(DemoError, match="schema"):
        with edit_doc(target, prof) as doc:
            doc.section["b"] = 2
    with commit.schema_validation(False):
        with edit_doc(target, prof) as doc:
            doc.section["b"] = 2
    assert "b: 2" in prof.demo_file.read_text()


# ── the editors on top of it ────────────────────────────────────────────────


def test_removing_the_first_group_keeps_the_next_groups_comment(tmp_path):
    prof = _Prof(tmp_path)
    prof.groups_file = tmp_path / "groups.yaml"
    prof.groups_file.write_text(
        "groups:\n"
        "  # about charging\n  charging:\n    members: [BMS]\n"
        "  # about driving\n  driving:\n    members: [VCU]\n"
    )
    groups_edit.remove_group("charging", profile=prof)
    assert prof.groups_file.read_text() == (
        "groups:\n  # about driving\n  driving:\n    members: [VCU]\n"
    )


def test_renaming_a_group_keeps_the_comments_between_groups(tmp_path):
    prof = _Prof(tmp_path)
    prof.groups_file = tmp_path / "groups.yaml"
    src = (
        "groups:\n"
        "  # about charging\n  charging:\n    members: [BMS]\n"
        "  # about driving\n  driving:\n    members: [VCU]\n"
        "  # about last\n  last:\n    members: [MCU]\n"
    )
    prof.groups_file.write_text(src)
    groups_edit.rename_group("driving", "drive", profile=prof)
    assert prof.groups_file.read_text() == src.replace("  driving:", "  drive:")


def test_groups_failed_check_goes_through_the_target(tmp_path, monkeypatch):
    prof = _Prof(tmp_path)
    prof.groups_file = tmp_path / "groups.yaml"
    groups_edit.add_group("a", ["BMS"], profile=prof)
    before = prof.groups_file.read_text()

    def boom(_data):
        raise groups_edit.GroupsEditError("nope")

    monkeypatch.setattr(groups_edit, "GROUPS", dataclasses.replace(groups_edit.GROUPS, check=boom))
    with pytest.raises(groups_edit.GroupsEditError):
        groups_edit.add_group("b", ["VCU"], profile=prof)
    assert prof.groups_file.read_text() == before
