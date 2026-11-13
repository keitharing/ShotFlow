"""Review decisions: every decision is a note, and approve/reject/revise also move the version status."""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import Note, Version, VersionStatus, log_activity

DECISIONS = {
    "approve": VersionStatus.APPROVED,
    "reject": VersionStatus.REJECTED,
    "revise": VersionStatus.REVISION,
    "comment": None,
}


def add_note(
    session: Session,
    version: Version,
    *,
    author: str,
    body: str,
    decision: str = "comment",
    frame: int | None = None,
) -> Note:
    if decision not in DECISIONS:
        raise ValueError(f"Unknown decision: {decision}")
    author = author.strip() or "익명"
    body = body.strip()
    if not body and decision == "comment":
        raise ValueError("Comment body is empty")
    if frame is not None and version.frame_count and not 0 <= frame < version.frame_count:
        raise ValueError(f"Frame {frame} outside 0..{version.frame_count - 1}")

    note = Note(version=version, author=author, body=body, decision=decision, frame=frame, source="user")
    session.add(note)
    new_status = DECISIONS[decision]
    shot = version.shot
    if new_status == VersionStatus.APPROVED:
        previous = shot.approved_version
        if previous is not None and previous.id != version.id:
            # One approved version per shot: the old one goes back to review instead of staying approved.
            previous.status = VersionStatus.IN_REVIEW
            log_activity(session, author, "superseded", previous, by_version=version.id)
        shot.approved_version = version
    elif new_status is not None and shot.approved_version_id == version.id:
        shot.approved_version = None
    if new_status is not None:
        version.status = new_status
    session.flush()
    log_activity(session, author, f"review_{decision}", version, note_id=note.id, frame=frame)
    return note
