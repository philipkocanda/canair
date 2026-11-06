import sys

import pytest

from canair.core import lazy_imports


def test_only_imports_made_by_canair_are_lazy():
    allowed = lazy_imports._imported_by_canair
    assert allowed("canair", "canair.cli", None)
    assert allowed("canair.cli.commands.read", "rich.console", ("Console",))
    assert not allowed("canairx", "json", None)
    assert not allowed("_pytest.pathlib", "fnmatch", None)


def test_enable_says_whether_this_python_has_lazy_imports():
    assert lazy_imports.enable() is hasattr(sys, "set_lazy_imports")


@pytest.mark.skipif(not hasattr(sys, "set_lazy_imports"), reason="lazy imports need 3.15")
def test_enable_turns_on_the_canair_filter():
    lazy_imports.enable()
    assert sys.get_lazy_imports() == "all"  # ty: ignore[unresolved-attribute]
    assert sys.get_lazy_imports_filter() is lazy_imports._imported_by_canair  # ty: ignore[unresolved-attribute]
