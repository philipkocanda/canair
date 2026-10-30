"""MkDocs hook: render the bundled-profile stats when the docs site is built.

The repo ships one or more vehicle profiles under ``profiles/``. Their headline
counts (ECUs / PIDs / signals / verified / research leads) change with nearly
every profile commit, so they are derived from ``profiles/*/ecus/`` at build time
instead of being committed:

- ``profiles/index.md`` is a virtual page, generated whole (``on_files``).
- Each dedicated tour page (see ``DEDICATED_PAGES``) keeps its hand-written prose;
  only the block between the ``<!-- BEGIN/END generated-stats -->`` markers is
  replaced (``on_page_markdown``). In the source, that block holds a placeholder
  row for readers browsing the Markdown on GitHub.

Committing the rendered numbers instead made every profile commit also
regenerate the docs; forgetting to was the cause of nearly every red CI run, and
a ``canair contribute`` pull request could not regenerate them at all.

Wired up by ``hooks:`` in ``mkdocs.yml``; ``mkdocs build``/``serve`` run it.
"""

from __future__ import annotations

import re
from pathlib import Path

from mkdocs.exceptions import PluginError
from mkdocs.structure.files import File, Files

REPO_ROOT = Path(__file__).resolve().parents[2]
PROFILES_DIR = REPO_ROOT / "profiles"
INDEX_URI = "profiles/index.md"
GITHUB_TREE = "https://github.com/philipkocanda/canair/tree/main/profiles"

# Managed-block markers on a dedicated profile page. Everything between them is
# rendered at build time; the surrounding prose is hand-written.
STATS_BEGIN = "<!-- BEGIN generated-stats -->"
STATS_END = "<!-- END generated-stats -->"
_STATS_BLOCK = re.compile(re.escape(STATS_BEGIN) + r".*?" + re.escape(STATS_END), re.DOTALL)

# Profiles that have their own dedicated tour page, keyed to its docs-relative URI.
DEDICATED_PAGES = {
    "ioniq-2017": "profiles/ioniq-2017.md",
    "nissan-leaf-ze1": "profiles/nissan-leaf-ze1.md",
}


def profile_stats(ecus_dir: Path) -> dict[str, int]:
    """Headline counts for one profile, loaded straight from its ecus/ files."""
    from canlib.pids import load_pids, pid_status

    data = load_pids(ecus_dir) if ecus_dir.is_dir() else {"ecus": {}}
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


def _car_model(root: Path) -> str:
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
    from canlib.explorer.export import profile_maturity

    return profile_maturity(counts)


_MATURITY_SENTENCE = {
    "mature": "A mature profile",
    "seed / stub": "A seed / stub profile",
    "early": "An early profile",
}


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _page_link(page_uri: str) -> str:
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


def render_stats_block(counts: dict[str, int]) -> str:
    """The managed "At a glance" rows for a dedicated profile page.

    Only the derived counts live here; the vehicle description row above them is
    hand-written prose outside the markers.

    The BEGIN marker is glued to the end of the *preceding* (hand-written) table
    row and the END marker to the end of the *last* generated row, rather than
    sitting on its own line — Markdown's ``tables`` extension only recognizes a
    contiguous run of ``|``-prefixed lines as one table, so a bare HTML comment
    line in between silently splits it into a real table plus a dangling
    paragraph of literal pipe characters. A comment glued to the end of an
    existing row is simply dropped by the table renderer instead.
    """
    unverified = counts["params"] - counts["verified"]
    rows = [
        f"| ECUs mapped | **{counts['ecus']}** (all registered with identity) |",
        f"| PIDs | **{counts['pids']}** active/draft |",
        f"| Signals | **{counts['params']}** decoded "
        f"(**{counts['verified']} verified**, {unverified} unverified/candidate) |",
        f"| IOControl DIDs | **{counts['iocontrol']}** actuators (UDS `0x2F` / KWP2000 `0x30`) |",
        f"| Research backlog | **{counts['research']}** open reverse-engineering leads |",
    ]
    body = "\n".join([*rows[:-1], f"{rows[-1]} {STATS_END}"])
    return f"{STATS_BEGIN}\n{body}"


def fill_stats_block(markdown: str, counts: dict[str, int], page_uri: str) -> str:
    """Return ``markdown`` with its managed stats block rendered from ``counts``."""
    if not _STATS_BLOCK.search(markdown):
        raise PluginError(
            f"{page_uri} has no {STATS_BEGIN} / {STATS_END} block — add one around its "
            '"At a glance" count rows.'
        )
    return _STATS_BLOCK.sub(lambda _m: render_stats_block(counts), markdown)


def render_index(profiles: list[tuple[str, Path]]) -> str:
    """The whole bundled-profiles index page."""
    lines = [
        "# Bundled profiles",
        "",
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

    rows = [(name, root, profile_stats(root / "ecus"), _car_model(root)) for name, root in profiles]
    for name, _root, counts, model in rows:
        page = DEDICATED_PAGES.get(name)
        name_cell = f"[{name}]({_page_link(page)})" if page else f"`{name}`"
        source = f"[`profiles/{name}/`]({GITHUB_TREE}/{name})"
        lines.append(
            f"| {name_cell} | {model} | {counts['ecus']} | {counts['pids']} | "
            f"{counts['params']} | {counts['verified']} | {_maturity(counts)} | {source} |"
        )

    lines += ["", "## The profiles", ""]
    for name, _root, counts, model in rows:
        page = DEDICATED_PAGES.get(name)
        source = f"[`profiles/{name}/`]({GITHUB_TREE}/{name})"
        if page and _maturity(counts) == "mature":
            lines.append(
                f"- **[{name}]({_page_link(page)})** — {model}. "
                f"A mature, worked reference profile ({counts['verified']} verified "
                f"signals across {counts['ecus']} ECUs). See the full tour, or "
                f"browse {source}."
            )
        elif page:
            lines.append(
                f"- **[{name}]({_page_link(page)})** — {model}. "
                f"{profile_summary(counts, source)} See its page for what it decodes."
            )
        else:
            lines.append(f"- **`{name}`** — {model}. {profile_summary(counts, source)}")
    lines.append("")

    return "\n".join(lines)


def on_files(files: Files, config) -> Files:
    if files.get_file_from_path(INDEX_URI) is not None:
        raise PluginError(
            f"docs/{INDEX_URI} is generated at build time by {Path(__file__).name}; "
            "delete the committed copy."
        )
    files.append(File.generated(config, INDEX_URI, content=render_index(discover_bundled())))
    return files


def on_page_markdown(markdown: str, page, config, files) -> str:
    uri = page.file.src_uri
    for name, page_uri in DEDICATED_PAGES.items():
        if uri == page_uri:
            return fill_stats_block(markdown, profile_stats(PROFILES_DIR / name / "ecus"), uri)
    return markdown
