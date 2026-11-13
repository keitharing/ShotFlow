"""External generation done by hand (Seedance video and Nano Banana Pro stills on Higgsfield), recorded like every
other job.

ShotFlow never logs in, uploads or pays. It prepares a folder (start frame at 1920x1080, prompt, settings, Korean
steps), the artist submits it on the website, and the downloaded result is registered as a child version of the
keyframe with the credits and job ID typed in. Provider "higgsfield" keeps it in the dashboard credit totals.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from sqlalchemy.orm import Session

from ..config import Settings, writable_path
from ..models import GenerationJob, Note, Origin, Version, VersionStatus, log_activity
from .comfy.runner import _next_code
from .media import is_image, probe, read_frame, sha256_file
from .qc import run_qc

PROVIDER = "higgsfield"
MODELS = {"seedance_2_5": "Seedance 2.5", "nano_banana_pro": "Nano Banana Pro"}
IMAGE_MODELS = {"nano_banana_pro"}  # these make one still from a reference image; the others make a clip from a start frame
DURATION_RANGE = (4, 15)      # seconds; a clip that has both a start and an end frame is used whole, so it is generated at the cut's own length
VIDEO_SUFFIXES = {".mp4", ".mov", ".webm"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
MAX_BYTES = 500 * 1024 * 1024

STEPS = """ShotFlow 외부 생성 패키지 · 작업 #{job_id}

1. Higgsfield(higgsfield.ai)에 로그인합니다.
2. 영상 생성에서 모델 {model} 을 고릅니다.
3. 시작 이미지로 start_frame.png 를 올립니다.{extra}
4. prompt.txt 의 내용을 프롬프트 칸에 붙여 넣습니다.
5. 길이 {duration}초, 비율 16:9 로 설정하고 생성합니다. (크레딧이 차감됩니다)
6. 완성된 영상을 내려받습니다.
7. ShotFlow 3D 연동 > 외부 생성 화면에서 이 작업의 "결과 가져오기"에 영상을 끌어다 놓고,
   차감된 크레딧과 작업 ID(있으면)를 적습니다.

ShotFlow는 업로드·결제를 하지 않습니다. 위 과정은 직접 진행합니다.
"""


STEPS_IMAGE = """ShotFlow 외부 그림 패키지 · 작업 #{job_id}

1. Higgsfield(higgsfield.ai)에 로그인합니다.
2. 이미지 생성에서 모델 {model} 을 고릅니다. 비율 16:9, 해상도 2K, 1장.
3. 참고 이미지로 reference.png 한 장만 올립니다. (구도가 다른 그림을 함께 올리면 섞입니다)
4. prompt.txt 의 내용을 프롬프트 칸에 붙여 넣고 생성합니다. (크레딧이 차감됩니다)
5. 완성된 그림을 내려받습니다.
6. ShotFlow 3D 연동 > 외부 생성 화면에서 이 작업의 "결과 가져오기"에 그림을 끌어다 놓고,
   차감된 크레딧과 작업 ID(있으면)를 적습니다.

ShotFlow는 업로드·결제를 하지 않습니다. 위 과정은 직접 진행합니다.
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _start_frame(version: Version) -> np.ndarray:
    image = cv2.imdecode(np.fromfile(version.path, dtype=np.uint8), cv2.IMREAD_COLOR) if is_image(version.path) \
        else read_frame(Path(version.path), 0)
    if image is None:
        raise ValueError("시작 이미지를 읽을 수 없습니다")
    if image.shape[:2] != (1080, 1920):
        image = cv2.resize(image, (1920, 1080), interpolation=cv2.INTER_LANCZOS4)
    return image


def create_package(session: Session, settings: Settings, version: Version, *, prompt: str, model: str,
                   duration_s: int, actor: str = "ui", end_version: Version | None = None,
                   reference_version: Version | None = None, prompt_snapshot_id: int | None = None) -> GenerationJob:
    """Pack what an outside video service needs. `version` is the start frame. An optional `end_version` (same shot)
    fixes where the clip must end, and an optional `reference_version` (same film) is an identity reference, such
    as the product. With both ends fixed, neighbouring cuts can share the very same moment at the cut."""
    if version.shot.kind != "shot":
        raise ValueError("샷 버전을 고르세요")
    if model not in MODELS:
        raise ValueError("지원하는 외부 모델이 아닙니다")
    still = model in IMAGE_MODELS
    if still and (end_version is not None or reference_version is not None):
        raise ValueError("그림 패키지는 참고 그림 한 장만 씁니다 (끝 그림 · 다른 참고 그림은 넣을 수 없습니다)")
    if not still and not DURATION_RANGE[0] <= duration_s <= DURATION_RANGE[1]:
        raise ValueError(f"길이는 {DURATION_RANGE[0]} ~ {DURATION_RANGE[1]}초 사이에서 고르세요")
    if not prompt.strip():
        raise ValueError("프롬프트를 적어 주세요 (영어 권장)")
    if end_version is not None and end_version.shot_id != version.shot_id:
        raise ValueError("끝 그림은 시작 그림과 같은 샷의 버전이어야 합니다")
    if reference_version is not None and reference_version.shot.sequence_id != version.shot.sequence_id:
        raise ValueError("참고 그림은 같은 영상의 버전이어야 합니다")
    job = GenerationJob(provider=PROVIDER, model=model, status="awaiting_result", prompt=prompt.strip(),
                        prompt_snapshot_id=prompt_snapshot_id,
                        params={"purpose": "external", "recipe_name": f"외부 생성 ({MODELS[model]})",
                                "shot_id": version.shot_id, "source_version_id": version.id,
                                "end_version_id": end_version.id if end_version else None,
                                "reference_version_id": reference_version.id if reference_version else None,
                                "kind": "image" if still else "video", "duration_s": None if still else duration_s,
                                "aspect": "16:9", "outputs": [], "registered": {}})
    session.add(job)
    session.flush()
    folder = writable_path(settings.data_dir / "external" / f"job{job.id:05d}" / ".keep", settings).parent
    ok, buf = cv2.imencode(".png", _start_frame(version))
    if not ok:
        raise ValueError("시작 이미지 저장 실패")
    first = "reference.png" if still else "start_frame.png"
    buf.tofile(str(folder / first))
    extra, files = "", {}
    for other, name, line in ((end_version, "end_frame.png", "끝 이미지(마지막 프레임)로 end_frame.png 를 올립니다."),
                              (reference_version, "reference.png", "참고 이미지로 reference.png 를 올립니다 (제품 · 인물이 같게 나오도록).")):
        if other is None:
            continue
        ok, buf = cv2.imencode(".png", _start_frame(other))
        if not ok:
            raise ValueError("이미지 저장 실패")
        buf.tofile(str(folder / name))
        extra += f"\n   {line}"
        files[name.split(".")[0]] = f"{name} ← {other.shot.code} {other.code} (#{other.id})"
    (folder / "prompt.txt").write_text(prompt.strip() + "\n", encoding="utf-8")
    (folder / "settings.json").write_text(json.dumps(
        {"service": "Higgsfield", "model": MODELS[model], "aspect": "16:9",
         **({"kind": "image", "resolution": "2K", "reference": first} if still else
            {"kind": "video", "duration_s": duration_s, "start_frame": first}), "source_version": f"{version.shot.sequence.name} {version.shot.code} "
         f"{version.code} (#{version.id})", **files, "shotflow_job": job.id}, ensure_ascii=False, indent=1), encoding="utf-8")
    steps = STEPS_IMAGE.format(job_id=job.id, model=MODELS[model]) if still else \
        STEPS.format(job_id=job.id, model=MODELS[model], duration=duration_s, extra=extra)
    (folder / "안내.txt").write_text(steps, encoding="utf-8")
    job.params = {**job.params, "package_dir": str(folder)}
    log_activity(session, actor, "external_package", version, job_id=job.id, model=model)
    return job


def import_result(session: Session, settings: Settings, job: GenerationJob, filename: str, payload: bytes, *,
                  credits: float | None, external_id: str | None, note: str = "", actor: str = "ui") -> Version:
    params = dict(job.params or {})
    if job.provider != PROVIDER or params.get("purpose") != "external":
        raise ValueError("외부 생성 패키지가 아닙니다")
    if job.status == "completed":
        raise ValueError("이미 결과를 가져온 작업입니다")
    still = params.get("kind") == "image"
    suffix = Path(filename).suffix.lower()
    if suffix not in (IMAGE_SUFFIXES if still else VIDEO_SUFFIXES):
        raise ValueError("그림 파일(png, jpg, webp)을 올려 주세요" if still else "영상 파일(mp4, mov, webm)을 올려 주세요")
    if not payload or len(payload) > MAX_BYTES:
        raise ValueError("파일이 비었거나 500MB를 넘습니다")
    if credits is not None and credits < 0:
        raise ValueError("크레딧은 0 이상이어야 합니다")
    folder = Path(params["package_dir"])
    path = writable_path(folder / f"result{suffix}", settings)
    path.write_bytes(payload)
    try:
        info = probe(path)
    except ValueError as exc:
        path.unlink(missing_ok=True)
        raise ValueError("그림으로 읽을 수 없는 파일입니다" if still else "영상으로 읽을 수 없는 파일입니다") from exc
    parent = session.get(Version, params["source_version_id"])
    shot = parent.shot
    version = Version(shot=shot, code=_next_code(shot, "img_" if still else "sd_"), origin=Origin.GENERATED, status=VersionStatus.IN_REVIEW,
                      parent=parent, path=str(path), sha256=sha256_file(path), bytes=path.stat().st_size,
                      width=info.width, height=info.height, fps=info.fps or None, frame_count=info.frame_count,
                      duration_s=info.duration_s or None, video_codec=info.video_codec, has_audio=info.has_audio,
                      generation_job=job)
    session.add(version)
    session.flush()
    job.status, job.finished_at = "completed", utcnow()
    job.credits = credits
    job.external_id = (external_id or "").strip() or None
    job.params = {**params, "outputs": [str(path)], "registered": {"0": version.id},
                  "original_name": Path(filename).name}
    if note.strip():
        session.add(Note(version=version, author=actor, body=note.strip(), decision="comment", source="user"))
    log_activity(session, actor, "external_imported", version, job_id=job.id, credits=credits)
    run_qc(session, version, actor=actor)
    return version


def open_folder(job: GenerationJob) -> str:
    folder = Path((job.params or {}).get("package_dir", ""))
    if not folder.is_dir():
        raise ValueError("패키지 폴더가 없습니다")
    os.startfile(folder)  # local tool: opens Explorer on this PC only
    return str(folder)


def job_out(job: GenerationJob) -> dict[str, Any]:
    p = job.params or {}
    return {"id": job.id, "status": job.status, "model": job.model, "model_name": MODELS.get(job.model, job.model),
            "prompt": job.prompt, "prompt_snapshot_id": job.prompt_snapshot_id,
            "shot_id": p.get("shot_id"), "source_version_id": p.get("source_version_id"),
            "kind": p.get("kind", "video"), "duration_s": p.get("duration_s"), "folder": p.get("package_dir"),
            "credits": job.credits,
            "end_version_id": p.get("end_version_id"), "reference_version_id": p.get("reference_version_id"),
            "external_id": job.external_id, "version_id": (p.get("registered") or {}).get("0"),
            "start_frame_url": f"/api/external/packages/{job.id}/start-frame",
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None}
