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

Inside a clone, `make install` (alias `make setup`) does the whole bootstrap in one go: Git LFS,
`uv sync`, the git hooks and `uv tool install --reinstall .`. Re-run it after a `git pull` to
refresh the bare `canair` command. It first checks that uv is version 0.13 or later and that uv
would not use a pre-release Python: the clone pins Python 3.15 (`.python-version`), and an older uv
resolves that to an old alpha, which fails to build PyYAML. If the check stops, it names the fix
(`uv self update`).

## Point your writes somewhere durable

`uv tool install` bakes a **frozen copy** of canair — including the profiles it
ships with — into uv's tool venv, and a reinstall (`canair update`) replaces that
copy wholesale. So a capture or PID edit recorded against a *bundled* profile from
a bare `canair` is deleted by your next update. Decide once, up front:

```bash
# You have the clone (you just used it to install) — keep profile data in it:
canair config set profiles_dir /path/to/canair/profiles

# Or take your own copy of a bundled profile:
canair profile adopt hyundai-ioniq-ae-ev
```

A profile you create yourself (`canair profile create`, or the first-run chooser)
already lands in `~/.config/canair/profiles/` and is unaffected. canair warns at
the moment of the write if data lands in the snapshot, and `canair update` lists
what a reinstall would delete — but neither is a substitute for the one-time
setting above. Details: [Profiles → Where your writes
land](../concepts/profiles.md#where-your-writes-land).

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
canair update --python 3.13   # reinstall on Python 3.13 instead of the newest
```

Because it checks out the advertised **release tag** (rather than fast-forwarding
`main`), the installed code is exactly the released version — never unreleased
commits sitting on the branch. If the latest release tag can't be determined
(GitHub unreachable), `canair update` reports the offline state and makes no
changes rather than guessing a version.

The reinstall also moves canair to the newest Python that uv installs itself and that every
dependency has a prebuilt wheel for. uv downloads that Python if you do not have it yet.
Without the update, uv keeps a tool on the Python it was first installed with. canair starts
faster on Python 3.15 and later. A dependency without a wheel for a new Python would build from
source, which needs a compiler and can make canair slower, so the update waits for the wheels.
If the reinstall on the new Python fails, the update tries again on the Python you had.

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

If you edit the repo or pull commits that bump the version, the two drift: a bare `canair`
keeps reporting the old version while `uv run canair` reports the new one. `canair update`
reports **which copy is running** and warns when the installed snapshot's version differs from
the clone's `pyproject.toml` (`canair status` shows the same warning). When there's no newer
release but the two have drifted, it offers a **reinstall-only resync**
(`uv tool install <clone> --reinstall`, no network or tag checkout). `canair update --json`
includes the full `install` block (`running_origin`, `running_build`, `tool_version`,
`clone_version`, `out_of_sync`).

### The version tells you which build you're running

The bare package version can't tell builds apart: a release, a clone with local
edits and a wheel built from a feature branch would all say `5.2.0`. So
`canair --version` (or `-V`) names the branch and short commit unless the code is
exactly a release (a clean tree at the `v5.2.0` tag), and then says where the
copy came from:

```console
$ canair --version
# uv run in a clone, on `main` at commit 343b244 (".dirty" with uncommitted edits)
canair 5.2.0+main.343b244 (from source)
# uv tool install . from the same clone
canair 5.2.0+main.343b244 (local build 343b244, 2026-11-05 10:12 UTC)
# after canair update: a clean build of the release tag
canair 5.2.0 (local build 9f2c1d0, 2026-11-05 10:12 UTC)
# a wheel built by GitHub Actions
canair 5.2.0 (CI build 9f2c1d0, 2026-11-05 10:12 UTC)
```

A run from source reads its git checkout live. An installed copy reads the build
stamp that the build hook (`hatch_build.py`) writes into every wheel: commit,
branch, release tag, dirty flag, build time and builder. A copy installed some
other way, without a stamp, says `installed, no build info`. `canair update --json`
has the structured form under `install.build`, with the CI run's URL for a CI
build.

The version string shows up in `canair status`, `canair update`, and — most
usefully — in every capture you record, so a suspect reading can be traced back to
the exact code that produced it. Release comparisons ignore the suffix, so a dev
checkout still gets an accurate "up to date" verdict.


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
