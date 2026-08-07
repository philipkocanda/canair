# Documentation screenshots

The docs embed SVG screenshots and animated GIFs of the CLI in action, all
**generated** from the manifest at `docs/screenshots/shots.yaml` — you never
craft or maintain them by hand. Static command output is rendered with
[`freeze`](https://github.com/charmbracelet/freeze) (SVG); interactive TUI and
montage clips with [`vhs`](https://github.com/charmbracelet/vhs) (GIF). Every
asset is captured against the bundled, read-only `ioniq-2017` profile with **no
device attached**, so it's reproducible on any machine and contains no
owner-specific data.

```bash
brew install charmbracelet/tap/freeze vhs   # one-time: the render tools
make screenshots                             # regenerate everything
make screenshots-only ONLY="bus decode-plot" # regenerate a subset
make screenshots-check                        # verify assets present + commands still run
```

`--check` (run by CI and the pre-push hook) is deliberately light: it needs
neither `freeze` nor `vhs`, and never byte-compares images (they aren't
reproducible). It verifies every manifest asset exists, flags orphans, and runs
each screenshotted command device-free — so a renamed command or dropped flag
fails the check and tells you to regenerate. **When you change the output of a
screenshotted command, re-render and commit the updated asset.** To add a shot,
append an entry to `shots.yaml` (a `rich` command or an `anim` tape) and
regenerate. Do **not** screenshot views that surface free-text capture
notes/labels (e.g. `captures --sessions`) — those can leak PII into public docs.

A few `anim` assets are marked **`live: true`** — recordings of the `monitor` TUI
polling a *real* car. These are non-reproducible, so the default `make
screenshots` skips them and `--check` only verifies the file is present. Re-record
one manually when a vehicle is reachable:

```bash
python3 scripts/gen_screenshots.py --only monitor-bms   # needs a live car + a configured device
```
