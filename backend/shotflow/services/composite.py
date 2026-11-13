"""Layer composite: an RGBA sequence rendered in Blender laid over a shot version, saved as a child version.

The single-frame preview and the full render use the same blend() so what the artist approves on the slider is
exactly what gets rendered. The source video is decoded and read only; the audio track is copied unchanged.
"""

from __future__ import annotations

import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from sqlalchemy.orm import Session

from ..config import Settings, load_settings, writable_path
from ..db import session_scope
from ..models import GenerationJob, Origin, Version, VersionStatus, log_activity
from .comfy.runner import _next_code
from .media import ffmpeg_exe, is_image, probe, read_frame, sha256_file
from .qc import run_qc
from .worker import worker

MODES = {"screen": "빛 더하기", "over": "일반"}
# The picture goes YUV -> BGR -> YUV once more than the untouched cuts next to it. swscale's default (fast) rounding
# darkens it by about 1% per trip, which shows as a step in brightness where a composited clip meets its neighbour.
# With exact rounding on both the decode and the encode the mean colour comes back within 0.1 of a level.
EXACT_COLOUR = ["-sws_flags", "accurate_rnd+full_chroma_int+full_chroma_inp"]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def layer_frames(layer_job: GenerationJob) -> list[Path]:
    outputs = (layer_job.params or {}).get("outputs") or {}
    if layer_job.status != "completed" or "frames_dir" not in outputs:
        raise ValueError("완료된 Blender 레이어 작업을 고르세요")
    frames = sorted(Path(outputs["frames_dir"]).glob("layer_*.png"))
    if not frames:
        raise ValueError("레이어 프레임 파일이 없습니다")
    return frames


def layer_index(frame: int, offset: int, count: int, loop: bool) -> int | None:
    """Shot frame -> layer frame. offset = layer frame shown on shot frame 0."""
    index = frame + offset
    if loop:
        return index % count
    return index if 0 <= index < count else None


def read_rgba(path: Path, size: tuple[int, int]) -> np.ndarray:
    image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    if image is None or image.ndim != 3 or image.shape[2] != 4:
        raise ValueError(f"투명 배경(RGBA) 레이어가 아닙니다: {path.name}")
    if (image.shape[1], image.shape[0]) != size:
        image = cv2.resize(image, size, interpolation=cv2.INTER_AREA)
    return image


def blend(base: np.ndarray, layer: np.ndarray, opacity: float, mode: str) -> np.ndarray:
    """base BGR uint8, layer BGRA uint8 (straight alpha from Blender's PNG) -> BGR uint8."""
    b = base.astype(np.float32) / 255.0
    rgb = layer[..., :3].astype(np.float32) / 255.0
    a = layer[..., 3:4].astype(np.float32) / 255.0 * float(opacity)
    if mode == "screen":  # light-only: the layer can brighten but never darken (rain, sparks, glints)
        out = 1.0 - (1.0 - b) * (1.0 - rgb * a)
    elif mode == "over":
        out = rgb * a + b * (1.0 - a)
    else:
        raise ValueError(f"Unknown mode {mode}")
    return np.clip(np.rint(out * 255.0), 0, 255).astype(np.uint8)


def _check(version: Version, opacity: float, mode: str) -> None:
    if version.shot.kind != "shot":
        raise ValueError("편집본이 아닌 샷 버전에 합성하세요")
    if mode not in MODES:
        raise ValueError("합성 방식은 '빛 더하기' 또는 '일반'입니다")
    if not 0.0 <= opacity <= 1.0:
        raise ValueError("진하기는 0~1 사이입니다")


def preview_png(version: Version, layer_job: GenerationJob, frame: int, opacity: float, mode: str,
                offset: int = 0, loop: bool = True) -> bytes:
    _check(version, opacity, mode)
    frames = layer_frames(layer_job)
    base = cv2.imdecode(np.fromfile(version.path, dtype=np.uint8), cv2.IMREAD_COLOR) if is_image(version.path) \
        else read_frame(Path(version.path), frame)
    index = layer_index(frame, offset, len(frames), loop)
    out = base if index is None else blend(base, read_rgba(frames[index], (base.shape[1], base.shape[0])), opacity, mode)
    ok, buf = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        raise ValueError("미리보기 인코딩 실패")
    return buf.tobytes()


def create_job(session: Session, version: Version, layer_job: GenerationJob, *, opacity: float, mode: str,
               offset: int = 0, loop: bool = True, actor: str = "ui") -> GenerationJob:
    _check(version, opacity, mode)
    if is_image(version.path):
        raise ValueError("영상 버전에 합성하세요 (정지 이미지는 미리보기만 됩니다)")
    count = len(layer_frames(layer_job))
    job = GenerationJob(
        provider="composite", model="ShotFlow 레이어 합성", status="queued",
        params={"purpose": "composite", "recipe_name": "레이어 합성", "shot_id": version.shot_id,
                "source_version_id": version.id, "layer_job_id": layer_job.id,
                "layer_scene": (layer_job.params or {}).get("scene_name"), "layer_frames": count,
                "opacity": float(opacity), "mode": mode, "offset": int(offset), "loop": bool(loop),
                "progress": 0.0, "stage": "대기", "outputs": [], "registered": {}},
    )
    session.add(job)
    session.flush()
    log_activity(session, actor, "composite_queued", version, job_id=job.id, layer_job=layer_job.id, mode=mode)
    return job


def submit(job_id: int, settings: Settings) -> None:
    worker.submit("composite", f"job:{job_id}", lambda: run_job(job_id, settings))


def recover_interrupted(settings: Settings) -> int:
    with session_scope(settings) as session:
        stale = session.query(GenerationJob).filter(GenerationJob.provider == "composite",
                                                    GenerationJob.status.in_(("queued", "running"))).all()
        for job in stale:
            job.status, job.error, job.finished_at = "interrupted", "서버가 다시 시작되어 중단됨", utcnow()
        return len(stale)


def job_out(job: GenerationJob) -> dict[str, Any]:
    p = job.params or {}
    return {"id": job.id, "status": job.status, "progress": p.get("progress", 0), "stage": p.get("stage"),
            "error": job.error, "seconds": p.get("seconds"), "shot_id": p.get("shot_id"),
            "source_version_id": p.get("source_version_id"), "layer_job_id": p.get("layer_job_id"),
            "layer_scene": p.get("layer_scene"), "opacity": p.get("opacity"), "mode": p.get("mode"),
            "offset": p.get("offset"), "version_id": (p.get("registered") or {}).get("0"),
            "created_at": job.created_at.isoformat() if job.created_at else None}


def _set(settings: Settings, job_id: int, **fields: Any) -> None:
    with session_scope(settings) as session:
        job = session.get(GenerationJob, job_id)
        params = fields.pop("params", None)
        for key, value in fields.items():
            setattr(job, key, value)
        if params:
            job.params = {**job.params, **params}


def run_job(job_id: int, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or load_settings()
    with session_scope(settings) as session:
        job = session.get(GenerationJob, job_id)
        if job.status != "queued":
            return {"job_id": job_id, "status": job.status}
        params = dict(job.params)
        job.status, job.started_at = "running", utcnow()
        source = session.get(Version, params["source_version_id"])
        src_path, frames = Path(source.path), layer_frames(session.get(GenerationJob, params["layer_job_id"]))
    started = time.perf_counter()
    out = writable_path(settings.data_dir / "composites" / f"job{job_id:05d}" / "composite.mp4", settings)
    try:
        info = probe(src_path)
        w, h, fps, total = info.width, info.height, info.fps or 24.0, info.frame_count
        decode = subprocess.Popen([ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-i", str(src_path), *EXACT_COLOUR,
                                   "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], stdout=subprocess.PIPE,
                                  creationflags=subprocess.CREATE_NO_WINDOW)
        encode_cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
                      "-s", f"{w}x{h}", "-r", f"{fps}", "-i", "-", "-i", str(src_path), "-map", "0:v", "-map", "1:a?",
                      *EXACT_COLOUR, "-c:v", "libx264", "-crf", "16", "-preset", "medium", "-pix_fmt", "yuv420p",
                      # no "-shortest": an audio track a little shorter than the picture (common in generated
                      # clips) would cut the last video frames, and the result must keep every source frame
                      "-c:a", "copy", "-movflags", "+faststart", str(out)]
        encode = subprocess.Popen(encode_cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE,
                                  creationflags=subprocess.CREATE_NO_WINDOW)
        size, index, last = w * h * 3, 0, 0.0
        while True:
            raw = decode.stdout.read(size)
            if len(raw) < size:
                break
            base = np.frombuffer(raw, np.uint8).reshape(h, w, 3)
            li = layer_index(index, params["offset"], len(frames), params["loop"])
            frame = base if li is None else blend(base, read_rgba(frames[li], (w, h)), params["opacity"], params["mode"])
            encode.stdin.write(frame.tobytes())
            index += 1
            if time.monotonic() - last > 1.0:
                last = time.monotonic()
                _set(settings, job_id, params={"progress": round(index / max(total, 1) * 0.95, 3),
                                               "stage": f"합성 {index}/{total}프레임"})
        decode.wait()
        encode.stdin.close()
        error = encode.stderr.read().decode("utf-8", "replace")
        if encode.wait() != 0:
            raise RuntimeError(f"인코딩 실패: {error[-400:]}")
        if probe(out).frame_count != index:
            raise RuntimeError("합성 결과 프레임 수가 원본과 다릅니다")
        elapsed = round(time.perf_counter() - started, 2)
        _set(settings, job_id, status="completed", finished_at=utcnow(),
             params={"outputs": [str(out)], "progress": 1.0, "stage": "완료", "seconds": elapsed, "frames": index})
        version_id = register(job_id, settings)
        return {"job_id": job_id, "status": "completed", "version_id": version_id, "seconds": elapsed}
    except Exception as exc:
        _set(settings, job_id, status="failed", error=str(exc), finished_at=utcnow(), params={"stage": "실패"})
        raise


def register(job_id: int, settings: Settings, actor: str = "composite") -> int:
    with session_scope(settings) as session:
        job = session.get(GenerationJob, job_id)
        params = dict(job.params)
        if "0" in params.get("registered", {}):
            return params["registered"]["0"]
        parent = session.get(Version, params["source_version_id"])
        shot = parent.shot
        path = Path(params["outputs"][0])
        info = probe(path)
        version = Version(shot=shot, code=_next_code(shot, f"{parent.code}_comp_"), origin=Origin.FX,
                          status=VersionStatus.IN_REVIEW, parent=parent, path=str(path), sha256=sha256_file(path),
                          bytes=path.stat().st_size, width=info.width, height=info.height, fps=info.fps or None,
                          frame_count=info.frame_count, duration_s=info.duration_s or None,
                          video_codec=info.video_codec, has_audio=info.has_audio, generation_job=job)
        session.add(version)
        session.flush()
        job.params = {**params, "registered": {"0": version.id}}
        log_activity(session, actor, "composite_registered", version, job_id=job.id)
        run_qc(session, version, actor=actor)
        return version.id
