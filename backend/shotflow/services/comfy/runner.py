"""Generation jobs: stored in GenerationJob (survive restarts), executed one at a time by the shared worker."""

from __future__ import annotations

import copy
import json
import string
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
from sqlalchemy.orm import Session

from ...config import Settings, load_settings, writable_path
from ...db import session_scope
from ...models import GenerationJob, Origin, RecipeVersion, Shot, Version, VersionStatus, log_activity
from ..media import ffmpeg_exe, is_image, probe, read_frame, run_ffmpeg, sha256_file
from ..qc import run_qc
from ..worker import worker
from . import fix as fixlib
from . import server
from .client import ComfyClient, ComfyError, Interrupted
from .gpu import GpuGuard
from .recipes import Exposed, apply_values, find_node, validate_values
from .server import model_path

ACTIVE = ("queued", "running", "cancel_requested")
CODE_PREFIX = {"keyframe": "kf", "img2img": "i2i", "t2v": "t2v", "v2v": "v2v", "inpaint": "fix", "guide": "g3d"}
MAX_GRID = 12  # one variation grid per batch (GPU rule in the handoff)
OOM_SCALE = 0.75


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def recipe_meta(rv: RecipeVersion) -> dict[str, Any]:
    return (rv.ui_json or {}).get("meta", {})


# ---------------------------------------------------------------- creating jobs

def _grid(meta: dict[str, Any], exposed: list[Exposed], base: dict[str, Any],
          seeds: list[int] | None, strengths: list[float] | None) -> list[dict[str, Any]]:
    seed_keys = [e.key for e in exposed if e.input == "seed"]
    strength = meta.get("strength_input")
    strength_key = None
    if strength and strengths:
        strength_key = next(e.key for e in exposed
                            if e.node_title == strength["node_title"] and e.input == strength["input"])
    combos = []
    for seed in seeds or [None]:
        for value in strengths or [None]:
            values = dict(base)
            if seed is not None:
                values.update({k: int(seed) for k in seed_keys})
            if value is not None and strength_key:
                values[strength_key] = float(value)
            combos.append(values)
    if len(combos) > MAX_GRID:
        raise ValueError(f"한 번에 최대 {MAX_GRID}개까지 생성합니다 (요청 {len(combos)}개)")
    return combos


def _check_limits(meta: dict[str, Any], exposed: list[Exposed], values: dict[str, Any]) -> None:
    limits = meta.get("limits")
    if not limits:
        return
    pick = {e.input: values.get(e.key, e.default) for e in exposed}
    width, height, length = pick.get("width"), pick.get("height"), pick.get("length")
    if width and height:
        multiple = limits.get("multiple", 1)
        if width % multiple or height % multiple:
            raise ValueError(f"가로·세로는 {multiple}의 배수여야 합니다")
        if width * height > limits["max_area"]:
            raise ValueError(f"해상도 {width}x{height}는 이 PC 기준 최대 면적({limits['max_area']}px)을 넘습니다")
    if length is not None:
        if not limits["length_min"] <= length <= limits["length_max"] or (length - 1) % 4:
            raise ValueError(f"프레임 수는 {limits['length_min']}~{limits['length_max']} 사이의 4n+1이어야 합니다")


def _main_model(rv: RecipeVersion) -> str | None:
    files = [m["file"] for m in rv.required_models or [] if m["folder"] in ("checkpoints", "diffusion_models")]
    return files[0] if files else None


def _prompt_text(rv: RecipeVersion, values: dict[str, Any]) -> str | None:
    for e in rv.exposed_inputs:
        if e["label"] == "프롬프트" and e["input"] == "text":
            return values.get(e["key"], e["default"])
    return None


def create_jobs(session: Session, rv: RecipeVersion, *, shot: Shot, values: dict[str, Any],
                seeds: list[int] | None = None, strengths: list[float] | None = None,
                source_version: Version | None = None, source_frame: int = 0,
                fix: dict[str, Any] | None = None, guide: dict[str, Any] | None = None,
                actor: str = "ui") -> list[GenerationJob]:
    meta = recipe_meta(rv)
    exposed = [Exposed(**e) for e in rv.exposed_inputs]
    base = validate_values(exposed, values)
    combos = _grid(meta, exposed, base, seeds, strengths)
    for combo in combos:
        _check_limits(meta, exposed, combo)
    needs_source = {"source_image", "source_video"} & set((meta.get("bindings") or {}).keys())
    if needs_source and source_version is None:
        raise ValueError("이 레시피는 원본 버전이 필요합니다")
    if source_version is not None and source_version.shot_id != shot.id:
        raise ValueError("원본 버전이 다른 샷에 속해 있습니다")
    if "guide_depth" in (meta.get("bindings") or {}) and not (guide or {}).get("depth_path"):
        raise ValueError("이 레시피는 Blender 깊이 이미지가 필요합니다 (3D 연동 화면에서 실행하세요)")

    batch = uuid.uuid4().hex[:10]
    jobs = []
    seed_keys = [e.key for e in exposed if e.input == "seed"]
    for combo in combos:
        seed = combo.get(seed_keys[0]) if seed_keys else None
        job = GenerationJob(
            provider="comfyui", model=_main_model(rv), recipe_version_id=rv.id, batch_id=batch,
            status="queued", prompt=_prompt_text(rv, combo), seed=seed,
            mask_path=(fix or {}).get("mask_path"),
            params={"recipe": meta.get("id"), "recipe_name": meta.get("name"), "recipe_version": rv.number,
                    "kind": meta.get("kind"), "shot_id": shot.id, "values": combo,
                    "source_version_id": source_version.id if source_version else None,
                    "source_frame": source_frame, "purpose": "fix" if fix else "generate", "fix": fix,
                    "guide": guide,
                    "progress": 0.0, "outputs": [], "registered": {}},
        )
        session.add(job)
        jobs.append(job)
    session.flush()
    log_activity(session, actor, "generation_queued", shot, batch=batch, jobs=len(jobs), recipe=meta.get("id"))
    return jobs


def submit(job_ids: list[int], settings: Settings) -> None:
    for job_id in job_ids:
        worker.submit("generate", f"job:{job_id}", lambda job_id=job_id: run_job(job_id, settings))


def recover_interrupted(settings: Settings) -> int:
    """Jobs that were queued/running when the server stopped cannot resume; mark them honestly."""
    with session_scope(settings) as session:
        stale = session.query(GenerationJob).filter(GenerationJob.provider == "comfyui",
                                                    GenerationJob.status.in_(ACTIVE)).all()
        for job in stale:
            job.status = "interrupted"
            job.error = "서버가 다시 시작되어 중단됨"
            job.finished_at = utcnow()
        return len(stale)


def cancel(session: Session, job: GenerationJob) -> GenerationJob:
    if job.status == "queued":
        job.status, job.finished_at, job.error = "cancelled", utcnow(), "사용자가 취소"
    elif job.status == "running":
        job.status = "cancel_requested"
    return job


# ---------------------------------------------------------------- running a job

def _set(settings: Settings, job_id: int, **fields: Any) -> None:
    with session_scope(settings) as session:
        job = session.get(GenerationJob, job_id)
        params = fields.pop("params", None)
        for key, value in fields.items():
            setattr(job, key, value)
        if params:
            job.params = {**job.params, **params}


def _status(settings: Settings, job_id: int) -> str:
    with session_scope(settings) as session:
        return session.get(GenerationJob, job_id).status


def _model_hashes(rv_models: list[dict[str, Any]], settings: Settings) -> list[dict[str, Any]]:
    """SHA-256 of each model file, cached by size+mtime (hashing 10GB of Wan weights takes ~a minute once)."""
    cache_path = settings.cache_dir / "model_hashes.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.is_file() else {}
    result = []
    for model in rv_models:
        path = model_path(model["folder"], model["file"], settings)
        if not path.is_file():
            result.append({**model, "sha256": None})
            continue
        stat = path.stat()
        key = f"{path}|{stat.st_size}|{int(stat.st_mtime)}"
        if key not in cache:
            cache[key] = sha256_file(path)
        result.append({"file": model["file"], "folder": model["folder"], "bytes": stat.st_size, "sha256": cache[key]})
    writable_path(cache_path, settings).write_text(json.dumps(cache, indent=1), encoding="utf-8")
    return result


def _job_dir(settings: Settings, job_id: int) -> Path:
    return writable_path(settings.data_dir / "generated" / f"job{job_id:05d}" / ".keep", settings).parent


def _prepare_source_video(src: Version, start_frame: int, prep: dict[str, Any], out: Path) -> Path:
    start = start_frame / (src.fps or 24)
    w, h, fps, frames = prep["width"], prep["height"], prep["fps"], prep["frames"]
    result = run_ffmpeg(["-y", "-loglevel", "error", "-ss", f"{start:.4f}", "-i", src.path,
                         "-vf", f"fps={fps},scale={w}:-2,crop={w}:{h}", "-frames:v", str(frames),
                         "-an", "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", str(out)])
    if result.returncode != 0:
        raise RuntimeError(f"원본 구간 준비 실패: {result.stderr[-400:]}")
    if probe(out).frame_count != frames:
        raise RuntimeError(f"원본 구간이 {frames}프레임보다 짧습니다 (시작 프레임을 앞으로 옮기세요)")
    return out


def _bind_inputs(client: ComfyClient, graph: dict[str, Any], api: dict[str, Any], meta: dict[str, Any],
                 params: dict[str, Any], job_dir: Path, settings: Settings) -> dict[str, str]:
    bound: dict[str, str] = {}
    sub = f"shotflow/{job_dir.name}"
    with session_scope(settings) as session:
        src = session.get(Version, params["source_version_id"]) if params.get("source_version_id") else None
        for role, spec in (meta.get("bindings") or {}).items():
            if role == "source_image":
                frame = fixlib.imread(Path(src.path)) if is_image(src.path) else \
                    read_frame(Path(src.path), int(params.get("source_frame") or 0))
                local = fixlib.imwrite(job_dir / "source.png", frame)
            elif role == "source_video":
                local = _prepare_source_video(src, int(params.get("source_frame") or 0), spec["prepare"],
                                              job_dir / "source.mp4")
            elif role in ("crop_image", "crop_mask"):
                local = Path(params["fix"]["crop_path" if role == "crop_image" else "crop_mask_path"])
            elif role == "guide_depth":  # depth pass rendered by Blender (services/blender)
                local = Path(params["guide"]["depth_path"])
            else:
                raise ValueError(f"Unknown binding role {role}")
            name = client.upload(local, sub)
            graph[find_node(api, spec["node_title"])]["inputs"][spec["input"]] = name
            bound[role] = str(local)
    return bound


def _shrink(graph: dict[str, Any], api: dict[str, Any], meta: dict[str, Any]) -> list[str]:
    changes = []
    for size in meta.get("size_inputs", []):
        node = graph[find_node(api, size["node_title"])]["inputs"]
        step = (meta.get("limits") or {}).get("multiple", 8)
        for key in (size["width"], size["height"]):
            new = max(step, int(node[key] * OOM_SCALE) // step * step)
            changes.append(f"{key} {node[key]}→{new}")
            node[key] = new
    return changes


def _collect(client: ComfyClient, entry: dict[str, Any], out_node: str, meta: dict[str, Any],
             job_dir: Path) -> list[str]:
    images = entry.get("outputs", {}).get(out_node, {}).get("images", [])
    if not images:
        raise ComfyError("출력 이미지가 없습니다", entry.get("outputs"))
    paths = []
    for i, item in enumerate(images):
        data = client.view(item["filename"], item.get("subfolder", ""), item.get("type", "output"))
        path = job_dir / f"out_{i:05d}.png"
        path.write_bytes(data)
        paths.append(path)
    if meta["output"]["kind"] != "frames":
        return [str(p) for p in paths]
    first = fixlib.imread(paths[0])
    h, w = first.shape[:2]
    movie = job_dir / "output.mp4"
    frames = b"".join(fixlib.imread(p).tobytes() for p in paths)
    proc = subprocess.run([ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24",
                           "-s", f"{w}x{h}", "-r", str(meta["output"]["fps"]), "-i", "-", "-an",
                           "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                           str(movie)], input=frames, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"프레임 인코딩 실패: {proc.stderr[-400:]!r}")
    if probe(movie).frame_count != len(paths):
        raise RuntimeError("인코딩된 프레임 수가 생성 프레임 수와 다릅니다")
    return [str(movie)]


def run_job(job_id: int, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or load_settings()
    with session_scope(settings) as session:
        job = session.get(GenerationJob, job_id)
        if job.status != "queued":
            return {"job_id": job_id, "status": job.status}
        rv = session.get(RecipeVersion, job.recipe_version_id)
        api, meta, params = copy.deepcopy(rv.api_json), recipe_meta(rv), copy.deepcopy(job.params)
        models = list(rv.required_models or [])
        job.status, job.started_at = "running", utcnow()

    job_dir = _job_dir(settings, job_id)
    client = None
    started = time.perf_counter()
    try:
        _set(settings, job_id, params={"stage": "모델 확인", "models": _model_hashes(models, settings)})
        _set(settings, job_id, params={"stage": "ComfyUI 준비"})
        client = ComfyClient(server.start(settings)["url"])
        graph = apply_values(api, params["values"])
        bound = _bind_inputs(client, graph, api, meta, params, job_dir, settings)
        out_node = find_node(api, meta["output"]["node_title"])
        graph[out_node]["inputs"]["filename_prefix"] = f"shotflow/{job_dir.name}/out"

        last_write = [0.0]

        def progress(fraction: float, node: str | None) -> None:
            if time.monotonic() - last_write[0] >= 1.0 or fraction >= 1.0:
                last_write[0] = time.monotonic()
                label = (api.get(node or "", {}).get("_meta") or {}).get("title") if node else None
                _set(settings, job_id, params={"progress": round(fraction, 3), "stage": label or "생성 중"})

        retry: list[str] = []
        guard = GpuGuard()
        for attempt in range(2):
            try:
                with GpuGuard() as guard:
                    prompt_id = client.queue(graph)
                    _set(settings, job_id, external_id=prompt_id, params={"stage": "대기열", "graph": graph})
                    last_check = [0.0, None]

                    def should_abort() -> str | None:
                        if guard.abort_reason():
                            return f"GPU 보호 기준 초과: {guard.abort_reason()}"
                        if time.monotonic() - last_check[0] > 2:
                            last_check[0] = time.monotonic()
                            last_check[1] = "사용자가 취소" if _status(settings, job_id) == "cancel_requested" else None
                        return last_check[1]

                    entry = client.wait(prompt_id, progress, should_abort)
                break
            except ComfyError as exc:
                if attempt == 0 and exc.out_of_memory and meta.get("size_inputs"):
                    retry = _shrink(graph, api, meta)
                    _set(settings, job_id, params={"stage": "메모리 부족 → 해상도 낮춰 재시도", "oom_retry": retry})
                    client.free()
                    continue
                raise

        outputs = _collect(client, entry, out_node, meta, job_dir)
        elapsed = round(time.perf_counter() - started, 2)
        _set(settings, job_id, status="completed", finished_at=utcnow(),
             peak_vram_mb=guard.peak_vram_mb or None, peak_temp_c=guard.peak_temp_c or None,
             params={"outputs": outputs, "progress": 1.0, "stage": "완료", "seconds": elapsed,
                     "bound": bound, "min_ram_gib": round(guard.min_ram_gib, 2) if guard.min_ram_gib < 1e8 else None})
        if params.get("purpose") == "fix":
            for index in range(len(outputs)):
                register_output(job_id, index, settings, actor="auto-fix")
        return {"job_id": job_id, "status": "completed", "outputs": outputs, "seconds": elapsed}
    except Interrupted as exc:
        status = "cancelled" if "취소" in str(exc) else "failed"
        _set(settings, job_id, status=status, error=str(exc), finished_at=utcnow(), params={"stage": "중단"})
        raise
    except Exception as exc:
        detail = getattr(exc, "detail", None)
        message = f"{exc}" + (f" | {json.dumps(detail, ensure_ascii=False, default=str)[:600]}" if detail else "")
        _set(settings, job_id, status="failed", error=message, finished_at=utcnow(), params={"stage": "실패"})
        raise
    finally:
        if client is not None:
            try:
                client.free()
            except Exception:
                pass
            client.close()


# ---------------------------------------------------------------- results -> versions

def _next_code(shot: Shot, prefix: str) -> str:
    taken = {v.code for v in shot.versions}
    if prefix.endswith("_fix_"):
        for letter in string.ascii_lowercase:
            if f"{prefix}{letter}" not in taken:
                return f"{prefix}{letter}"
    n = 1
    while f"{prefix}{n:03d}" in taken:
        n += 1
    return f"{prefix}{n:03d}"


def register_output(job_id: int, index: int, settings: Settings | None = None, actor: str = "ui") -> int:
    """Turn one job output into a version (fix outputs are stitched back into the full frame first)."""
    settings = settings or load_settings()
    with session_scope(settings) as session:
        job = session.get(GenerationJob, job_id)
        params = dict(job.params)
        if job.status != "completed":
            raise ValueError("완료된 작업만 등록할 수 있습니다")
        if str(index) in params.get("registered", {}):
            return params["registered"][str(index)]
        output = Path(params["outputs"][index])
        shot = session.get(Shot, params["shot_id"])
        parent = session.get(Version, params["source_version_id"]) if params.get("source_version_id") else None

        if params.get("purpose") == "fix":
            prep = fixlib.FixPrep(**params["fix"])
            frame_no = int(params.get("source_frame") or 0)
            code = _next_code(shot, f"{parent.code}_f{frame_no}_fix_")
            path = fixlib.stitch(prep, fixlib.imread(output), output.parent / f"{code}.png", settings)
            origin = Origin.FIX
        else:
            code = _next_code(shot, f"{CODE_PREFIX.get(params.get('kind'), 'gen')}_")
            path = output
            origin = Origin.GENERATED

        info = probe(path)
        version = Version(shot=shot, code=code, origin=origin, status=VersionStatus.IN_REVIEW, parent=parent,
                          path=str(path), sha256=sha256_file(path), bytes=path.stat().st_size,
                          width=info.width, height=info.height, fps=info.fps or None,
                          frame_count=info.frame_count, duration_s=info.duration_s or None,
                          video_codec=info.video_codec, has_audio=info.has_audio, generation_job_id=job.id)
        session.add(version)
        session.flush()
        params["registered"] = {**params.get("registered", {}), str(index): version.id}
        job.params = params
        log_activity(session, actor, "generation_registered", version, job_id=job.id, output=index)
        run_qc(session, version, actor=actor)
        return version.id


def protection_for(version: Version) -> dict[str, int] | None:
    """Independent re-check used by QC: compare the fix version with its source frame outside the mask."""
    job = version.generation_job
    if job is None or (job.params or {}).get("purpose") != "fix":
        return None
    fix = job.params["fix"]
    source = fixlib.imread(Path(fix["frame_path"]))
    fixed = fixlib.imread(Path(version.path))
    mask = fixlib.imread_gray(Path(fix["mask_path"]))
    if source.shape != fixed.shape:
        return {"outside_pixels": int((mask == 0).sum()), "outside_changed": -1, "inside_pixels": 0, "inside_changed": 0}
    return fixlib.protection_check(source, fixed, mask)


def frame_png(version: Version, frame: int) -> bytes:
    image = fixlib.imread(Path(version.path)) if is_image(version.path) else read_frame(Path(version.path), frame)
    ok, buf = cv2.imencode(".png", image)
    if not ok:
        raise ValueError("Frame encode failed")
    return buf.tobytes()
