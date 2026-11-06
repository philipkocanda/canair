"""Orchestration for ``canair contribute`` — one-command profile pull requests.

Turning a profile into an upstream contribution normally means the "fork dance": fork, clone,
branch, copy the profile in, commit, push, open a PR. This module automates it via the GitHub
CLI (``gh``), which also handles authentication.

It adapts to the contributor's access to the upstream repo:

* **No push access** (the common case) — fork the repo, clone the fork, push a branch there,
  and open a cross-fork PR.
* **Push access** (a maintainer, or the owner who *cannot* fork their own repo) — clone the
  upstream repo directly, push a branch to it, and open a same-repo PR.

It does **not** matter where the source profile lives (bundled, in ``~/.config/canair/profiles/``
or at a ``--path``): the destination is always ``profiles/<name>/`` in a checkout of the upstream
repo, so the profile is **copied** in and git computes the diff, for a new profile and an edit.

The heavy lifting (``git``/``gh`` invocations) goes through :func:`canair.contribute.git._run`, so
tests drive the flow with a fake runner and no network.

Belongs: the git/gh orchestration (``git``, ``workspace``, ``github``), the profile copy (``copy``),
the checks before a contribution (``checks``), the source-checkout clean-up (``source``), the log
scrub (``logs``), the personal-data scan the PII gate runs (``pii``) and the PR summary
(``summary``).
Not here: user interaction and the pre-flight gates (``cli/commands/contribute/``, helpers).
Imports: ``profile``, ``privacy``, ``captures``, ``ecus``, ``config``, ``install`` and ``core``.
"""
