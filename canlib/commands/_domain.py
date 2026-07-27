"""Domain tags for command help — is a command UDS, CAN, or both?

canair spans two data domains (see the contributing skill's "Two data domains"):
diagnostic request/response **UDS**/KWP2000, and passively-broadcast raw **CAN**
frames. This module centralizes, in one place, which domain each subcommand
operates on so every command's ``--help`` can advertise it consistently near the
top (applied uniformly in ``canlib.cli.build_parser``) rather than hand-writing a
tag into 30 command modules.

Pure-meta commands (config/profile/update/completion) are intentionally absent —
they belong to no data domain, so tagging them would mislead.
"""

from __future__ import annotations

# Command name -> domain label. Keys are the subcommand strings (module.NAME),
# not the module filenames (e.g. "import", not "import_").
UDS = "UDS"
CAN = "CAN"
BOTH = "UDS+CAN"

DOMAINS: dict[str, str] = {
    # live device — diagnostic request/response
    "query": UDS,
    "scan": UDS,
    "discover": UDS,
    "raw": UDS,
    "io": UDS,
    "routines": UDS,
    "identity": UDS,
    "dtc": UDS,
    "repl": UDS,
    # live device — passive broadcast
    "sniff": CAN,
    # offline analysis
    "captures": BOTH,
    "decode": UDS,
    "correlate": BOTH,
    "hunt": BOTH,
    "investigate": BOTH,
    "coverage": UDS,
    "research": UDS,
    # authoring / maintenance
    "pids": UDS,
    "signals": CAN,
    "validate": BOTH,
    "wican": UDS,
    "ecu": UDS,
    # interop
    "import": BOTH,
    "export": CAN,
    # utilities
    "bix": UDS,
}


def tag_for(name: str) -> str | None:
    """Return the bracketed domain tag for a command, or None if untagged."""
    label = DOMAINS.get(name)
    return f"[{label}]" if label else None


def apply_domain_tags(subparsers) -> None:
    """Prefix each tagged subcommand's help + description with its domain tag.

    Operates on the built subparsers action so the tag shows both in the
    top-level command map (the one-line help) and at the top of the command's
    own ``--help`` (the description). Idempotent — skips text already prefixed.
    """
    # One-line help lives on the choices' pseudo-actions.
    for action in getattr(subparsers, "_choices_actions", []):
        tag = tag_for(action.dest)
        if tag and action.help and not action.help.startswith("["):
            action.help = f"{tag} {action.help}"
    # The description shows at the top of `canair <cmd> --help`.
    for name, parser in subparsers.choices.items():
        tag = tag_for(name)
        desc = parser.description
        if tag and desc and not desc.lstrip().startswith("["):
            parser.description = f"{tag} {desc}"
