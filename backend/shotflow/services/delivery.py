"""Delivery packages: approved versions copied under a naming rule, with manifest and checksums, re-verifiable."""

from __future__ import annotations

import csv
import io
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ..config import Settings, writable_path
from ..models import Delivery, DeliveryItem, Sequence, Shot, Version, VersionStatus, log_activity
from .media import sha256_file

NAMING_RULE = "{project}_{sequence}_{shot}_{version}{ext}"
SCOPES = ("approved_shots", "approved_edits", "versions")


def _slug(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z.\-]+", "_", text).strip("_") or "item"


def file_name(version: Version) -> str:
    shot = version.shot
    return NAMING_RULE.format(project=_slug(shot.sequence.project.code), sequence=_slug(shot.sequence.code),
                              shot=_slug(shot.code), version=_slug(version.code), ext=Path(version.path).suffix.lower())


def collect(session: Session, scope: str, sequence_ids: list[int] | None = None,
            version_ids: list[int] | None = None) -> list[Version]:
    """Only approved versions are deliverable; anything else is refused rather than silently skipped."""
    if scope not in SCOPES:
        raise ValueError(f"Unknown scope {scope}")
    if scope == "versions":
        versions = [session.get(Version, vid) for vid in version_ids or []]
        missing = [vid for vid, v in zip(version_ids or [], versions) if v is None]
        if missing:
            raise ValueError(f"없는 버전: {missing}")
        not_approved = [f"{v.shot.code}/{v.code}" for v in versions if v.status != VersionStatus.APPROVED]
        if not_approved:
            raise ValueError(f"승인되지 않은 버전은 납품할 수 없습니다: {', '.join(not_approved)}")
    else:
        kind = "shot" if scope == "approved_shots" else "edit"
        query = session.query(Shot).join(Sequence).filter(Shot.kind == kind)
        if sequence_ids:
            query = query.filter(Shot.sequence_id.in_(sequence_ids))
        versions = [s.approved_version for s in query.order_by(Sequence.order, Shot.order) if s.approved_version]
    if not versions:
        raise ValueError("납품할 승인 버전이 없습니다")
    return versions


def _summary(version: Version) -> dict[str, Any]:
    run = version.qc_runs[-1] if version.qc_runs else None
    job = version.generation_job
    p = (job.params or {}) if job else {}
    return {
        "version": {"id": version.id, "sequence": version.shot.sequence.name, "shot": version.shot.code,
                    "code": version.code, "origin": version.origin, "status": version.status,
                    "parent_id": version.parent_id, "width": version.width, "height": version.height,
                    "fps": version.fps, "frames": version.frame_count, "duration_s": version.duration_s},
        "qc": {"status": run.status if run else None,
               "errors": sum(1 for i in run.issues if i.severity == "error") if run else None,
               "warnings": sum(1 for i in run.issues if i.severity == "warning") if run else None},
        "generation": None if job is None else {
            "provider": job.provider, "model": job.model, "seed": job.seed, "prompt": job.prompt,
            "credits": job.credits, "recipe": p.get("recipe_name"), "recipe_version": p.get("recipe_version"),
            "external_id": job.external_id},
    }


def create(session: Session, settings: Settings, name: str, versions: list[Version], actor: str = "ui") -> Delivery:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    folder = writable_path(settings.data_dir / "deliveries" / f"{_slug(name)}_{stamp}" / ".keep", settings).parent
    names = [file_name(v) for v in versions]
    if len(set(names)) != len(names):
        raise ValueError("이름 규칙으로 만든 파일 이름이 겹칩니다")

    delivery = Delivery(name=name, naming_rule=NAMING_RULE, target_dir=str(folder))
    entries = []
    for version, fname in zip(versions, names):
        target = folder / fname
        shutil.copyfile(version.path, target)
        digest = sha256_file(target)
        if version.sha256 and digest != version.sha256:
            raise RuntimeError(f"{fname}: 복사본 해시가 원본과 다릅니다")
        delivery.items.append(DeliveryItem(version_id=version.id, file_name=fname, sha256=digest))
        entries.append({"file": fname, "sha256": digest, "bytes": target.stat().st_size, **_summary(version)})

    manifest = {"delivery": name, "created_at_utc": stamp, "naming_rule": NAMING_RULE, "files": len(entries),
                "items": entries}
    (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (folder / "checksums.sha256").write_text("".join(f"{e['sha256']}  {e['file']}\n" for e in entries), encoding="utf-8")
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["file", "sequence", "shot", "version", "status", "qc", "width", "height", "frames", "sha256"])
    for e in entries:
        v = e["version"]
        writer.writerow([e["file"], v["sequence"], v["shot"], v["code"], v["status"], e["qc"]["status"],
                         v["width"], v["height"], v["frames"], e["sha256"]])
    (folder / "manifest.csv").write_text(buffer.getvalue(), encoding="utf-8-sig")  # BOM so Excel shows Korean

    delivery.verified = True
    session.add(delivery)
    session.flush()
    log_activity(session, actor, "delivery_created", delivery, files=len(entries), folder=str(folder))
    return delivery


def verify(session: Session, delivery: Delivery, actor: str = "ui") -> dict[str, Any]:
    """Re-hash every delivered file against the recorded checksum (detects missing or altered files)."""
    folder = Path(delivery.target_dir)
    results = []
    for item in delivery.items:
        path = folder / item.file_name
        if not path.is_file():
            results.append({"file": item.file_name, "status": "missing"})
            continue
        actual = sha256_file(path)
        results.append({"file": item.file_name, "status": "ok" if actual == item.sha256 else "changed",
                        "expected": item.sha256, "actual": actual})
    recorded = {i.file_name for i in delivery.items}
    extra = sorted(p.name for p in folder.iterdir()
                   if p.is_file() and p.name not in recorded and p.name not in ("manifest.json", "manifest.csv", "checksums.sha256"))
    ok = all(r["status"] == "ok" for r in results)
    delivery.verified = ok
    log_activity(session, actor, "delivery_verified", delivery, ok=ok, extra=extra)
    return {"ok": ok, "checked": len(results), "results": results, "extra_files": extra,
            "verified_at": datetime.now(timezone.utc).isoformat()}
