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
    "monitor": UDS,
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
    """Prefix each tagged subcommand's own ``--help`` description with its tag.

    The tag shows at the top of ``canair <cmd> --help`` (the command's
    description). It is deliberately *not* added to the top-level command map
    (the one-line help in the overview) — that list is already grouped by
    category, and each command's own help states its domain, so repeating the
    tag there is redundant noise. Idempotent — skips text already prefixed.
    """
    for name, parser in subparsers.choices.items():
        tag = tag_for(name)
        desc = parser.description
        if tag and desc and not desc.lstrip().startswith("["):
            parser.description = f"{tag} {desc}"
