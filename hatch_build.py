"""Hatchling build hook: stamp every wheel with where and when it was built.

The stamp (``canair/_build_info.json``) records the commit, branch, release tag,
dirty flag, build time and builder (a CI run, or a local build), so an installed
``canair --version`` can say which code it is. The format is owned by
``src/canair/install/build_info.py``, which this hook loads by path: the build
runs in an isolated environment where canair itself is not installed.

An editable install (``uv sync``) gets no stamp: it runs from the checkout, and
canair reads the checkout's git directly.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

_BUILD_INFO = Path("src", "canair", "install", "build_info.py")


def _load_build_info(root: Path):
    name = "_canair_build_info"
    spec = importlib.util.spec_from_file_location(name, root / _BUILD_INFO)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {_BUILD_INFO}")
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolve string annotations through sys.modules.
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        del sys.modules[name]
    return module


class BuildStampHook(BuildHookInterface):
    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict) -> None:
        if version == "editable":
            return
        root = Path(self.root)
        build_info = _load_build_info(root)
        stamp = build_info.collect_stamp(root, os.environ)
        self._tmp = tempfile.TemporaryDirectory()
        path = Path(self._tmp.name) / build_info.STAMP_FILE
        path.write_text(json.dumps(stamp, indent=2) + "\n", encoding="utf-8")
        build_data["force_include"][str(path)] = f"canair/{build_info.STAMP_FILE}"

    def finalize(self, version: str, build_data: dict, artifact_path: str) -> None:
        tmp = getattr(self, "_tmp", None)
        if tmp is not None:
            tmp.cleanup()
