from __future__ import annotations

from typing import Any

from ..models import Edit, GenerationJob, Note, Project, QCRun, Sequence, Shot, Version
from ..services.jobs import latest_issue_counts
from ..services.media import is_image


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def project(p: Project) -> dict[str, Any]:
    return {"id": p.id, "code": p.code, "name": p.name, "fps": p.fps, "width": p.width,
            "height": p.height, "target_lufs": p.target_lufs, "sequences": len(p.sequences)}


def version_brief(v: Version) -> dict[str, Any]:
    return {
        "id": v.id, "code": v.code, "status": v.status, "origin": v.origin, "parent_id": v.parent_id,
        "duration_s": v.duration_s, "frame_count": v.frame_count, "sha_verified": v.sha_verified,
        "media_kind": "image" if is_image(v.path) else "video",
        "qc_status": v.qc_runs[-1].status if v.qc_runs else None,
        "qc_counts": latest_issue_counts(v),
    }


def shot(s: Shot) -> dict[str, Any]:
    return {
        "id": s.id, "code": s.code, "kind": s.kind, "order": s.order, "description": s.description,
        "approved_version_id": s.approved_version_id,
        "versions": [version_brief(v) for v in s.versions],
    }


def sequence(s: Sequence) -> dict[str, Any]:
    real_shots = [x for x in s.shots if x.kind == "shot"]
    return {
        "id": s.id, "code": s.code, "name": s.name, "order": s.order,
        "target_duration_s": s.target_duration_s, "shots": len(real_shots),
        "approved": sum(1 for x in real_shots if x.approved_version_id),
        "edits": [e.id for e in s.edits], "frame_size": list(s.frame_size), "own_frame_size": s.width is not None,
    }


def generation_job(j: GenerationJob | None) -> dict[str, Any] | None:
    if j is None:
        return None
    return {
        "id": j.id, "provider": j.provider, "model": j.model, "status": j.status,
        "external_id": j.external_id, "prompt": j.prompt, "params": j.params, "seed": j.seed,
        "credits": j.credits, "error": j.error, "source_record": j.source_record,
    }


def note(n: Note) -> dict[str, Any]:
    return {"id": n.id, "author": n.author, "body": n.body, "frame": n.frame,
            "decision": n.decision, "source": n.source, "created_at": _iso(n.created_at)}


def qc_run(r: QCRun) -> dict[str, Any]:
    return {
        "id": r.id, "status": r.status, "created_at": _iso(r.created_at), "duration_s": r.duration_s,
        "error": r.error, "measurements": r.measurements,
        "issues": [
            {"id": i.id, "check": i.check, "severity": i.severity, "start_frame": i.start_frame,
             "end_frame": i.end_frame, "value": i.value, "threshold": i.threshold, "message": i.message}
            for i in r.issues
        ],
    }


def version_detail(v: Version) -> dict[str, Any]:
    return {
        **version_brief(v),
        "shot": {"id": v.shot.id, "code": v.shot.code, "kind": v.shot.kind},
        "sequence": {"id": v.shot.sequence.id, "code": v.shot.sequence.code, "name": v.shot.sequence.name},
        "file_name": v.path.replace("\\", "/").rsplit("/", 1)[-1],
        "sha256": v.sha256, "sha256_expected": v.sha256_expected, "bytes": v.bytes,
        "width": v.width, "height": v.height, "fps": v.fps, "video_codec": v.video_codec,
        "has_audio": v.has_audio, "created_at": _iso(v.created_at),
        "generation_job": generation_job(v.generation_job),
        "notes": [note(n) for n in v.notes],
        "qc": qc_run(v.qc_runs[-1]) if v.qc_runs else None,
        "media_url": f"/api/versions/{v.id}/media",
    }


def edit(e: Edit) -> dict[str, Any]:
    position = 0
    entries = []
    for entry in e.entries:
        entries.append({
            "order": entry.order, "version_id": entry.version_id, "shot_code": entry.version.shot.code,
            "version_code": entry.version.code, "src_in": entry.src_in, "src_out": entry.src_out,
            "frames": entry.frames, "record_in": position, "transition": entry.transition,
            "transition_frames": entry.transition_frames, "color": entry.color,
        })
        position += entry.frames
    return {
        "id": e.id, "name": e.name, "sequence_id": e.sequence_id, "result_version_id": e.result_version_id,
        "total_frames": position, "settings": e.settings, "entries": entries,
        "boundary_sheet_url": f"/api/edits/{e.id}/boundary-sheet",
    }
