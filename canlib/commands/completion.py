"""``canair completion`` — print a shell snippet to enable tab-completion."""

from __future__ import annotations

import argparse
import sys

NAME = "completion"

_INSTALL_HINT = """\
# To enable canair tab-completion, add this to your shell startup file:
#
#   bash  (~/.bashrc):   eval "$(canair completion bash)"
#   zsh   (~/.zshrc):    eval "$(canair completion zsh)"
#   fish  (~/.config/fish/config.fish):
#                        canair completion fish | source
#
# Then restart your shell (or `source` the file). Completes subcommands,
# flags, and ECU/PID names from the active vehicle profile.
"""


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Print a shell snippet that enables tab-completion for canair",
        description="Print shell code to enable `canair` tab-completion "
        '(subcommands, flags, and ECU/PID names). Use with e.g. eval "$(canair completion zsh)".',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_INSTALL_HINT,
    )
    parser.add_argument(
        "shell",
        nargs="?",
        choices=["bash", "zsh", "fish", "tcsh", "powershell"],
        help="Target shell (default: auto-detected from $SHELL, else bash)",
    )
    parser.set_defaults(func=run)
    return parser


def _detect_shell() -> str:
    import os

    shell = os.environ.get("SHELL", "")
    for name in ("zsh", "bash", "fish", "tcsh"):
        if shell.endswith(name):
            return name
    return "bash"


def run(args) -> int:
    try:
        import argcomplete
    except ImportError:
        print("error: argcomplete is not installed.", file=sys.stderr)
        return 1

    shell = args.shell or _detect_shell()
    try:
        code = argcomplete.shellcode(["canair"], shell=shell)
    except Exception as e:  # older argcomplete without multi-shell support
        print(f"error: could not generate completion for {shell!r}: {e}", file=sys.stderr)
        print('Fallback: eval "$(register-python-argcomplete canair)"', file=sys.stderr)
        return 1
    sys.stdout.write(code)
    if not code.endswith("\n"):
        sys.stdout.write("\n")
    return 0
