"""``canair completion`` — enable tab-completion for canair.

``canair completion --install`` auto-detects your shell and writes the
completion script into the shell's autoload directory (no manual rc editing
for fish/bash, and zsh when a writable ``fpath`` directory exists). Without
``--install`` it prints the shell snippet for use with ``eval``.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

NAME = "completion"

_SHELLS = ["bash", "zsh", "fish", "tcsh", "powershell"]

_EPILOG = """\
examples:
  canair completion --install          # auto-detect shell, install, done
  canair completion --install zsh      # install for a specific shell
  eval "$(canair completion zsh)"      # or wire it up manually (add to ~/.zshrc)

After --install, open a new shell (fish/bash autoload it; zsh loads it from a
directory on $fpath). Completes subcommands, flags, and ECU/PID names from the
active vehicle profile.
"""


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Enable tab-completion for canair (use --install for one-shot setup)",
        description="Enable `canair` tab-completion (subcommands, flags, ECU/PID names).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_EPILOG,
    )
    parser.add_argument(
        "shell",
        nargs="?",
        choices=_SHELLS,
        help="Target shell (default: auto-detected from $SHELL, else bash)",
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help="Write the completion script into the shell's autoload directory",
    )
    parser.set_defaults(func=run)
    return parser


def _detect_shell() -> str:
    shell = os.environ.get("SHELL", "")
    for name in ("zsh", "bash", "fish", "tcsh"):
        if shell.endswith(name):
            return name
    return "bash"


def _shellcode(shell: str) -> str:
    import argcomplete

    code = argcomplete.shellcode(["canair"], shell=shell)
    return code if code.endswith("\n") else code + "\n"


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


# ── zsh helpers ───────────────────────────────────────────────────────────


def _zsh_fpath_dirs() -> list[Path]:
    """Return the user's real $fpath (interactive shell), best-effort."""
    try:
        out = subprocess.run(
            ["zsh", "-ic", "print -rl -- $fpath"],
            capture_output=True,
            text=True,
            timeout=8,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return [Path(line) for line in out.stdout.splitlines() if line.strip()]


def _writable_fpath_dir() -> Path | None:
    home = Path.home()
    dirs = _zsh_fpath_dirs()
    # Prefer directories under $HOME, then any other writable existing dir.
    dirs.sort(key=lambda d: 0 if (d == home or home in d.parents) else 1)
    for d in dirs:
        if d.is_dir() and os.access(d, os.W_OK):
            return d
    return None


_ZSHRC_MARKER = "# canair completion"


def _ensure_zshrc_fpath(comp_dir: Path) -> bool:
    """Idempotently add ``comp_dir`` to $fpath in ~/.zshrc. Returns True if added."""
    zshrc = Path.home() / ".zshrc"
    existing = zshrc.read_text() if zshrc.exists() else ""
    if _ZSHRC_MARKER in existing:
        return False
    block = (
        f"\n{_ZSHRC_MARKER}\n"
        f"fpath=({comp_dir} $fpath)\n"
        "autoload -Uz compinit && compinit\n"
    )
    with open(zshrc, "a") as f:
        f.write(block)
    return True


def _install_zsh() -> tuple[Path, str]:
    content = _shellcode("zsh")
    target_dir = _writable_fpath_dir()
    if target_dir is not None:
        target = target_dir / "_canair"
        _write(target, content)
        return target, "Open a new shell to load it (zsh autoloads from $fpath)."
    # No writable fpath dir — use ~/.zsh/completions and wire it into ~/.zshrc.
    target_dir = Path.home() / ".zsh" / "completions"
    target = target_dir / "_canair"
    _write(target, content)
    added = _ensure_zshrc_fpath(target_dir)
    if added:
        note = "Added an fpath line to ~/.zshrc — open a new shell to load it."
    else:
        note = "Open a new shell to load it (ensure ~/.zsh/completions is on $fpath)."
    return target, note


def _install(shell: str) -> int:
    if shell == "fish":
        target = Path.home() / ".config" / "fish" / "completions" / "canair.fish"
        _write(target, _shellcode("fish"))
        note = "fish autoloads it — open a new shell."
    elif shell == "bash":
        xdg = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        target = Path(xdg) / "bash-completion" / "completions" / "canair"
        _write(target, _shellcode("bash"))
        note = "bash-completion (>=2.8) autoloads it — open a new shell."
    elif shell == "zsh":
        target, note = _install_zsh()
    else:
        print(
            f"error: --install is not supported for {shell!r}. "
            f'Add this to your shell config instead:\n  eval "$(canair completion {shell})"',
            file=sys.stderr,
        )
        return 1

    print(f"Installed canair {shell} completion → {target}")
    print(note)
    return 0


def run(args) -> int:
    try:
        import argcomplete  # noqa: F401
    except ImportError:
        print("error: argcomplete is not installed.", file=sys.stderr)
        return 1

    shell = args.shell or _detect_shell()

    try:
        if args.install:
            return _install(shell)
        sys.stdout.write(_shellcode(shell))
        return 0
    except Exception as e:
        print(f"error: could not generate completion for {shell!r}: {e}", file=sys.stderr)
        print('Fallback: eval "$(register-python-argcomplete canair)"', file=sys.stderr)
        return 1
