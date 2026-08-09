"""Create a profile bundle — scaffold a new one, or adopt a read-only one.

Both verbs answer the same question: *where can this vehicle's data be written?*
A profile resolved from an install snapshot (``site-packages``) accepts writes
that the next reinstall destroys, and a repo-bundled profile belongs to the
checkout rather than to the person driving the car. Either way the fix is a
bundle under a writable root, which is what these two functions produce:

* :func:`create_profile` scaffolds an empty bundle from ``templates/`` — a car
  canair knows nothing about yet.
* :func:`adopt_profile` copies an existing (typically bundled) profile into
  ``~/.config/canair/profiles/``, where it *shadows* the read-only original by
  name and becomes writable.

Both are pure of argparse, so the CLI (``canair profile create``/``adopt``) and
the first-run wizard call the same code.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .config import set_config_value, user_profiles_dir
from .profile import BUNDLE_MEMBERS, looks_like_profile, profiles_roots

# Default ELM327 init string for a new profile: ISO 15765-4 CAN 11-bit/500 kbit
# (the common modern-vehicle protocol), spaces off, allow long messages. The
# response timeout (ATST) is deliberately NOT baked in here — it is vehicle-
# specific and set via the profile's `response_timeout_ms:` (the Ioniq needs a
# high value; faster cars want a lower one). Editable in profile.yaml afterwards.
DEFAULT_INIT = "ATSP6;ATS0;ATAL;"

# Generated output is reproducible (`canair wican autopid write`), so an adopted
# copy regenerates it rather than inheriting a stale one. Everything else in the
# bundle — definitions, captures, references, the local DTC log — is carried
# over, because the point of adopting is to keep working with that data.
_ADOPT_SKIP_ROLES = ("generated",)

# Never copied out of any bundle member: write-ahead journals belong to the
# session that opened them, and the rest is editor/interpreter litter.
_ADOPT_IGNORE = shutil.ignore_patterns(".journal", "*.tmp", "__pycache__", ".DS_Store")


# Scaffold templates live in the repo-root `templates/` dir (shipped in the wheel
# via pyproject force-include). Placeholders use `string.Template` ($var) syntax
# so literal braces in YAML/comments never need escaping. See templates/*.tmpl.
def render_template(filename: str, **subs: str) -> str:
    """Read ``templates/<filename>`` and substitute ``$placeholders``.

    ``safe_substitute`` tolerates each template using only the vars it needs.
    """
    from string import Template

    from .constants import TEMPLATES_DIR

    text = (TEMPLATES_DIR / filename).read_text()
    return Template(text).safe_substitute(subs)


def create_profile(
    name: str,
    *,
    car_model: str,
    init: str | None = None,
    path=None,
    set_default: bool = False,
    force: bool = False,
) -> Path:
    """Scaffold a new profile bundle. Returns its root; raises on error."""
    name = name.strip()
    if not name:
        raise ValueError("profile name cannot be empty")

    root = Path(path) if path else user_profiles_dir() / name
    if root.exists() and any(root.iterdir()) and not force:
        raise FileExistsError(f"{root} already exists and is not empty (use force to proceed).")

    car_model = car_model.strip()
    if not car_model:
        raise ValueError("car_model is required")

    init = init or DEFAULT_INIT

    (root / "ecus").mkdir(parents=True, exist_ok=True)
    (root / "captures").mkdir(parents=True, exist_ok=True)
    (root / "out").mkdir(parents=True, exist_ok=True)

    (root / "profile.yaml").write_text(
        render_template("profile.yaml.tmpl", car_model=car_model, init=init)
    )
    (root / "vehicle_states.yaml").write_text(
        render_template("vehicle_states.yaml.tmpl", car_model=car_model)
    )
    (root / "can_buses.yaml").write_text(
        render_template("can_buses.yaml.tmpl", car_model=car_model)
    )
    (root / "groups.yaml").write_text(render_template("groups.yaml.tmpl", car_model=car_model))

    if set_default:
        set_config_value("default_profile", name)
    return root


def adopt_profile(
    name: str,
    *,
    profiles_dir: str | Path | None = None,
    set_default: bool = False,
    force: bool = False,
) -> tuple[Path, Path]:
    """Copy the discovered profile ``name`` into the user profiles directory.

    Returns ``(source, destination)``. The copy takes the same name, and user
    profiles shadow bundled ones, so ``--profile <name>`` keeps working and now
    resolves somewhere writable that a reinstall cannot touch.

    Raises ``LookupError`` for an unknown name, ``ValueError`` when there is
    nothing to adopt (the profile only exists as the user copy) or when it
    resolves from a root that outranks the destination, and ``FileExistsError``
    when the destination holds data and ``force`` is not set.
    """
    name = name.strip()
    dest = user_profiles_dir() / name
    source = _adoption_source(name, profiles_dir, dest)
    if dest.exists() and any(dest.iterdir()) and not force:
        raise FileExistsError(f"{dest} already exists and is not empty")

    dest.mkdir(parents=True, exist_ok=True)
    for member in BUNDLE_MEMBERS:
        if member.role in _ADOPT_SKIP_ROLES:
            continue
        for candidate in (member.name, *member.aliases):
            src = source / candidate
            if not src.exists():
                continue
            if member.kind == "dir":
                shutil.copytree(src, dest / candidate, dirs_exist_ok=True, ignore=_ADOPT_IGNORE)
            else:
                shutil.copy2(src, dest / candidate)

    if set_default:
        set_config_value("default_profile", name)
    return source, dest


def overlay_profile(
    name: str,
    *,
    profiles_dir: str | Path | None = None,
    set_default: bool = False,
) -> tuple[Path, Path]:
    """Create a capture *layer* over the discovered profile ``name``.

    Returns ``(base, overlay)``. Unlike :func:`adopt_profile` this copies nothing:
    it writes a bundle holding only an ``extends:`` marker and an empty
    ``captures/``, so definitions keep resolving from the base (and keep tracking
    upstream) while everything you record lands in your own directory.

    Raises the same errors as :func:`adopt_profile`, plus ``FileExistsError`` when
    the destination already exists — refreshing a layer is meaningless, and
    silently reusing a directory that might be a full adopted copy would turn its
    definitions into dead weight.
    """
    name = name.strip()
    dest = user_profiles_dir() / name
    base = _adoption_source(name, profiles_dir, dest)
    if dest.exists():
        raise FileExistsError(f"{dest} already exists")

    (dest / "captures").mkdir(parents=True)
    (dest / "profile.yaml").write_text(
        f"# Capture layer over the '{name}' profile.\n"
        "#\n"
        f"# `extends:` makes this bundle a layer rather than a whole vehicle: {name}'s\n"
        "# definitions still resolve from the base bundle, and every capture you\n"
        "# record lands here instead. Remove the marker and this becomes an ordinary\n"
        "# (definition-shadowing) profile.\n"
        f"extends: {name}\n"
    )

    if set_default:
        set_config_value("default_profile", name)
    return base, dest


def _adoption_source(name: str, profiles_dir: str | Path | None, dest: Path) -> Path:
    """Find the bundle ``name`` should be adopted *from*.

    Deliberately not ``discover_profiles``: once a user copy exists it shadows
    the original, so the ordinary lookup would answer with the destination and
    make a ``force`` refresh impossible. Instead the ordered search roots are
    walked directly, skipping the user directory to find what it shadows.

    Roots *above* the user directory (``--profiles-dir``, ``CANAIR_PROFILES_DIR``,
    config ``profiles_dir``) keep winning after the copy, so a profile found in
    one of those is refused rather than copied somewhere that is never read.
    """
    user_dir = user_profiles_dir()
    outranking = True
    for root in profiles_roots(profiles_dir):
        if _same_dir(root, user_dir):
            outranking = False
            continue
        candidate = root / name
        if not looks_like_profile(candidate):
            continue
        if outranking:
            raise ValueError(
                f"'{name}' resolves from {root}, which outranks the user profiles "
                f"directory, so a copy at {dest} would never be read. Adopting cannot "
                "help here — make that root writable, or point canair at one that is "
                "(`canair config set profiles_dir <dir>`, or unset CANAIR_PROFILES_DIR)."
            )
        return candidate

    if looks_like_profile(dest):
        raise ValueError(f"'{name}' only exists at {dest} — it is already yours, nothing to adopt.")
    raise LookupError(name)


def _same_dir(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return a == b
