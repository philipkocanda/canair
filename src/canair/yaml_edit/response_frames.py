"""Write a session's confirmed response-frame counts back into the profile.

The reverse direction of :mod:`canair.ecus.response_frames`: that module resolves a ledger
into edits, and this one applies them through the ``ecus/`` editors.
"""

from __future__ import annotations

import sys

from canair.ecus.response_frames import FIELD, resolve_edits, revision_target
from canair.uds.frame_counts import FrameCountLedger


def persist(pids_data: dict, ledger: FrameCountLedger, *, verbose: bool = False) -> int:
    """Write a session's confirmed counts into the profile; return how many landed.

    Called from the shared session teardown, so it covers every transport with one
    implementation. Silent when there is nothing to say — the steady state, once a
    profile has learned its counts, is no output at all.

    Deliberately *not* wrapped in the ``pids`` command's schema-validate-and-revert
    guard. That guard reverts on any error anywhere in the file, which at teardown
    would throw away a correct edit because of unrelated pre-existing breakage, and
    it buys nothing here: the two schema rules for this field (``>= 1``, and never
    alongside ``variable_length``) are enforced by the editor's own checker, which
    already reparses and reverts the file it wrote.

    A failure never propagates. The measurement is a by-product of whatever the
    user actually ran, so a read-only profile or a locked file must not turn a
    successful session into a failed command.
    """
    from canair.profile import ProfileError, require_writable_definitions
    from canair.yaml_edit.echo import echo_edit
    from canair.yaml_edit.pids import PidsEditError, set_response_frames

    edits, notes = resolve_edits(pids_data, ledger)
    if verbose:
        for note in notes:
            print(f"  [count] {note}", file=sys.stderr)
    if not edits:
        return 0

    try:
        require_writable_definitions()
    except ProfileError as e:
        # A layered profile's base is read-only by design; the counts are simply
        # not recorded rather than the session failing over it.
        if verbose:
            print(f"  [count] not persisted: {e}", file=sys.stderr)
        return 0

    written = 0
    for edit in edits:
        try:
            routable, target = revision_target(edit.ecu, edit.pid)
        except Exception as e:  # pragma: no cover - an unloadable profile
            routable, target = False, None
            print(f"  [count] could not route {edit.ecu} {edit.pid}: {e}", file=sys.stderr)
        if not routable:
            if verbose:
                print(
                    f"  [count] {edit.ecu} {edit.pid}: not recorded — the ECU's revision is "
                    "undetermined, and a count belongs to one revision",
                    file=sys.stderr,
                )
            continue
        try:
            if target is None:
                path = set_response_frames(edit.ecu, edit.pid, edit.frames)
            else:
                path = _set_revision_frames(edit.ecu, edit.pid, target, edit.frames)
        except (PidsEditError, OSError) as e:
            print(f"  [count] could not record {edit.ecu} {edit.pid}: {e}", file=sys.stderr)
            continue
        where = f" (revision {target})" if target else ""
        echo_edit(edit.describe() + where, path)
        written += 1
    return written


def _set_revision_frames(ecu: str, pid: str, revision: str, frames: int | None):
    from canair.yaml_edit.ecu import EcusEditError
    from canair.yaml_edit.pids import PidsEditError
    from canair.yaml_edit.revisions import set_override

    try:
        if frames is None:
            return set_override(ecu, pid, None, revision, {}, clear=[FIELD])
        return set_override(ecu, pid, None, revision, {FIELD: int(frames)})
    except EcusEditError as e:
        raise PidsEditError(str(e)) from e
