# Documentation screenshots

The docs embed SVG screenshots and animated GIFs of the CLI in action. canair **generates** all of
them from the manifest at `docs/screenshots/shots.yaml`. You never craft or maintain them by hand.
[`freeze`](https://github.com/charmbracelet/freeze) renders static command output (SVG).
[`vhs`](https://github.com/charmbracelet/vhs) renders interactive text user interface (TUI) clips
and montage clips (GIF). Every asset is captured against the bundled, read-only
`hyundai-ioniq-ae-ev` profile with **no device attached**. It is therefore reproducible on any
machine and contains no owner-specific data.

```bash
brew install charmbracelet/tap/freeze vhs   # one-time: the render tools
make screenshots                             # regenerate everything
make screenshots-only ONLY="bus decode-plot" # regenerate a subset
make screenshots-check                        # verify assets present + commands still run
```

`--check` is deliberately light. Continuous integration (CI) and the pre-push hook run it. It
needs neither `freeze` nor `vhs`, and it never byte-compares images, because images are not
reproducible. It verifies that every manifest asset exists, flags orphans, and runs each
screenshotted command device-free. A renamed command or a dropped flag therefore fails the check,
and the check tells you to regenerate.

**When you change the output of a screenshotted command, re-render and commit the updated asset.**
To add a shot, append an entry to `shots.yaml` (a `rich` command, a `tui` still or an `anim` tape)
and regenerate.

## The monitor without a car

The monitor needs a bus, and the docs must not. `scripts/gen/monitor_demo.py` runs the production
monitor (controller, poll loop, decoders, renderer, Textual app) with a *replay terminal* in place
of the dongle. The terminal answers each request with the next payload that one capture session of
the bundled Ioniq profile recorded on the road, so the values move as they did. It runs on a
throwaway copy of the profile, so a recording or an edit made in it never reaches the repo, and its
link zone says `replay`.

- A **`tui`** entry is a still: the script drives the app headless (`--size`, `--keys`, `--wait`)
  and the app exports the SVG itself, so no `freeze` is needed.
- The **`monitor-tour`** tape runs the script in `vhs`. `vhs` cannot press `f10` and types
  `alt+letter` as two keys, so the tour reaches commands through the palette. The menus are in the
  stills.
- `--check` runs `monitor_demo.py --check`: three headless poll cycles over the replay.

```bash
uv run python scripts/gen/monitor_demo.py            # the demo, in your terminal
python3 scripts/gen/gen_screenshots.py --only monitor monitor-menu monitor-tour
```

Do **not** screenshot views that show free-text capture notes or labels (for example
`captures --sessions`). Those views can leak personally identifiable information (PII) into public
docs.

A few `anim` assets are marked **`live: true`**. They are recordings of the `monitor` TUI that
polls a *real* car. They are not reproducible, so the default `make screenshots` skips them and
`--check` only verifies that the file is present. The two that exist (`monitor-bms`,
`monitor-drivetrain`) predate the monitor's menu bar and zoned status line, so the docs show the
replayed recordings instead until they are re-recorded. Re-record one by hand when a vehicle is
reachable:

```bash
python3 scripts/gen/gen_screenshots.py --only monitor-bms   # needs a live car + a configured device
```
