"""Units of work shared by the CLI and the API worker. Each opens its own DB session."""

from __future__ import annotations

from typing import Any

from ..config import Settings, load_settings
from ..db import session_scope
from ..models import Edit, Severity, Version, log_activity
from . import boundary
from .qc import run_qc


def qc_version(version_id: int, settings: Settings | None = None) -> dict[str, Any]:
    with session_scope(settings) as session:
        version = session.get(Version, version_id)
        if version is None:
            raise LookupError(f"Version {version_id} not found")
        run = run_qc(session, version)
        session.flush()
        return {"version_id": version_id, "run_id": run.id, "status": run.status, "issues": len(run.issues)}


def qc_all(settings: Settings | None = None) -> list[dict[str, Any]]:
    with session_scope(settings) as session:
        ids = [v.id for v in session.query(Version).order_by(Version.id)]
    return [qc_version(i, settings) for i in ids]


def boundary_sheet(edit_id: int, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or load_settings()
    with session_scope(settings) as session:
        edit = session.get(Edit, edit_id)
        if edit is None:
            raise LookupError(f"Edit {edit_id} not found")
        path, infos = boundary.render_sheet(edit, settings)
        edit.settings = {**(edit.settings or {}), "boundary_sheet": str(path),
                         "boundaries": [info.__dict__ for info in infos]}
        log_activity(session, "system", "boundary_sheet", edit, path=str(path), cuts=len(infos))
        return {"edit_id": edit_id, "path": str(path), "boundaries": [info.__dict__ for info in infos]}


def boundary_sheets_all(settings: Settings | None = None) -> list[dict[str, Any]]:
    with session_scope(settings) as session:
        ids = [e.id for e in session.query(Edit).order_by(Edit.id)]
    return [boundary_sheet(i, settings) for i in ids]


def latest_issue_counts(version: Version) -> dict[str, int]:
    counts = {Severity.ERROR: 0, Severity.WARNING: 0, Severity.INFO: 0}
    if version.qc_runs:
        for issue in version.qc_runs[-1].issues:
            counts[issue.severity] = counts.get(issue.severity, 0) + 1
    return counts
