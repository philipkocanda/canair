"""scripts/check_lfs_objects.py: the pointer-vs-server comparison, with a fake batch API."""

from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_lfs_objects.py"

spec = importlib.util.spec_from_file_location("check_lfs_objects", SCRIPT)
assert spec and spec.loader
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def _file(oid: str, name: str, size: int = 10) -> dict:
    return {"oid": oid, "size": size, "name": name}


def _server(present: set[str]):
    calls: list[list[dict]] = []

    def batch(objects: list[dict]) -> list[dict]:
        calls.append(objects)
        return [
            {"oid": o["oid"], "size": o["size"]}
            if o["oid"] in present
            else {"oid": o["oid"], "error": {"code": 404, "message": "Object does not exist"}}
            for o in objects
        ]

    return batch, calls


def test_reports_only_the_absent_objects_with_their_paths():
    batch, _ = _server({"a"})
    missing = checker.missing_objects([_file("a", "a.csv"), _file("b", "b.csv")], batch)
    assert [(m["oid"], m["names"]) for m in missing] == [("b", ["b.csv"])]


def test_one_object_under_several_paths_is_asked_once_and_names_every_path():
    batch, calls = _server(set())
    missing = checker.missing_objects([_file("a", "x"), _file("a", "y")], batch)
    assert sum(len(c) for c in calls) == 1
    assert missing[0]["names"] == ["x", "y"]


def test_large_sets_are_split_into_batches():
    batch, calls = _server({f"{i:03d}" for i in range(250)})
    files = [_file(f"{i:03d}", f"f{i}") for i in range(250)]
    assert checker.missing_objects(files, batch) == []
    assert [len(c) for c in calls] == [100, 100, 50]


def test_lfs_endpoint_normalises_the_remote_url():
    assert checker.lfs_endpoint("https://github.com/o/r") == "https://github.com/o/r.git/info/lfs"
    assert checker.lfs_endpoint("https://github.com/o/r.git/") == (
        "https://github.com/o/r.git/info/lfs"
    )
