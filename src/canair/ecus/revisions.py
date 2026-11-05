"""Which revision of each ECU the selected car has."""

from __future__ import annotations

from typing import TYPE_CHECKING

from canair.ecus.pids import revision_resolutions

if TYPE_CHECKING:
    from canair.profile import Profile


def determined_revisions(profile: Profile) -> dict[str, str]:
    """ECU name -> its revision for the selected car, for every ECU that is determined.

    An ECU with several possible revisions and no pin is absent (undetermined); see
    :func:`canair.ecus.pids.revision_resolutions` for the full picture.
    """
    return {
        name: res.revision
        for name, res in revision_resolutions(profile).items()
        if res.revision is not None
    }
