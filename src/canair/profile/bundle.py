"""The bundle, declared once: every member of a profile directory and its role."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# ── the bundle, declared once ─────────────────────────────────────────────
# Every member of a profile directory is described here, and everything that reasons about
# "what is a profile made of" reads this table (Profile's path properties, `profile show`,
# `contribute`, the blind-test strip, discovery), so a new member is one edit.
#
# `role` decides how a contribution treats the member:
#   definition — curated: always contributed, and normally only *grows*.
#   evidence   — recorded measurements: contributed opt-in (`--captures`), append-only.
#   external   — third-party material of uncertain licence: never contributed.
#   generated  — reproducible output: never contributed, regenerate instead.
# Machine-local bookkeeping about *your* car (the DTC log, detected revisions) is no member:
# it lives in user state under the config dir, where a reinstall cannot delete it.
MemberRole = Literal["definition", "evidence", "external", "generated"]


MemberKind = Literal["file", "dir"]


@dataclass(frozen=True)
class BundleMember:
    """One member of a profile bundle (a file or directory under its root)."""

    name: str
    kind: MemberKind
    role: MemberRole
    # Profile property exposing this member's path (None for a legacy alias).
    attr: str | None = None
    # Column label in `canair profile show` (defaults to the name sans suffix).
    label: str = ""
    # A profile is recognisable by its required members (see looks_like_profile).
    required: bool = False
    # Withheld from the blind-rediscovery sandbox: it would leak the answers.
    blind_strip: bool = False
    # Superseded names still honoured when present (e.g. states.yaml).
    aliases: tuple[str, ...] = ()

    @property
    def contributable(self) -> bool:
        """Whether `canair contribute` ships this member (evidence: opt-in)."""
        return self.role in ("definition", "evidence")

    @property
    def display_label(self) -> str:
        return self.label or self.name.removesuffix(".yaml")


BUNDLE_MEMBERS: tuple[BundleMember, ...] = (
    BundleMember(
        "profile.yaml", "file", "definition", attr="meta_file", label="profile", required=True
    ),
    BundleMember("ecus", "dir", "definition", attr="ecus_dir", required=True),
    BundleMember(
        "vehicle_states.yaml",
        "file",
        "definition",
        attr="states_file",
        label="states",
        aliases=("states.yaml",),
    ),
    BundleMember("can_buses.yaml", "file", "definition", attr="can_buses_file"),
    BundleMember("groups.yaml", "file", "definition", attr="groups_file"),
    # An action's condition names decoded signals, so the blind strip withholds it
    # along with them.
    BundleMember("actions.yaml", "file", "definition", attr="actions_file", blind_strip=True),
    BundleMember("variants.yaml", "file", "definition", attr="variants_file"),
    # A signal map names and scales broadcast fields — the answers a blind run
    # is meant to rediscover — so it ships upstream but never into the sandbox.
    BundleMember("signals", "dir", "definition", attr="signals_dir", blind_strip=True),
    BundleMember("captures", "dir", "evidence", attr="captures_dir"),
    BundleMember("references", "dir", "external", attr="references_dir", blind_strip=True),
    BundleMember("out", "dir", "generated", attr="out_dir", blind_strip=True),
)


def members_by_role(*roles: MemberRole) -> tuple[BundleMember, ...]:
    """Bundle members with any of ``roles``, in declaration order."""
    return tuple(m for m in BUNDLE_MEMBERS if m.role in roles)


def member_names(members: tuple[BundleMember, ...]) -> tuple[str, ...]:
    """Flatten members to on-disk names, each followed by its legacy aliases."""
    return tuple(n for m in members for n in (m.name, *m.aliases))
