"""Blender as a render step (decision D-009).

Blender runs headless with --factory-startup (the user's own Blender settings are never read or changed) and opens
the .blend read-only; integrations/blender/shotflow_blender.py does the rendering inside Blender. Jobs live in
GenerationJob (provider "blender") and run one at a time on the shared worker, so Blender and ComfyUI never use
the 6GB GPU together.

passes: beauty / depth / mask of one frame. The beauty render becomes a version of the chosen shot (origin
        "blender", code b3d_###); depth and mask stay as job files for the 3D guide recipe.
layer:  an RGBA image sequence (rain, sparks, dust...) for services.composite.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ...config import ROOT, Settings, load_settings, writable_path
from ...db import session_scope
from ...models import GenerationJob, Origin, Shot, Version, VersionStatus, log_activity
from ..media import probe, run_ffmpeg, sha256_file
from ..qc import run_qc
from ..worker import worker
from ..comfy.gpu import GpuGuard
from ..comfy.gpu import sample as gpu_sample
from ..comfy.runner import _next_code

SCRIPT = ROOT / "integrations" / "blender" / "shotflow_blender.py"
SAMPLE_SCENES = ROOT / "scenes"
DEFAULT_ROOT = Path(r"C:\Program Files\Blender Foundation")
ACTIVE = ("queued", "running", "cancel_requested")
MAX_LAYER_FRAMES = 600
_SAMPLES = re.compile(r"Rendering\s+(\d+)\s*/\s*(\d+)\s+samples")

ERRORS = {  # exit reasons printed by shotflow_blender.py -> what the artist should do
    "NO_CAMERA": "이 Blender 파일에 활성 카메라가 없습니다. Blender에서 카메라를 추가하고 장면 속성의 카메라로 지정한 뒤 저장하세요.",
    "EMPTY_DEPTH": "카메라에 보이는 물체가 없어 깊이 이미지를 만들 수 없습니다. 카메라 방향과 물체 위치를 확인하세요.",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- Blender itself

def blender_exe() -> Path | None:
    """SHOTFLOW_BLENDER, or the newest Blender under Program Files (4.2 LTS on this PC)."""
    configured = os.environ.get("SHOTFLOW_BLENDER")
    if configured:
        return Path(configured) if Path(configured).is_file() else None
    found = sorted(DEFAULT_ROOT.glob("Blender */blender.exe"),
                   key=lambda p: [int(x) for x in re.findall(r"\d+", p.parent.name)] or [0])
    return found[-1] if found else None


def status() -> dict[str, Any]:
    exe = blender_exe()
    version = re.search(r"Blender ([\d.]+)", exe.parent.name).group(1) if exe and "Blender " in exe.parent.name else None
    return {"installed": exe is not None, "exe": str(exe) if exe else None, "version": version}


def _friendly(log_tail: str, code: int) -> str:
    for key, message in ERRORS.items():
        if key in log_tail:
            return message
    error = re.findall(r"SF_ERROR (.+)", log_tail)
    return f"Blender 렌더 실패 (종료 코드 {code}): {error[-1] if error else log_tail.strip().splitlines()[-1:]}"


def run_blender(scene: Path, job: dict[str, Any], out_dir: Path, *, on_line: Callable[[str], None] | None = None,
                should_abort: Callable[[], str | None] | None = None) -> dict[str, Any]:
    exe = blender_exe()
    if exe is None:
        raise RuntimeError("Blender가 설치되어 있지 않습니다 (C:\\Program Files\\Blender Foundation). 설치 후 다시 시도하세요.")
    if not scene.is_file():
        raise FileNotFoundError(f"Blender 파일을 찾을 수 없습니다: {scene}")
    job_file = out_dir / "job.json"
    job_file.write_text(json.dumps({**job, "out_dir": str(out_dir)}, ensure_ascii=False), encoding="utf-8")
    (out_dir / "result.json").unlink(missing_ok=True)
    command = [str(exe), "-b", "--factory-startup", str(scene), "--python-exit-code", "1",
               "--python", str(SCRIPT), "--", str(job_file)]
    tail: list[str] = []
    with (out_dir / "blender.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                   encoding="utf-8", errors="replace", creationflags=subprocess.CREATE_NO_WINDOW)
        for line in process.stdout:
            log.write(line)
            tail = (tail + [line])[-40:]
            if on_line:
                on_line(line)
            reason = should_abort() if should_abort else None
            if reason:
                process.kill()
                process.wait()
                raise InterruptedError(reason)
        code = process.wait()
    if code != 0 or not (out_dir / "result.json").is_file():
        raise RuntimeError(_friendly("".join(tail), code))
    return json.loads((out_dir / "result.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- scene list (inspected .blend files)

def _registry_path(settings: Settings) -> Path:
    return settings.data_dir / "blender" / "scenes.json"


def _registry(settings: Settings) -> dict[str, Any]:
    path = _registry_path(settings)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def scene_dir(settings: Settings, scene: Path) -> Path:
    key = sha256_file(scene)[:16]
    return writable_path(settings.data_dir / "blender" / "scenes" / key / ".keep", settings).parent


def inspect(scene: Path, settings: Settings | None = None) -> dict[str, Any]:
    """Cameras, frame range, resolution and a small preview, so the artist sees what will be rendered."""
    settings = settings or load_settings()
    scene = Path(scene).resolve()
    if scene.suffix.lower() != ".blend":
        raise ValueError("Blender 파일(.blend)만 쓸 수 있습니다")
    out = scene_dir(settings, scene)
    result = run_blender(scene, {"mode": "inspect"}, out)
    entry = {"path": str(scene), "name": scene.stem, "sha256": sha256_file(scene),
             "mtime": int(scene.stat().st_mtime), "inspected_at": utcnow().isoformat(),
             "preview": str(out / "preview.png") if (out / "preview.png").is_file() else None, **result["info"]}
    registry = _registry(settings)
    registry[str(scene)] = entry
    writable_path(_registry_path(settings), settings).write_text(
        json.dumps(registry, ensure_ascii=False, indent=1), encoding="utf-8")
    return entry


def scenes(settings: Settings | None = None) -> list[dict[str, Any]]:
    """Inspected scenes plus the bundled samples (ShotFlow/scenes) that have not been opened yet."""
    settings = settings or load_settings()
    registry = _registry(settings)
    listed = []
    for path, entry in registry.items():
        exists = Path(path).is_file()
        changed = exists and int(Path(path).stat().st_mtime) != entry.get("mtime")
        listed.append({**entry, "exists": exists, "changed": changed, "sample": _is_sample(Path(path))})
    for sample in sorted(SAMPLE_SCENES.glob("**/*.blend")):
        if str(sample.resolve()) not in registry:
            listed.append({"path": str(sample.resolve()), "name": sample.stem, "exists": True, "sample": True,
                           "inspected_at": None, "preview": None})
    return listed


def _is_sample(path: Path) -> bool:
    return path.resolve().is_relative_to(SAMPLE_SCENES.resolve())


# ---------------------------------------------------------------- jobs

def create_job(session: Session, purpose: str, scene: Path, *, shot: Shot | None = None, frame: int | None = None,
               frame_start: int | None = None, frame_end: int | None = None, width: int = 1920, height: int = 1080,
               actor: str = "ui") -> GenerationJob:
    if purpose not in ("passes", "layer"):
        raise ValueError(f"Unknown Blender job {purpose}")
    if not Path(scene).is_file():
        raise ValueError(f"Blender 파일을 찾을 수 없습니다: {scene}")
    if purpose == "passes" and (shot is None or shot.kind != "shot"):
        raise ValueError("깊이 이미지를 붙일 샷을 고르세요")
    if purpose == "layer":
        if frame_start is None or frame_end is None or frame_end < frame_start:
            raise ValueError("레이어 프레임 범위를 확인하세요 (시작 ≤ 끝)")
        if frame_end - frame_start + 1 > MAX_LAYER_FRAMES:
            raise ValueError(f"한 번에 최대 {MAX_LAYER_FRAMES}프레임까지 렌더합니다")
    if not (64 <= width <= 3840 and 64 <= height <= 2160):
        raise ValueError("해상도는 64~3840 × 64~2160 사이여야 합니다")
    info = status()
    job = GenerationJob(
        provider="blender", model=f"Blender {info['version']}" if info["version"] else "Blender", status="queued",
        params={"purpose": purpose, "recipe_name": "Blender 패스 렌더" if purpose == "passes" else "Blender 레이어",
                "scene": str(Path(scene).resolve()), "scene_name": Path(scene).stem,
                "shot_id": shot.id if shot else None, "frame": frame, "frame_start": frame_start,
                "frame_end": frame_end, "width": width, "height": height, "progress": 0.0, "stage": "대기",
                "outputs": {}, "registered": {}},
    )
    session.add(job)
    session.flush()
    log_activity(session, actor, f"blender_{purpose}_queued", shot, job_id=job.id, scene=Path(scene).name)
    return job


def submit(job_id: int, settings: Settings) -> None:
    worker.submit("blender", f"job:{job_id}", lambda: run_job(job_id, settings))


COOL_START_C = 60  # one guide keyframe heats this laptop GPU by ~20°C; the guard still stops at 83°C
COOL_WAIT_S = 600


def wait_for_cool_gpu(report: Callable[[str], None], limit: float = COOL_START_C, timeout: float = COOL_WAIT_S) -> None:
    """Back-to-back renders on this laptop reach the 83°C stop; waiting a little between jobs avoids failing them."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = gpu_sample()
        if current is None or current["temp_c"] <= limit:
            return
        report(f"GPU 식히는 중 {current['temp_c']:.0f}°C (→ {limit:.0f}°C 이하에서 시작)")
        time.sleep(5)


def submit_cooled_generation(job_ids: list[int], settings: Settings) -> None:
    """3D guide keyframes: same ComfyUI runner, but each job waits for the GPU to cool before it starts."""
    from ..comfy import runner as comfy_runner

    def run(job_id: int) -> dict[str, Any]:
        wait_for_cool_gpu(lambda message: comfy_runner._set(settings, job_id, params={"stage": message}))
        return comfy_runner.run_job(job_id, settings)

    for job_id in job_ids:
        worker.submit("generate", f"job:{job_id}", lambda job_id=job_id: run(job_id))


def recover_interrupted(settings: Settings) -> int:
    with session_scope(settings) as session:
        stale = session.query(GenerationJob).filter(GenerationJob.provider == "blender",
                                                    GenerationJob.status.in_(ACTIVE)).all()
        for job in stale:
            job.status, job.error, job.finished_at = "interrupted", "서버가 다시 시작되어 중단됨", utcnow()
        return len(stale)


def cancel(session: Session, job: GenerationJob) -> GenerationJob:
    if job.status == "queued":
        job.status, job.finished_at, job.error = "cancelled", utcnow(), "사용자가 취소"
    elif job.status == "running":
        job.status = "cancel_requested"
    return job


def _set(settings: Settings, job_id: int, **fields: Any) -> None:
    with session_scope(settings) as session:
        job = session.get(GenerationJob, job_id)
        params = fields.pop("params", None)
        for key, value in fields.items():
            setattr(job, key, value)
        if params:
            job.params = {**job.params, **params}


def _job_dir(settings: Settings, job_id: int) -> Path:
    return writable_path(settings.data_dir / "blender" / f"job{job_id:05d}" / ".keep", settings).parent


def _progress_reader(settings: Settings, job_id: int, purpose: str, total_frames: int) -> Callable[[str], None]:
    """Turn Blender's log lines into a progress value (samples for stills, saved files for sequences)."""
    state = {"saved": 0, "last": 0.0, "pass": 0}

    def on_line(line: str) -> None:
        fraction = None
        if purpose == "layer" and line.startswith("Saved:"):
            state["saved"] += 1
            fraction = state["saved"] / max(1, total_frames)
            stage = f"레이어 렌더 {state['saved']}/{total_frames}프레임"
        elif purpose == "passes" and (m := _SAMPLES.search(line)):
            done, total = int(m.group(1)), int(m.group(2))
            if done == 1 and state["last"] > 0:
                state["pass"] += 1  # the mask render starts its own sample count
            stage = "일반 렌더" if state["pass"] == 0 else "마스크 렌더"
            fraction = (done / total) * (0.85 if state["pass"] == 0 else 0.15) + (0.0 if state["pass"] == 0 else 0.85)
            state["last"] = done / total
        if fraction is not None:
            now = time.monotonic()
            if now - state.get("written", 0.0) > 1.0 or fraction >= 1.0:
                state["written"] = now
                _set(settings, job_id, params={"progress": round(min(fraction, 0.99), 3), "stage": stage})

    return on_line


def _layer_preview(out_dir: Path, start: int, fps: float) -> Path:
    """Small MP4 of the layer over dark grey so the artist can judge it before compositing."""
    preview = out_dir / "layer_preview.mp4"
    result = run_ffmpeg(["-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=0x1b1d1f:s=960x540:r=24",
                         "-framerate", str(fps), "-start_number", str(start), "-i", str(out_dir / "layer_%04d.png"),
                         "-filter_complex", "[1]scale=960:540[l];[0][l]overlay=shortest=1,format=yuv420p",
                         "-c:v", "libx264", "-crf", "20", "-movflags", "+faststart", str(preview)])
    if result.returncode != 0:
        raise RuntimeError(f"레이어 미리보기 인코딩 실패: {result.stderr[-300:]}")
    return preview


def run_job(job_id: int, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or load_settings()
    with session_scope(settings) as session:
        job = session.get(GenerationJob, job_id)
        if job.status != "queued":
            return {"job_id": job_id, "status": job.status}
        params = dict(job.params)
        job.status, job.started_at = "running", utcnow()
    scene = Path(params["scene"])
    out_dir = _job_dir(settings, job_id)
    purpose = params["purpose"]
    started = time.perf_counter()
    try:
        _set(settings, job_id, params={"stage": "Blender 파일 확인", "scene_sha256": sha256_file(scene),
                                       "blender": status()["version"]})
        wait_for_cool_gpu(lambda message: _set(settings, job_id, params={"stage": message}))
        spec = {"mode": purpose, "width": params["width"], "height": params["height"], "frame": params.get("frame"),
                "frame_start": params.get("frame_start"), "frame_end": params.get("frame_end")}
        total = (params["frame_end"] - params["frame_start"] + 1) if purpose == "layer" else 1
        last_check = [0.0, None]

        with GpuGuard() as guard:
            def should_abort() -> str | None:
                if guard.abort_reason():
                    return f"GPU 보호 기준 초과: {guard.abort_reason()}"
                if time.monotonic() - last_check[0] > 2:
                    last_check[0] = time.monotonic()
                    with session_scope(settings) as s:
                        last_check[1] = "사용자가 취소" if s.get(GenerationJob, job_id).status == "cancel_requested" else None
                return last_check[1]

            _set(settings, job_id, params={"stage": "Blender 시작 (셰이더 준비에 수십 초 걸릴 수 있음)"})
            result = run_blender(scene, spec, out_dir, on_line=_progress_reader(settings, job_id, purpose, total),
                                 should_abort=should_abort)

        outputs: dict[str, str] = {}
        if purpose == "passes":
            for name in ("beauty", "depth", "mask"):
                if (out_dir / f"{name}.png").is_file():
                    outputs[name] = str(out_dir / f"{name}.png")
        else:
            outputs["frames_dir"] = str(out_dir)
            outputs["preview"] = str(_layer_preview(out_dir, params["frame_start"], result["info"]["fps"]))
            outputs["frame_count"] = total
        elapsed = round(time.perf_counter() - started, 2)
        _set(settings, job_id, status="completed", finished_at=utcnow(),
             peak_vram_mb=guard.peak_vram_mb or None, peak_temp_c=guard.peak_temp_c or None,
             params={"outputs": outputs, "result": result, "progress": 1.0, "stage": "완료", "seconds": elapsed,
                     "camera": result["info"].get("camera")})
        if purpose == "passes":
            register_beauty(job_id, settings)
        return {"job_id": job_id, "status": "completed", "seconds": elapsed}
    except InterruptedError as exc:
        status_ = "cancelled" if "취소" in str(exc) else "failed"
        _set(settings, job_id, status=status_, error=str(exc), finished_at=utcnow(), params={"stage": "중단"})
        raise
    except Exception as exc:
        _set(settings, job_id, status="failed", error=str(exc), finished_at=utcnow(), params={"stage": "실패"})
        raise


def register_beauty(job_id: int, settings: Settings, actor: str = "blender") -> int:
    """The beauty render becomes a reviewable version; it is also the source image of the 3D guide recipe."""
    with session_scope(settings) as session:
        job = session.get(GenerationJob, job_id)
        params = dict(job.params)
        if "beauty" in params.get("registered", {}):
            return params["registered"]["beauty"]
        path = Path(params["outputs"]["beauty"])
        shot = session.get(Shot, params["shot_id"])
        info = probe(path)
        version = Version(shot=shot, code=_next_code(shot, "b3d_"), origin=Origin.BLENDER,
                          status=VersionStatus.IN_REVIEW, path=str(path), sha256=sha256_file(path),
                          bytes=path.stat().st_size, width=info.width, height=info.height, frame_count=1,
                          video_codec=info.video_codec, has_audio=False, generation_job=job)
        session.add(version)
        session.flush()
        job.params = {**params, "registered": {**params.get("registered", {}), "beauty": version.id}}
        log_activity(session, actor, "blender_registered", version, job_id=job.id, scene=params["scene_name"])
        run_qc(session, version, actor=actor)
        return version.id


def job_out(job: GenerationJob) -> dict[str, Any]:
    p = job.params or {}
    outputs = p.get("outputs") or {}
    files = {name: f"/api/blender/jobs/{job.id}/files/{name}" for name in ("beauty", "depth", "mask", "preview")
             if name in outputs}
    return {"id": job.id, "provider": job.provider, "purpose": p.get("purpose"), "status": job.status,
            "scene": p.get("scene"), "scene_name": p.get("scene_name"), "shot_id": p.get("shot_id"),
            "frame": p.get("frame"), "frame_start": p.get("frame_start"), "frame_end": p.get("frame_end"),
            "width": p.get("width"), "height": p.get("height"), "progress": p.get("progress", 0),
            "stage": p.get("stage"), "error": job.error, "seconds": p.get("seconds"), "model": job.model,
            "camera": p.get("camera"), "scene_sha256": p.get("scene_sha256"),
            "peak_vram_mb": job.peak_vram_mb, "peak_temp_c": job.peak_temp_c,
            "frame_count": outputs.get("frame_count"), "depth": (p.get("result") or {}).get("depth"),
            "beauty_version_id": (p.get("registered") or {}).get("beauty"), "files": files,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None}
