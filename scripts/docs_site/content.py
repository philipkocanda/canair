"""Render the bundled-profile pages of the docs site from the profile data.

The repo ships one or more vehicle profiles under ``profiles/``. Their headline
counts (ECUs / PIDs / signals / verified / research leads) change with nearly
every profile commit, so they are derived from ``profiles/*/ecus/`` at build time
instead of being committed:

- ``profiles/index.md`` holds a title and one macro call; ``render_index`` renders the rest.
- Each dedicated tour page (see ``DEDICATED_PAGES``) keeps its hand-written prose. Its
  "At a glance" table ends with one macro call, which ``render_stats_rows`` answers with the
  count rows.
- The other names people call a car by (``also_known_as:`` in ``profile.yaml`` and
  ``variants.yaml``) are rendered on the index, in a dedicated page's count rows
  and in its meta description, so a search for "Classic Ioniq" finds the right page.

Committing the rendered numbers instead made every profile commit also
regenerate the docs; forgetting to was the cause of nearly every red CI run, and
a ``canair contribute`` pull request could not regenerate them at all.

Nothing here imports the site generator, so the engine can change without touching this code.
``macros.py`` is the glue that the site build runs.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from canair.profile.names import AlsoKnownAs

REPO_ROOT = Path(__file__).resolve().parents[2]
PROFILES_DIR = REPO_ROOT / "profiles"
INDEX_URI = "profiles/index.md"
GITHUB_TREE = "https://github.com/philipkocanda/canair/tree/main/profiles"


class DocsContentError(Exception):
    """A docs page or profile cannot be rendered; the build must stop."""


# Profiles that have their own dedicated tour page, keyed to its docs-relative URI.
DEDICATED_PAGES = {
    "hyundai-ioniq-ae-ev": "profiles/hyundai-ioniq-ae-ev.md",
    "hyundai-ioniq-5-ne-ev": "profiles/hyundai-ioniq-5-ne-ev.md",
    "nissan-leaf-ze1-ev": "profiles/nissan-leaf-ze1-ev.md",
}


def profile_stats(ecus_dir: Path) -> dict[str, int]:
    """Headline counts for one profile, loaded straight from its ecus/ files."""
    from canair.ecus.pid_status import pid_status
    from canair.ecus.pids import load_pids_dir

    data = load_pids_dir(ecus_dir) if ecus_dir.is_dir() else {"ecus": {}}
    ecus = data.get("ecus") or {}

    # Counts mirror `canair validate pids --stats` (the canonical headline): the
    # PID count is every defined PID (including `ignored` tombstones), while
    # signals/verified are only counted on non-ignored PIDs.
    counts = {"ecus": 0, "pids": 0, "params": 0, "verified": 0, "iocontrol": 0, "research": 0}
    for ecu_def in ecus.values():
        if not isinstance(ecu_def, dict):
            continue
        counts["ecus"] += 1
        pids = ecu_def.get("pids") or {}
        for pid_def in pids.values():
            if not isinstance(pid_def, dict):
                continue
            counts["pids"] += 1
            if pid_status(pid_def) == "ignored":
                continue
            for param in (pid_def.get("parameters") or {}).values():
                if not isinstance(param, dict):
                    continue
                counts["params"] += 1
                if param.get("verified"):
                    counts["verified"] += 1
        counts["iocontrol"] += len(ecu_def.get("iocontrol") or {})
        # Open research leads, matching `canair research`'s default view: every
        # item whose status isn't `done` (see commands/research/__init__.py::filter_records).
        for item in ecu_def.get("research") or []:
            if isinstance(item, dict) and item.get("status") != "done":
                counts["research"] += 1
    return counts


def also_known_as_for(root: Path) -> AlsoKnownAs:
    from canair.profile.names import also_known_as

    return also_known_as(root)


def car_model(root: Path) -> str:
    import yaml

    meta_path = root / "profile.yaml"
    if meta_path.exists():
        meta = yaml.safe_load(meta_path.read_text()) or {}
        model = meta.get("car_model")
        if model:
            return str(model)
    return "—"


def discover_bundled(profiles_dir: Path = PROFILES_DIR) -> list[tuple[str, Path]]:
    """(name, root) for each bundled profile, sorted by name."""
    if not profiles_dir.is_dir():
        return []
    profiles = []
    for child in sorted(profiles_dir.iterdir()):
        if child.is_dir() and ((child / "ecus").is_dir() or (child / "profile.yaml").exists()):
            profiles.append((child.name, child))
    return profiles


def _maturity(counts: dict[str, int]) -> str:
    """A one-word maturity hint from the counts."""
    from canair.interop.explorer.export import profile_maturity

    return profile_maturity(counts)


_MATURITY_SENTENCE = {
    "mature": "A mature profile",
    "seed / stub": "A seed / stub profile",
    "early": "An early profile",
}


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def page_link(page_uri: str) -> str:
    """A dedicated page's link target, relative to the index page."""
    return Path(page_uri).relative_to(Path(INDEX_URI).parent).as_posix()


def profile_summary(counts: dict[str, int], source: str) -> str:
    """One-sentence description of a profile without a dedicated tour page.

    The maturity word is used once, with the article it needs, and only the
    profiles that are not yet mature are called a starting point.
    """
    maturity = _maturity(counts)
    stats = f"({_plural(counts['params'], 'signal')}, {counts['verified']} verified)"
    lead = f"{_MATURITY_SENTENCE[maturity]} {stats}"
    if maturity != "mature":
        lead += " — a starting point that needs contributions"
    return f"{lead}. Browse {source}."


def _cell(text: str) -> str:
    return text.replace("|", "\\|")


def known_as_cell(names: AlsoKnownAs) -> str:
    """The "Also known as" table cell: the car's names, then one line per variant."""
    lines = [", ".join(names.profile)] if names.profile else []
    lines += [f"**{v.name}**: {', '.join(v.names)}" for v in names.variants]
    return _cell("<br>".join(lines))


def page_description(car_model: str, names: AlsoKnownAs) -> str:
    """The page's meta description, which carries the names to search engines."""
    known = names.all()
    suffix = f", also known as {', '.join(known)}" if known else ""
    return f"canair vehicle profile for the {car_model}{suffix}."


def render_stats_rows(counts: dict[str, int], names: AlsoKnownAs | None = None) -> str:
    """The "At a glance" rows of a dedicated profile page that come from the profile data.

    The derived counts and the car's other names live here. The vehicle description row above
    them is hand-written prose in the page.
    """
    unverified = counts["params"] - counts["verified"]
    rows = [f"| Also known as | {known_as_cell(names)} |"] if names and names.all() else []
    rows += [
        f"| ECUs mapped | **{counts['ecus']}** (all registered with identity) |",
        f"| PIDs | **{counts['pids']}** active/draft |",
        f"| Signals | **{counts['params']}** decoded "
        f"(**{counts['verified']} verified**, {unverified} unverified/candidate) |",
        f"| IOControl DIDs | **{counts['iocontrol']}** actuators (UDS `0x2F` / KWP2000 `0x30`) |",
        f"| Research backlog | **{counts['research']}** open reverse-engineering leads |",
    ]
    return "\n".join(rows)


def tour_page_rows(name: str) -> str:
    """The count rows of the tour page for the bundled profile ``name``."""
    root = _dedicated_root(name)
    return render_stats_rows(profile_stats(root / "ecus"), also_known_as_for(root))


def tour_page_description(name: str) -> str:
    """The meta description of the tour page for ``name``, which carries the car's other names."""
    root = _dedicated_root(name)
    return page_description(car_model(root), also_known_as_for(root))


def _dedicated_root(name: str) -> Path:
    if name not in DEDICATED_PAGES:
        raise DocsContentError(
            f"{name!r} has no tour page; the profiles with one are {', '.join(DEDICATED_PAGES)}."
        )
    return PROFILES_DIR / name


def render_index(profiles: list[tuple[str, Path]]) -> str:
    """The bundled-profiles index page, below its title."""
    lines = [
        "The repo ships these vehicle profiles under "
        f"[`profiles/`]({GITHUB_TREE}). Each is a self-contained "
        "[profile bundle](../concepts/profiles.md) you can select with "
        "`--profile NAME`. Build one for *your* car with the "
        "[bring-your-own-car](../bring-your-own-car/overview.md) journey — and "
        "please [contribute it back](../bring-your-own-car/08-share.md#contribute-your-profile-back).",
        "",
        "To see how a profile's ECUs sit on the CAN buses and to browse their PIDs, signals "
        "and IO controls, open the [profile explorer](explorer.md).",
        "",
        "> These counts are computed from each profile's `ecus/` files whenever "
        "this site is built, so they always match the data.",
        "",
        "| Profile | Vehicle | ECUs | PIDs | Signals | Verified | Maturity | Source |",
        "|---|---|---:|---:|---:|---:|---|---|",
    ]

    rows = [(name, root, profile_stats(root / "ecus"), car_model(root)) for name, root in profiles]
    for name, _root, counts, model in rows:
        page = DEDICATED_PAGES.get(name)
        name_cell = f"[{name}]({page_link(page)})" if page else f"`{name}`"
        source = f"[`profiles/{name}/`]({GITHUB_TREE}/{name})"
        lines.append(
            f"| {name_cell} | {model} | {counts['ecus']} | {counts['pids']} | "
            f"{counts['params']} | {counts['verified']} | {_maturity(counts)} | {source} |"
        )

    lines += ["", "## The profiles", ""]
    for name, root, counts, model in rows:
        page = DEDICATED_PAGES.get(name)
        known = also_known_as_for(root).all()
        model = f"{model}. Also known as {', '.join(known)}" if known else model
        source = f"[`profiles/{name}/`]({GITHUB_TREE}/{name})"
        if page and _maturity(counts) == "mature":
            lines.append(
                f"- **[{name}]({page_link(page)})** — {model}. "
                f"A mature, worked reference profile ({counts['verified']} verified "
                f"signals across {counts['ecus']} ECUs). See the full tour, or "
                f"browse {source}."
            )
        elif page:
            lines.append(
                f"- **[{name}]({page_link(page)})** — {model}. "
                f"{profile_summary(counts, source)} See its page for what it decodes."
            )
        else:
            lines.append(f"- **`{name}`** — {model}. {profile_summary(counts, source)}")
    lines.append("")

    return "\n".join(lines)
