"""The ``canair contribute`` parser."""

from __future__ import annotations

import argparse

from .pipeline import run

NAME = "contribute"

_DESCRIPTION = """\
Open a pull request contributing the active profile upstream.

A one-command path to share a reverse-engineered profile (its definitions, and by
default its captures) without the manual fork/clone/branch/push dance — it works
wherever the profile is stored. Before anything leaves your machine it validates
the profile, scans it for anything that could identify or locate you, warns if the
source looks stale, and asks you to confirm.
"""


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        aliases=["share"],
        help="Open a pull request contributing the active profile upstream",
        description=_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  canair contribute                     # PR the active profile (definitions + captures)
  canair --profile ev6 contribute       # PR a specific profile
  canair contribute --no-captures       # contribute definitions only
  canair contribute --diff              # show what would be contributed, then stop
  canair contribute --dry-run           # prepare the branch + commit locally; no push/PR
  canair contribute --no-logs           # leave the scrubbed diagnostics log out of the PR
  canair contribute --yes --json        # non-interactive (agents/CI); emit the PR URL

requires the GitHub CLI:
  install:  brew install gh   (macOS)  ·  winget install GitHub.cli   (Windows)
            https://github.com/cli/cli#installation
  sign in:  gh auth login
""",
    )
    caps = parser.add_mutually_exclusive_group()
    caps.add_argument(
        "--captures",
        dest="captures",
        action="store_true",
        default=True,
        help="Include the captures/ evidence (default)",
    )
    caps.add_argument(
        "--no-captures",
        dest="captures",
        action="store_false",
        help="Contribute definitions only (ecus/, profile.yaml, states, buses, groups, signals)",
    )
    parser.add_argument(
        "--branch", help="Branch name to push (default: contribute/<profile>-<date>)"
    )
    parser.add_argument("--title", help="Pull request title (default: generated)")
    parser.add_argument(
        "--body",
        help="Pull request body (default: generated summary of the car, ECUs, states, "
        "sessions and link quality, plus a checklist)",
    )
    logs = parser.add_mutually_exclusive_group()
    logs.add_argument(
        "--include-logs",
        dest="include_logs",
        action="store_true",
        default=True,
        help="Attach the diagnostics log (canair logs) entries written while the contributed "
        "sessions were recording to the PR body, for debugging; device addresses, local "
        "paths, emails and VINs are scrubbed (default)",
    )
    logs.add_argument(
        "--no-logs",
        dest="include_logs",
        action="store_false",
        help="Leave the diagnostics log out of the PR body",
    )
    parser.add_argument(
        "--repo-dir",
        help="Use this existing canair checkout (with a fork as 'origin') instead of "
        "the managed fork clone",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Prepare the branch and commit locally, but do not push or open a PR",
    )
    parser.add_argument(
        "--diff",
        action="store_true",
        help="Show the diff this contribution would submit, then stop (no commit/push/PR)",
    )
    parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompts")
    local = parser.add_mutually_exclusive_group()
    local.add_argument(
        "--discard-local",
        dest="local_changes",
        action="store_const",
        const="discard",
        help="Once the PR is open, drop the changes it carries from your canair checkout "
        "(backed up first), so pulling the merged PR does not conflict with them. Without "
        "this or --keep-local you are asked; with --yes or --json they are kept",
    )
    local.add_argument(
        "--keep-local",
        dest="local_changes",
        action="store_const",
        const="keep",
        help="Leave your checkout's uncommitted profile changes alone without asking",
    )
    parser.add_argument("--json", action="store_true", help="Emit a machine-readable summary")
    parser.set_defaults(func=run)
    return parser
