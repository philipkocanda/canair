"""The router lists every edit target, and every target honours the shared contract."""

from __future__ import annotations

import importlib
import pkgutil

import pytest
from ruamel.yaml.comments import CommentedMap, CommentedSeq

import canair.yaml_edit as package
from canair.core.edit_errors import EditError
from canair.yaml_edit.doc import Target
from canair.yaml_edit.targets import open_target, target, targets


def test_every_target_declared_in_the_package_is_registered():
    declared = {}
    for info in pkgutil.walk_packages(package.__path__, package.__name__ + "."):
        module = importlib.import_module(info.name)
        for value in vars(module).values():
            if isinstance(value, Target):
                declared[value.key] = value
    assert set(declared) == set(targets()), (
        "a Target is not listed in src/canair/yaml_edit/targets.py"
    )


@pytest.mark.parametrize("key", list(targets()))
def test_each_target_is_well_formed(key):
    t = target(key)
    assert t.key == key
    assert issubclass(t.error, EditError)
    assert t.kind in (CommentedMap, CommentedSeq)
    assert t.path is not None or key == "ecu"
    assert t.after_write is not None


def test_an_unknown_target_names_the_known_ones():
    with pytest.raises(KeyError, match="groups"):
        target("nope")


class _Prof:
    def __init__(self, root):
        self.groups_file = root / "groups.yaml"


def test_open_target_edits_by_name(tmp_path):
    prof = _Prof(tmp_path)
    with open_target("groups", prof, create=True) as doc:
        doc.section["one"] = {"members": ["BMS"]}
    assert "one:" in prof.groups_file.read_text()
