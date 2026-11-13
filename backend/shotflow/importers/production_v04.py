"""Import the AI Product Films v0.4 production records (read-only) into ShotFlow."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ..config import Settings, load_settings
from ..models import (
    Edit,
    EditEntry,
    GenerationJob,
    Note,
    Origin,
    Project,
    Sequence,
    Shot,
    Version,
    VersionStatus,
    log_activity,
)
from ..services.media import probe, sha256_file

PROJECT_CODE = "AI_PRODUCT_FILMS"
V03_CREDITS_PER_CLIP = 60.0  # v0.3 ledger: 180 credits for the 3 clips reused in v0.4
EDIT_FILES = {
    "BASALT RUN": "자동차_24초_검토본.mp4",
    "RAIN ATLAS": "향수_20초_검토본.mp4",
    "THE LAST LIGHT": "시계_24초_검토본.mp4",
}
_CLIP_NAME = re.compile(r"^(?P<shot>[A-Z]\d{2})_(?P<version>.+)_original\.mp4$")


@dataclass
class ImportReport:
    sequences: int = 0
    shots: int = 0
    versions: int = 0
    edits: int = 0
    edit_versions: int = 0
    jobs_generated: int = 0
    jobs_reused: int = 0
    jobs_rejected: int = 0
    notes: int = 0
    credits_total: float = 0.0
    sha_mismatches: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _seq_code(film: str) -> str:
    return film.upper().replace(" ", "_")


def _register_media(version: Version, path: Path, expected_sha: str | None, report: ImportReport) -> None:
    info = probe(path)
    version.path = str(path)
    version.bytes = path.stat().st_size
    version.sha256 = sha256_file(path)
    version.sha256_expected = expected_sha
    version.width, version.height, version.fps = info.width, info.height, info.fps
    version.frame_count, version.duration_s = info.frame_count, info.duration_s
    version.video_codec, version.has_audio = info.video_codec, info.has_audio
    if expected_sha and version.sha256 != expected_sha:
        report.sha_mismatches.append(path.name)


def import_production_v04(session: Session, settings: Settings | None = None, actor: str = "importer") -> ImportReport:
    settings = settings or load_settings()
    src = settings.production_v04
    records = src / "05_Records"
    if not records.is_dir():
        raise FileNotFoundError(f"v0.4 records not found: {records}")

    timeline = _load(records / "Selected_Timeline.json")
    existing = session.query(Project).filter_by(code=PROJECT_CODE).one_or_none()
    if existing:  # importing again replaces the imported project; source files are never touched
        imported_codes = {_seq_code(film["film"]) for film in timeline}
        hand_made = sorted(s.code for s in existing.sequences if s.code not in imported_codes)
        if hand_made:  # sequences registered by hand (week 6) would be deleted with the project
            raise ValueError(
                "직접 등록한 영상이 있어 다시 불러올 수 없습니다 (다시 불러오면 이 영상들이 지워집니다): "
                + ", ".join(hand_made))
        session.delete(existing)
        session.flush()

    report = ImportReport()
    review = {s["shot_id"]: s for s in _load(src / "02_QA" / "Visual_Review.json")["shots"]}
    reused = {r["shot_id"]: r for r in _load(records / "Reused_Clips.json")}
    downloads = {
        (d["shot_id"], d["version"]): d
        for d in (_load(p) for p in sorted(records.glob("Download_*.json")))
    }
    submissions = [(p, _load(p)) for p in sorted(records.glob("Submission_*.json"))]
    edit_log = {e["film"]: e for e in _load(records / "Actual_Edit_Log.json")}

    project = Project(code=PROJECT_CODE, name="AI Product Films", fps=24.0, width=1920, height=1080)
    session.add(project)

    used_sources = {shot["source"].split("/")[-1] for film in timeline for shot in film["shots"]}
    shots_by_code: dict[str, Shot] = {}
    for order, film in enumerate(timeline):
        sequence = Sequence(project=project, code=_seq_code(film["film"]), name=film["film"],
                            order=order, target_duration_s=float(film["duration_s"]))
        for shot_order, entry in enumerate(film["shots"]):
            shot = Shot(sequence=sequence, code=entry["shot_id"], order=shot_order)
            shots_by_code[shot.code] = shot
        session.add(sequence)
        report.sequences += 1
    report.shots = len(shots_by_code)

    versions: dict[tuple[str, str], Version] = {}
    for clip in sorted((src / "01_Original_Videos").glob("*.mp4")):
        match = _CLIP_NAME.match(clip.name)
        if not match:
            report.warnings.append(f"Unrecognised clip name: {clip.name}")
            continue
        shot = shots_by_code[match["shot"]]
        code = match["version"]
        expected = (downloads.get((shot.code, code)) or {}).get("sha256") or (reused.get(shot.code) or {}).get("sha256")
        version = Version(shot=shot, code=code, origin=Origin.IMPORTED,
                          status=VersionStatus.APPROVED if clip.name in used_sources else VersionStatus.REJECTED)
        _register_media(version, clip, expected, report)
        versions[(shot.code, code)] = version
        if version.status == VersionStatus.APPROVED:
            shot.approved_version = version
    report.versions = len(versions)

    # B01 retry01 regenerated the rejected v01 (it added a car to an environment-only shot).
    for (shot_code, code), version in versions.items():
        if code.startswith("retry") and (shot_code, "v01") in versions:
            version.parent = versions[(shot_code, "v01")]

    for path, sub in submissions:
        job_id = sub.get("job_id")
        params = sub.get("params", {})
        if not job_id:
            session.add(GenerationJob(
                provider="higgsfield", model=params.get("model") or "seedance_2_5",
                status=sub.get("status", "rejected"), credits=0.0,
                error=(sub.get("response") or {}).get("error"), source_record=path.name,
                params={"shot_id": sub.get("shot_id")},
            ))
            report.jobs_rejected += 1
            continue
        job = GenerationJob(
            provider="higgsfield", model=params.get("model") or sub.get("model"), external_id=job_id,
            status="completed", prompt=params.get("prompt"), params=params,
            seed=sub.get("seed") if isinstance(sub.get("seed"), int) else None,
            credits=float(sub.get("estimated_credits") or 0.0), source_record=path.name,
        )
        session.add(job)
        version = versions.get((sub["shot_id"], sub["version"]))
        if version is None:
            report.warnings.append(f"Submission without clip: {path.name}")
        else:
            version.generation_job = job
        report.jobs_generated += 1
        report.credits_total += job.credits or 0.0

    for shot_code, item in reused.items():
        version = next(v for (s, _), v in versions.items() if s == shot_code and v.code.startswith("reused"))
        job = GenerationJob(
            provider="higgsfield", model="seedance_2_5", status="reused_from_v0.3",
            credits=V03_CREDITS_PER_CLIP, source_record="Reused_Clips.json",
            params={"v03_source": item["source"], "new_credits_in_v04": item["new_credits"]},
        )
        session.add(job)
        version.generation_job = job
        report.jobs_reused += 1
        report.credits_total += V03_CREDITS_PER_CLIP

    for shot_code, item in review.items():
        version = versions.get((shot_code, item["selected_version"]))
        if version is None:
            report.warnings.append(f"Review for unknown version: {shot_code} {item['selected_version']}")
            continue
        body = f"{item['observation']}\n한계: {item['limitations']}"
        session.add(Note(version=version, author="가져온 기록 (v0.4 Visual_Review)", body=body,
                         decision="approve", source="imported"))
        report.notes += 1

    for film in timeline:
        sequence = next(s for s in project.sequences if s.name == film["film"])
        edit_shot = Shot(sequence=sequence, code="EDIT", kind="edit", order=999,
                         description="시퀀스 편집본")
        edit = Edit(sequence=sequence, name="v0.4 review cut",
                    settings={"fade_in_s": 0.12, "fade_out_s": 0.25, "audio_cut_fade_ms": [40, 60]})
        for order, entry in enumerate(film["shots"]):
            shot_code = entry["shot_id"]
            clip_name = entry["source"].split("/")[-1]
            code = _CLIP_NAME.match(clip_name)["version"]
            edit.entries.append(EditEntry(order=order, version=versions[(shot_code, code)],
                                          src_in=entry["source_in_frame"],
                                          src_out=entry["source_out_frame_exclusive"]))
        movie = settings.portfolio_edits / EDIT_FILES[film["film"]]
        if movie.is_file():
            result = Version(shot=edit_shot, code="v0.4", origin=Origin.IMPORTED, status=VersionStatus.IN_REVIEW)
            _register_media(result, movie, edit_log.get(film["film"], {}).get("sha256"), report)
            edit_shot.approved_version = None
            edit.result_version = result
            report.edit_versions += 1
        else:
            report.warnings.append(f"Edit movie not found: {movie}")
        session.add(edit)
        report.edits += 1

    session.flush()
    log_activity(session, actor, "import_v04", project, **{k: v for k, v in report.as_dict().items() if k != "warnings"})
    return report
