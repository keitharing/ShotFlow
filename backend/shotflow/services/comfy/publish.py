"""Versions published from inside ComfyUI by the "ShotFlow에 등록" node."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from sqlalchemy.orm import Session

from ...config import Settings, writable_path
from ...models import GenerationJob, Note, Origin, Shot, Version, VersionStatus, log_activity
from ..media import probe, sha256_file
from ..qc import run_qc
from .runner import _next_code

MAX_FILES = 16
MODEL_KEYS = ("ckpt_name", "unet_name")


def _first(graph: dict[str, Any], class_types: tuple[str, ...], key: str) -> Any:
    for node in graph.values():
        if isinstance(node, dict) and node.get("class_type") in class_types and key in node.get("inputs", {}):
            value = node["inputs"][key]
            if not isinstance(value, list):
                return value
    return None


def _model(graph: dict[str, Any]) -> str | None:
    for node in graph.values():
        for key in MODEL_KEYS:
            value = (node.get("inputs") or {}).get(key) if isinstance(node, dict) else None
            if isinstance(value, str):
                return value
    return None


def register_published(session: Session, settings: Settings, shot: Shot, files: list[tuple[str, bytes]], *,
                       graph: dict[str, Any], workflow: Any, parent_id: int | None, note: str,
                       author: str) -> list[Version]:
    if shot.kind != "shot":
        raise ValueError("편집본 샷에는 등록할 수 없습니다")
    if not files or len(files) > MAX_FILES:
        raise ValueError(f"이미지는 1~{MAX_FILES}장이어야 합니다")
    parent = session.get(Version, parent_id) if parent_id else None
    if parent_id and (parent is None or parent.shot_id != shot.id):
        raise ValueError("부모 버전이 이 샷에 없습니다")

    seed = _first(graph, ("KSampler", "KSamplerAdvanced"), "seed")
    job = GenerationJob(
        provider="comfyui-node", status="completed", model=_model(graph),
        prompt=_first(graph, ("CLIPTextEncode",), "text"), seed=seed if isinstance(seed, int) else None,
        started_at=datetime.now(timezone.utc), finished_at=datetime.now(timezone.utc),
        params={"purpose": "publish", "recipe_name": "ComfyUI 직접 작업", "shot_id": shot.id,
                "source_version_id": parent.id if parent else None, "graph": graph,
                "workflow_saved": workflow is not None, "note": note, "outputs": [], "registered": {}},
    )
    session.add(job)
    session.flush()
    folder = writable_path(settings.data_dir / "published" / f"job{job.id:05d}" / ".keep", settings).parent
    if workflow is not None:  # the editable UI graph, so the artist can reopen exactly this setup
        (folder / "workflow.json").write_text(json.dumps(workflow, ensure_ascii=False), encoding="utf-8")
    (folder / "graph.json").write_text(json.dumps(graph, ensure_ascii=False, indent=1), encoding="utf-8")

    versions, outputs = [], []
    for index, (_, payload) in enumerate(files):
        if cv2.imdecode(np.frombuffer(payload, np.uint8), cv2.IMREAD_COLOR) is None:
            raise ValueError(f"{index + 1}번째 파일이 이미지가 아닙니다")
        path = folder / f"out_{index:03d}.png"
        path.write_bytes(payload)
        outputs.append(str(path))
        info = probe(path)
        version = Version(shot=shot, code=_next_code(shot, "pub_"), origin=Origin.GENERATED,
                          status=VersionStatus.IN_REVIEW, parent=parent, path=str(path), sha256=sha256_file(path),
                          bytes=path.stat().st_size, width=info.width, height=info.height, frame_count=1,
                          video_codec=info.video_codec, has_audio=False, generation_job=job)
        session.add(version)
        session.flush()
        if note.strip():
            session.add(Note(version=version, author=author, body=note.strip(), decision="comment", source="comfyui"))
        run_qc(session, version, actor=author)
        versions.append(version)
    job.params = {**job.params, "outputs": outputs, "registered": {str(i): v.id for i, v in enumerate(versions)}}
    log_activity(session, author, "comfyui_published", shot, job_id=job.id, versions=[v.id for v in versions])
    return versions


def catalog_versions(session: Session) -> list[dict[str, Any]]:
    rows = (session.query(Version).join(Shot, Version.shot_id == Shot.id)
            .filter(Shot.kind == "shot").order_by(Shot.sequence_id, Shot.order, Version.id))
    return [{"id": v.id, "label": f"{v.shot.sequence.name} · {v.shot.code} · {v.code} · #{v.id}",
             "media_kind": "image" if Path(v.path).suffix.lower() in (".png", ".jpg", ".jpeg") else "video"}
            for v in rows]


def catalog_shots(session: Session) -> list[dict[str, Any]]:
    rows = session.query(Shot).filter(Shot.kind == "shot").order_by(Shot.sequence_id, Shot.order)
    return [{"id": s.id, "label": f"{s.sequence.name} · {s.code} · #{s.id}"} for s in rows]
