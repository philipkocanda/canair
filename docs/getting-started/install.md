# Install

canair isn't on PyPI yet, so install it from a clone of the repository. You need
[`uv`](https://docs.astral.sh/uv/) (a fast Python package/tool manager).

## Install the CLI

```bash
git clone https://github.com/philipkocanda/canair.git
cd canair
uv tool install .    # installs the `canair` command globally
canair --help        # first run creates ~/.config/canair/ + a starter config.yaml
```

The first `canair` run scaffolds your user config directory and a starter
`config.yaml`. If you run an interactive command that needs a vehicle profile,
canair offers a **first-run chooser** — pick one of the bundled/discovered
profiles or create a new one. It tells you exactly where profiles live
(`~/.config/canair/profiles/`) and records your choice as `default_profile` so
later runs are non-interactive. The chooser never fires when piped/scripted, or
when you pass `--profile`/`CANAIR_PROFILE`. Next:
[connect your dongle](connect-device.md).

## Try it without installing

To poke around without a global install, run it straight from the checkout:

```bash
uv run canair --help
```

`uv run canair …` executes the code in the current repo checkout. This is also
what you'll use if you're hacking on canair itself.

## Staying up to date

canair checks GitHub once a day (in a background thread — it never blocks a
command, and any network failure is silently ignored) for a newer released
version. When one is available it prints a one-line notice with a link to the
[changelog](https://github.com/philipkocanda/canair/blob/main/CHANGELOG.md) and
how to upgrade.

Upgrade with a single command — it checks out the latest release tag in your
clone and reinstalls the CLI from it (keeping the git-clone install intact):

```bash
canair update            # check, confirm, then checkout <tag> + uv tool install . --reinstall
canair update --check    # report current/latest + changelog only, change nothing
canair update --yes      # skip the confirmation prompt (automation)
```

Because it checks out the advertised **release tag** (rather than fast-forwarding
`main`), the installed code is exactly the released version — never unreleased
commits sitting on the branch. If the latest release tag can't be determined
(GitHub unreachable), `canair update` reports the offline state and makes no
changes rather than guessing a version.

If canair can't find your clone or `uv` (e.g. a different install method), it
prints the exact manual commands instead. To silence the automatic check, set
`check_for_updates: false` in your config (or export `CANAIR_NO_UPDATE_CHECK=1`).

### The two installs can drift out of sync

Running `uv tool install .` **and** working in a clone means you have *two*
copies of canair on the machine:

- a bare `canair` runs the **installed snapshot** (uv's tool venv), taken at the
  last `uv tool install`;
- `uv run canair` runs the **repo working tree** — whatever you've currently
  checked out or edited.

Edit the repo (or pull new commits that bump the version) and the two drift: a
bare `canair` keeps reporting the old version while `uv run canair` reports the
new one. `canair update` detects this — it reports **which copy is running** and
warns when the installed snapshot's version differs from the source clone's
`pyproject.toml` (the same warning also shows up in `canair status`). When there
is no newer release to check out but the two have drifted, `canair update` offers
a **reinstall-only resync** — it runs `uv tool install <clone> --reinstall` (no
network, no tag checkout) to bring the bare `canair` back in line with the clone.
`canair update --json` includes the full `install` block (`running_origin`,
`tool_version`, `clone_version`, `out_of_sync`) for scripts.


## Tab-completion (optional)

Completion covers subcommands, flags, and ECU/PID names from your active profile:

```bash
canair completion --install    # auto-detects your shell; open a new shell after
```

Completion hooks the literal `canair` command word, so it won't fire through
`uv run`. If you work from a checkout, activate the venv first:

```bash
uv sync && source .venv/bin/activate
canair completion --install
```

## Git LFS (for raw-CAN logs)

Large raw-CAN broadcast logs (`.blf`/`.asc`/`.trc` and a profile's
`captures/can/`) are stored with [Git LFS](https://git-lfs.com). Install it once
so a clone fetches the real log contents instead of pointer files:

```bash
git lfs install    # one-time, per machine
```

Without it the tiny bundled fixtures still work (they're plain git), but any
committed large log will appear as an LFS pointer. See
[Broadcast frames → Storing raw-CAN logs](../concepts/broadcast-frames.md) for
what is committed vs. fetched on demand.
