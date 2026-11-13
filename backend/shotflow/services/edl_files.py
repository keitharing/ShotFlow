"""Load an edit decision file (edits/<version>/<SEQUENCE>.json) into an Edit, then assemble and check it."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config import Settings, load_settings
from ..db import session_scope
from ..models import Project, Sequence, log_activity
from . import assemble as asm
from . import boundary
from .qc import run_qc


def apply_edl(path: Path, settings: Settings | None = None, actor: str = "edl-file") -> dict[str, Any]:
    settings = settings or load_settings()
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
    with session_scope(settings) as session:
        sequence = (session.query(Sequence).join(Project)
                    .filter(Sequence.code == spec["sequence"]).order_by(Project.id).first())
        if sequence is None:
            raise LookupError(f"Sequence {spec['sequence']} not found (run import-v04 first)")
        base = next((e for e in sequence.edits if e.name == spec["base_edit"]), None)
        if base is None:
            raise LookupError(f"Base edit '{spec['base_edit']}' not found in {sequence.code}")
        edit = next((e for e in sequence.edits if e.name == spec["name"]), None)
        if edit is None:
            edit = asm.clone_edit(session, base, spec["name"], actor)

        shots = {s.code: s for s in sequence.shots if s.kind == "shot"}
        auto = spec.get("settings", {}).get("auto_color")
        entries = []
        for item in spec["entries"]:
            shot = shots[item["shot"]]
            version = next((v for v in shot.versions if v.code == item["version"]), None) if "version" in item \
                else shot.approved_version
            if version is None:
                raise LookupError(f"{item['shot']}: no approved version")
            entries.append({"version_id": version.id, "src_in": item["src_in"], "src_out": item["src_out"],
                            "transition": item.get("transition", "cut"),
                            "transition_frames": item.get("transition_frames", 0), "color": item.get("color", {})})
        notes = {item["shot"]: item.get("reason", "") for item in spec["entries"]}
        asm.update_edit(session, edit, entries, {**spec.get("settings", {}), "entry_notes": notes}, actor)

        if auto:
            offsets = asm.suggest_brightness(edit, auto["reference"], auto.get("strength", 0.5), auto.get("limit", 0.04))
            for entry in edit.entries:
                if "brightness" not in (entry.color or {}):
                    entry.color = {**(entry.color or {}), "brightness": offsets[entry.version.shot.code]}
            log_activity(session, actor, "auto_color", edit, offsets=offsets)

        version = asm.assemble(session, edit, settings, spec["name"], actor)
        session.flush()
        run = run_qc(session, version, actor=actor)
        sheet, infos = boundary.render_sheet(edit, settings)
        edit.settings = {**edit.settings, "boundary_sheet": str(sheet), "boundaries": [i.__dict__ for i in infos]}
        render = edit.settings["renders"][str(version.id)]
        return {
            "sequence": sequence.code, "edit_id": edit.id, "version_id": version.id, "version_code": version.code,
            "path": version.path, "frames": version.frame_count, "planned_frames": render["planned_frames"],
            "duration_s": round(version.duration_s or 0, 3), "render_seconds": render["seconds"],
            "qc_status": run.status, "qc_issues": [(i.severity, i.check, i.start_frame, i.message) for i in run.issues],
            "brightness": {e.version.shot.code: (e.color or {}).get("brightness") for e in edit.entries},
            "boundary_sheet": str(sheet),
        }


def apply_folder(folder: Path, settings: Settings | None = None) -> list[dict[str, Any]]:
    return [apply_edl(p, settings) for p in sorted(Path(folder).glob("*.json"))]

