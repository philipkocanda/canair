"""scripts/check/deferred_*.py and hoist_deferred.py: classify and hoist deferred imports."""

from __future__ import annotations

import sys
from pathlib import Path
from textwrap import dedent

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS / "check"))

import deferred_hoist as hoist  # noqa: E402
import deferred_imports as classify  # noqa: E402
import deferred_scan as scan  # noqa: E402

KNOWN = {"canair", "canair.a", "canair.b", "canair.c", "canair.pkg", "canair.pkg.sub"}


def _scan(source: str, module: str = "canair.m"):
    path = Path("/x/canair/m.py")
    return scan.scan_text(module, path, dedent(source), KNOWN | {module})


def _hoist(source: str, lines: set[int] | None = None, module: str = "canair.m"):
    path = scan.graph.REPO_ROOT / "src" / "canair" / "m.py"
    text = dedent(source)
    wanted = lines if lines is not None else {i.line for i in _scan(source, module)[0]}
    return hoist.hoist_text(module, path, text, wanted, KNOWN | {module})


def test_only_function_level_imports_are_deferred():
    found, edges, _heavy, others = _scan(
        """
        from __future__ import annotations
        from typing import TYPE_CHECKING

        from canair.a import x

        if TYPE_CHECKING:
            from canair.b import y

        try:
            from canair.c import z
        except ImportError:
            z = None

        def f():
            from canair.b import y
            import json
            return y, json

        class K:
            from canair.c import w
        """
    )
    assert [(i.source, i.line) for i in found] == [("canair.b", 16)]
    assert edges == {"canair.a", "canair.c", "canair"}
    assert others == 3  # the TYPE_CHECKING import, the try import and the class-body import


@pytest.mark.parametrize(
    ("source", "reason"),
    [
        ("def f():\n    from canair.a import x  # deferred: textual\n", True),
        ("def f():\n    # deferred: textual\n    from canair.a import x\n", True),
        ("def f():\n    # deferred: textual\n    # adds 65 ms\n    from canair.a import x\n", True),
        ("def f():\n    # a comment\n    from canair.a import x\n", False),
        ("def f():\n    # deferred: textual\n\n    from canair.a import x\n", False),
        ("def f():\n    from canair.a import x\n", False),
    ],
)
def test_reason_is_a_comment_on_the_line_or_just_above(source, reason):
    found = _scan(source)[0]
    assert [i.has_reason for i in found] == [reason]


def test_hoist_merges_into_an_existing_import_and_adds_no_line():
    source = '''
        """Doc."""

        from canair.a import x


        def f():
            from canair.a import y

            return x + y
    '''
    result = _hoist(source)
    assert "from canair.a import x, y" in result.text
    assert "    from canair.a" not in result.text
    assert result.text.count("\n") < dedent(source).count("\n")
    assert [i.line for i in result.hoisted] == [8]


def test_hoist_keeps_a_trailing_comment_and_drops_a_stale_reason():
    result = _hoist(
        """
        import os


        def f():
            # deferred: cycle with a
            from canair.a import x  # noqa: F401

            return os
        """
    )
    assert "from canair.a import x  # noqa: F401" in result.text
    assert "deferred" not in result.text


def test_hoist_is_idempotent():
    source = """
        import os


        def f():
            from canair.a import x

            return os, x
    """
    first = _hoist(source)
    assert first.hoisted
    again = hoist.hoist_text(
        "canair.m",
        scan.graph.REPO_ROOT / "src" / "canair" / "m.py",
        first.text,
        set(),
        KNOWN | {"canair.m"},
    )
    assert again.text == first.text


@pytest.mark.parametrize(
    ("source", "why"),
    [
        (
            "x = 1\n\ndef f():\n    from canair.a import x\n    return x\n\ndef g():\n    pass\n",
            "module level",
        ),
        (
            "def f():\n    from canair.a import x\n    return x\n"
            "\ndef g():\n    from canair.b import x\n    return x\n",
            "two different",
        ),
        (
            "def f():\n    from canair.a import open\n    return open\n\ndef g():\n    pass\n",
            "builtin",
        ),
        (
            "def f():\n    try:\n        from canair.a import x\n    except ImportError:\n"
            "        x = 1\n    return x\n",
            "try",
        ),
        ("def f():\n    from canair.a import x\n", "only statement"),
        ("def f():\n    from canair.a import *\n    return 1\n\ndef g():\n    pass\n", "star"),
    ],
)
def test_hoist_refuses_what_would_change_behaviour(source, why):
    result = _hoist(source)
    assert not result.hoisted
    assert result.text == source
    assert why in " ".join(reason for _imp, reason in result.skipped)


def test_hoist_refuses_a_name_bound_under_type_checking():
    result = _hoist(
        """
        from typing import TYPE_CHECKING

        if TYPE_CHECKING:
            from canair.a import x


        def f():
            from canair.a import x

            return x
        """
    )
    assert not result.hoisted
    assert "TYPE_CHECKING" in result.skipped[0][1]


def test_hoist_drops_an_import_the_module_already_has():
    result = _hoist(
        """
        from canair.a import x


        def f():
            from canair.a import x

            return x
        """
    )
    assert [i.line for i in result.dropped] == [6]
    assert result.text.count("import x") == 1


def test_hoist_keeps_relative_imports_relative():
    result = _hoist(
        """
        def f():
            from .pkg import sub

            return sub

        def g():
            pass
        """
    )
    assert "from .pkg import sub" in result.text.split("def f")[0]
    assert "    from" not in result.text


def _tree(tmp_path: Path, files: dict[str, str]) -> scan.Tree:
    root = tmp_path / "src" / "canair"
    for name, source in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dedent(source), encoding="utf8")
    return scan.scan_tree(root)


def test_classify_tells_loaded_heavy_cycle_and_other(tmp_path):
    tree = _tree(
        tmp_path,
        {
            "__init__.py": "",
            "a.py": """
                def f():
                    from canair.b import X

                    return X
            """,
            "b.py": """
                from canair.a import f

                X = f
            """,
            "c.py": """
                def g():
                    from canair.d import D
                    from canair.screen import S
                    from canair.later import L

                    return D, S, L
            """,
            "d.py": "D = 1\n",
            "screen.py": "import textual\n\nS = 1\n",
            "later.py": "L = 1\n",
        },
    )
    loaded = {"canair", "canair.a", "canair.b", "canair.c", "canair.d"}
    verdicts = classify.classify(tree, loaded, jobs=2)
    kinds = {i.source: v.kind for i, v in verdicts.items()}
    assert kinds == {
        "canair.b": "cycle",
        "canair.d": "loaded",
        "canair.screen": "heavy",
        "canair.later": "other",
    }
    assert "partially initialized" in next(
        v.why for i, v in verdicts.items() if i.source == "canair.b"
    )


def test_a_cycle_that_still_imports_is_a_cycle(tmp_path):
    tree = _tree(
        tmp_path,
        {
            "__init__.py": "",
            "a.py": """
                X = 1


                def f():
                    from canair.b import g

                    return g
            """,
            "b.py": """
                def g():
                    return 1


                import canair.a  # noqa: E402
            """,
        },
    )
    verdicts = classify.classify(tree, {"canair", "canair.a", "canair.b"}, jobs=2)
    (verdict,) = verdicts.values()
    assert (verdict.kind, verdict.why) == ("cycle", classify.JOINS_CYCLE)


def test_two_hoists_that_close_a_cycle_together_are_both_cycles(tmp_path):
    tree = _tree(
        tmp_path,
        {
            "__init__.py": "",
            "a.py": """
                def f():
                    from canair.b import g

                    return g
            """,
            "b.py": """
                def g():
                    from canair.a import f

                    return f
            """,
            "c.py": """
                def h():
                    from canair.a import f

                    return f
            """,
        },
    )
    loaded = {"canair", "canair.a", "canair.b", "canair.c"}
    verdicts = classify.classify(tree, loaded, jobs=2)
    assert {(i.module, v.kind) for i, v in verdicts.items()} == {
        ("canair.a", "cycle"),
        ("canair.b", "cycle"),
        ("canair.c", "loaded"),
    }


def test_a_package_may_import_its_own_submodule(tmp_path):
    tree = _tree(
        tmp_path,
        {
            "__init__.py": "",
            "pkg/__init__.py": """
                def run():
                    from canair.pkg import sub

                    return sub
            """,
            "pkg/sub.py": "X = 1\n",
        },
    )
    verdicts = classify.classify(tree, {"canair", "canair.pkg", "canair.pkg.sub"}, jobs=2)
    assert [v.kind for v in verdicts.values()] == ["loaded"]


def test_a_table_counts_each_kind_per_package():
    imp = scan.FunctionImport(
        module="canair.cli.x",
        path=Path("x.py"),
        line=1,
        end_line=1,
        source="canair.a",
        names=(("n", None),),
        level=0,
        rel_module="canair.a",
        is_from=True,
        targets=("canair.a",),
        in_try=False,
        has_reason=False,
        trailing_comment="",
    )
    rows = classify.table({imp: classify.Verdict("loaded")}, depth=1)
    assert rows == {"cli": {"loaded": 1, "total": 1, "no_reason": 1}}
    assert classify.matches("canair.cli.commands.scan", "cli.commands")
    assert not classify.matches("canair.cli_x", "cli")
