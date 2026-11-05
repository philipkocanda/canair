"""``canair contribute`` — open a pull request for the active profile.

A one-command path to share a reverse-engineered profile (definitions and
captures) upstream, without the manual fork/clone/branch/push dance. It works
regardless of where the profile is stored (bundled repo, ``~/.config`` user dir,
or a ``--path`` bundle): the profile is copied into a managed fork checkout and a
PR is opened via the GitHub CLI (``gh``).

The flow is a **linear pipeline**: pre-flight gates first, then the actions they
guard (stage → review → submit). Any step can end the run by raising
:class:`~.report.Stop`, which is what keeps :func:`~.pipeline.contribute` readable
as the sequence a contributor experiences. The modules:

* :mod:`.parser` — the flags and the ``--help`` text.
* :mod:`.pipeline` — the sequence itself.
* :mod:`.gates` — the pre-flight policy (is the source fit? is the environment
  ready? has the user consented?).
* :mod:`.stage`, :mod:`.submit`, :mod:`.tidy` — the actions: stage the copy, commit
  and open the PR, tidy the source checkout.
* :mod:`.report` — the human/``--json`` duality every outcome is reported through.

Belongs: `canair contribute`: the parser, the gates and the pipeline.
Not here: the git/gh orchestration (`canair.contribute`).
Imports: `contribute`, `profile` and below.
"""

from .parser import NAME, add_parser
from .pipeline import run

__all__ = ["NAME", "add_parser", "run"]
