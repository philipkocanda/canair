"""What the stages of ``canair contribute`` operate on."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from canair.contribute.copy import CaptureReplacement
from canair.contribute.git import MODE_DIRECT, Preflight
from canair.core.paths import GITHUB_REPO

from .report import Reporter


@dataclass
class Contribution:
    """What the action stages operate on, once the gates have cleared the run.

    Constructed only after the environment and workspace gates have resolved
    ``pre``/``workspace``, so no stage has to defend against a half-built run.
    """

    args: argparse.Namespace
    rep: Reporter
    profile: Any
    branch: str
    include_captures: bool
    pre: Preflight
    workspace: Path
    mode: str = ""
    staged: bool = False  # the profile is (being) copied into the workspace
    rollback: list[tuple[str, int]] = field(default_factory=list)
    replaced: list[CaptureReplacement] = field(default_factory=list)
    findings: list[Any] = field(default_factory=list)
    summary: Any = None  # canair.contribute.summary.ContributionSummary

    @property
    def target(self) -> str:
        """Where the branch is pushed, for prose."""
        return GITHUB_REPO if self.mode == MODE_DIRECT else "your fork"
