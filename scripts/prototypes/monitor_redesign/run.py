"""Launch a monitor-redesign mockup, or render screenshots of all of them.

    uv run python scripts/prototypes/monitor_redesign/run.py desktop
    uv run python scripts/prototypes/monitor_redesign/run.py palette
    uv run python scripts/prototypes/monitor_redesign/run.py workbench
    uv run python scripts/prototypes/monitor_redesign/run.py combined
    uv run python scripts/prototypes/monitor_redesign/run.py --shots   # → shots/*.png
    uv run python scripts/prototypes/monitor_redesign/run.py --shots --only d-   # one prefix

The mockups never open a socket: the data is the bundled Ioniq profile and recorded payloads.
``ctrl+t`` switches between the terminal palette (``ansi-dark``) and Textual's own theme.
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from combined import CombinedApp  # noqa: E402
from desktop import DesktopApp  # noqa: E402
from palette import PaletteApp  # noqa: E402
from workbench import WorkbenchApp  # noqa: E402

APPS = {
    "desktop": DesktopApp,
    "palette": PaletteApp,
    "workbench": WorkbenchApp,
    "combined": CombinedApp,
}

WIDE = (120, 34)
#: An iPhone 16 Pro in portrait with the keyboard up (see the plan, "The phone target").
NARROW = (45, 22)

#: (file stem, app, size, keys to press before the shot). ``@trouble`` puts the session in a
#: bad way: frames lost, an output left on, polling paused. ``@clear`` drops the message.
SHOTS = [
    ("a-desktop-wide", "desktop", WIDE, ["down", "down"]),
    ("a-desktop-menu", "desktop", WIDE, ["down", "f10", "right"]),
    ("a-desktop-trouble", "desktop", WIDE, ["@trouble"]),
    ("a-desktop-narrow", "desktop", NARROW, ["down"]),
    ("a-desktop-narrow-trouble", "desktop", NARROW, ["@trouble"]),
    ("b-palette-wide", "palette", WIDE, ["down", "down"]),
    ("b-palette-commands", "palette", WIDE, ["ctrl+p", *"rec"]),
    ("b-palette-trouble", "palette", WIDE, ["@trouble"]),
    ("b-palette-narrow", "palette", NARROW, ["down"]),
    ("b-palette-narrow-trouble", "palette", NARROW, ["@trouble"]),
    ("c-workbench-wide", "workbench", WIDE, ["down", "down"]),
    ("c-workbench-menu", "workbench", WIDE, ["down", "f9"]),
    ("c-workbench-trouble", "workbench", WIDE, ["@trouble"]),
    ("c-workbench-narrow", "workbench", NARROW, ["down"]),
    ("c-workbench-narrow-trouble", "workbench", NARROW, ["@trouble"]),
    ("d-combined-wide", "combined", WIDE, ["down", "down"]),
    ("d-combined-menu", "combined", WIDE, ["down", "f10", "right", "right"]),
    ("d-combined-commands", "combined", WIDE, ["ctrl+p", *"rec"]),
    ("d-combined-modes", "combined", WIDE, ["V", "V", "V", "F", "r", "@clear"]),
    ("d-combined-trouble", "combined", WIDE, ["@trouble"]),
    ("d-combined-narrow", "combined", NARROW, ["down"]),
    ("d-combined-narrow-trouble", "combined", NARROW, ["@trouble"]),
    ("d-combined-narrow-menu", "combined", NARROW, ["down", "f10", "right"]),
]


def trouble(app) -> None:
    app.action_trouble()


def for_rsvg(svg: str) -> str:
    """Make Textual's SVG render in ``rsvg-convert``: it drops leading no-break spaces and
    cannot fetch the web font, which shifts every span after a space."""
    svg = svg.replace("&#160;", " ").replace(
        "font-family: Fira Code, monospace", "font-family: Menlo"
    )
    return svg.replace(
        '<svg class="rich-terminal"', '<svg xml:space="preserve" class="rich-terminal"'
    )


async def shoot(out: Path, only: str = "") -> None:
    out.mkdir(exist_ok=True)
    for stem, name, size, keys in SHOTS:
        if not stem.startswith(only):
            continue
        app = APPS[name]()
        async with app.run_test(size=size, notifications=True) as pilot:
            await pilot.pause(0.3)
            for key in keys:
                if key == "@trouble":
                    trouble(app)
                elif key == "@clear":
                    app.flash.clear()
                else:
                    await pilot.press(key)
                await pilot.pause(0.1)
            await pilot.pause(0.3)
            svg = out / f"{stem}.svg"
            svg.write_text(for_rsvg(app.export_screenshot(title=f"{name} {size[0]}×{size[1]}")))
        if shutil.which("rsvg-convert"):
            subprocess.run(
                ["rsvg-convert", "-o", str(svg.with_suffix(".png")), str(svg)], check=True
            )
            svg.unlink()
        print(f"→ {svg.with_suffix('.png') if not svg.exists() else svg}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("direction", nargs="?", choices=sorted(APPS))
    parser.add_argument("--shots", action="store_true", help="render every mockup to shots/")
    parser.add_argument("--only", default="", help="with --shots: only the shots with this prefix")
    args = parser.parse_args()
    if args.shots:
        asyncio.run(shoot(HERE / "shots", args.only))
    elif args.direction:
        APPS[args.direction]().run()
    else:
        parser.error("name a direction, or pass --shots")


if __name__ == "__main__":
    main()
