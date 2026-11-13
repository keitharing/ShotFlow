"""Prompt management: shared text blocks per film + one scene text per shot and use.

A film keeps its shared wording once, as blocks: the look, a palette, how to read the guide image, things to avoid.
A shot keeps only what is special about it (the scene text) and which blocks it uses, separately for each use
(a start image, a start image made on this PC, a video). The full prompt is always composed, never typed twice:

    blocks placed before the scene + scene text + blocks placed after the scene        (negative blocks are kept apart)

When a generation uses a composed prompt, the exact text is frozen as a snapshot and the job points at it.
Editing a block later changes the next prompt of every shot that uses it, and never the record of what an earlier
result was made from.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from sqlalchemy.orm import Session

from ..models import GenerationJob, PromptBlock, PromptSnapshot, Sequence, Shot, ShotPrompt, log_activity, utcnow

USES = {"image": "그림", "image_local": "그림 (로컬)", "video": "영상"}
PLACES = {"before": "장면 앞", "after": "장면 뒤", "negative": "넣지 않을 것"}
KEY = re.compile(r"^[a-z0-9_]{1,40}$")


def _check_use(use: str) -> str:
    if use not in USES:
        raise ValueError(f"쓰임새는 {', '.join(USES)} 중 하나여야 합니다")
    return use


def blocks(session: Session, sequence: Sequence) -> list[PromptBlock]:
    return (session.query(PromptBlock).filter_by(sequence_id=sequence.id)
            .order_by(PromptBlock.order, PromptBlock.id).all())


def set_block(session: Session, sequence: Sequence, key: str, *, label: str, text: str, place: str = "after",
              order: int | None = None, actor: str = "ui") -> PromptBlock:
    if not KEY.match(key or ""):
        raise ValueError("묶음 이름(key)은 영어 소문자 · 숫자 · 밑줄로 40자까지 적습니다")
    if place not in PLACES:
        raise ValueError(f"자리는 {', '.join(PLACES)} 중 하나여야 합니다")
    if not text.strip():
        raise ValueError("묶음의 글이 비어 있습니다")
    block = session.query(PromptBlock).filter_by(sequence_id=sequence.id, key=key).one_or_none()
    if block is None:
        last = max((b.order for b in blocks(session, sequence)), default=0)
        block = PromptBlock(sequence_id=sequence.id, key=key, order=last + 1)
        session.add(block)
    changed = block.text != text.strip()
    block.label, block.text, block.place = (label.strip() or key), text.strip(), place
    if order is not None:
        block.order = order
    block.updated_at = utcnow()
    session.flush()
    log_activity(session, actor, "prompt_block", block, sequence_id=sequence.id, key=key, text_changed=changed)
    return block


def delete_block(session: Session, sequence: Sequence, key: str, actor: str = "ui") -> None:
    block = session.query(PromptBlock).filter_by(sequence_id=sequence.id, key=key).one_or_none()
    if block is None:
        raise ValueError("그런 묶음이 없습니다")
    users = sorted({f"{p.shot.code}({USES[p.use]})" for p in _shot_prompts(session, sequence) if key in (p.block_keys or [])})
    if users:
        raise ValueError(f"이 묶음을 쓰는 컷이 있습니다: {', '.join(users[:8])}{' …' if len(users) > 8 else ''}. 먼저 컷에서 빼세요")
    log_activity(session, actor, "prompt_block_delete", block, sequence_id=sequence.id, key=key)
    session.delete(block)
    session.flush()


def _shot_prompts(session: Session, sequence: Sequence) -> list[ShotPrompt]:
    return session.query(ShotPrompt).join(Shot, ShotPrompt.shot_id == Shot.id).filter(Shot.sequence_id == sequence.id).all()


def shot_prompt(session: Session, shot: Shot, use: str) -> ShotPrompt | None:
    return session.query(ShotPrompt).filter_by(shot_id=shot.id, use=use).one_or_none()


def set_shot_prompt(session: Session, shot: Shot, use: str, *, scene: str, block_keys: list[str],
                    note: str | None = None, actor: str = "ui") -> ShotPrompt:
    _check_use(use)
    if shot.kind != "shot":
        raise ValueError("편집본에는 프롬프트를 둘 수 없습니다")
    known = {b.key for b in blocks(session, shot.sequence)}
    unknown = [k for k in block_keys if k not in known]
    if unknown:
        raise ValueError(f"없는 묶음입니다: {', '.join(unknown)}")
    row = shot_prompt(session, shot, use)
    if row is None:
        row = ShotPrompt(shot_id=shot.id, use=use)
        session.add(row)
    row.scene, row.block_keys, row.note = scene.strip(), list(dict.fromkeys(block_keys)), (note or "").strip() or None
    row.updated_at = utcnow()
    session.flush()
    return row


def apply_blocks(session: Session, sequence: Sequence, use: str, *, add: list[str] | None = None,
                 remove: list[str] | None = None, shot_ids: list[int] | None = None, actor: str = "ui") -> int:
    """Put blocks into (or take them out of) many shots at once: the look of a film is set in one step."""
    _check_use(use)
    add, remove = list(add or []), set(remove or [])
    known = {b.key for b in blocks(session, sequence)}
    unknown = [k for k in add if k not in known]
    if unknown:
        raise ValueError(f"없는 묶음입니다: {', '.join(unknown)}")
    changed = 0
    for shot in sequence.shots:
        if shot.kind != "shot" or (shot_ids is not None and shot.id not in shot_ids):
            continue
        row = shot_prompt(session, shot, use)
        before = list(row.block_keys or []) if row else []
        after = [k for k in dict.fromkeys(before + add) if k not in remove]
        if after != before:
            set_shot_prompt(session, shot, use, scene=row.scene if row else "", block_keys=after,
                            note=row.note if row else None, actor=actor)
            changed += 1
    log_activity(session, actor, "prompt_apply_blocks", sequence, use=use, add=add, remove=sorted(remove), shots=changed)
    return changed


def compose(session: Session, shot: Shot, use: str) -> dict[str, Any]:
    _check_use(use)
    row = shot_prompt(session, shot, use)
    keys = set(row.block_keys or []) if row else set()
    chosen = [b for b in blocks(session, shot.sequence) if b.key in keys]
    scene = (row.scene if row else "") or ""
    parts = [{"key": b.key, "label": b.label, "place": "before", "text": b.text} for b in chosen if b.place == "before"]
    if scene:
        parts.append({"key": "@scene", "label": "장면", "place": "scene", "text": scene})
    parts += [{"key": b.key, "label": b.label, "place": "after", "text": b.text} for b in chosen if b.place == "after"]
    negative = [b for b in chosen if b.place == "negative"]
    text = " ".join(p["text"] for p in parts)
    return {"use": use, "scene": scene, "block_keys": [b.key for b in chosen], "note": row.note if row else None,
            "text": text, "negative": ", ".join(b.text for b in negative), "chars": len(text),
            "parts": parts + [{"key": b.key, "label": b.label, "place": "negative", "text": b.text} for b in negative],
            "scene_missing": not scene, "updated_at": row.updated_at.isoformat() if row and row.updated_at else None}


def snapshot(session: Session, shot: Shot, use: str) -> PromptSnapshot:
    """Freeze the prompt as it reads now. The same text gives the same snapshot, so repeats do not pile up."""
    made = compose(session, shot, use)
    if not made["text"]:
        raise ValueError(f"{shot.code}의 {USES[use]} 프롬프트가 비어 있습니다 (프롬프트 화면에서 먼저 적으세요)")
    digest = hashlib.sha256(f"{use}\n{made['text']}\n--\n{made['negative']}".encode("utf-8")).hexdigest()
    snap = session.query(PromptSnapshot).filter_by(shot_id=shot.id, sha256=digest).one_or_none()
    if snap is None:
        snap = PromptSnapshot(shot_id=shot.id, use=use, sha256=digest, text=made["text"], negative=made["negative"] or None,
                              parts=made["parts"])
        session.add(snap)
        session.flush()
    return snap


def match(session: Session, shot: Shot | None, text: str | None) -> PromptSnapshot | None:
    """If a prompt typed or pasted into a generation is exactly a prompt kept here, link it."""
    wanted = (text or "").strip()
    if shot is None or shot.kind != "shot" or not wanted:
        return None
    for use in USES:
        if compose(session, shot, use)["text"] == wanted:
            return snapshot(session, shot, use)
    return None


def _job_brief(job: GenerationJob) -> dict[str, Any]:
    p = job.params or {}
    registered = p.get("registered") or {}
    return {"id": job.id, "provider": job.provider, "model": job.model, "status": job.status, "purpose": p.get("purpose"),
            "recipe_name": p.get("recipe_name"), "credits": job.credits,
            "version_ids": [v for v in registered.values() if v],
            "created_at": job.created_at.isoformat() if job.created_at else None}


def history(session: Session, shot: Shot, use: str | None = None) -> list[dict[str, Any]]:
    """Every frozen prompt of a shot, newest first, with the generations that used it."""
    query = session.query(PromptSnapshot).filter_by(shot_id=shot.id)
    if use:
        query = query.filter_by(use=use)
    snaps = query.order_by(PromptSnapshot.id.desc()).all()
    jobs: dict[int, list[GenerationJob]] = {}
    if snaps:
        rows = session.query(GenerationJob).filter(GenerationJob.prompt_snapshot_id.in_([s.id for s in snaps]))
        for job in rows.order_by(GenerationJob.id):
            jobs.setdefault(job.prompt_snapshot_id, []).append(job)
    return [{"id": s.id, "use": s.use, "sha256": s.sha256, "text": s.text, "negative": s.negative, "parts": s.parts,
             "created_at": s.created_at.isoformat() if s.created_at else None,
             "jobs": [_job_brief(j) for j in jobs.get(s.id, [])]} for s in snaps]


def overview(session: Session, sequence: Sequence) -> dict[str, Any]:
    used: dict[tuple[int, str], int] = {}
    snaps = session.query(PromptSnapshot).join(Shot, PromptSnapshot.shot_id == Shot.id).filter(Shot.sequence_id == sequence.id).all()
    by_id = {s.id: s for s in snaps}
    if by_id:
        for job in session.query(GenerationJob).filter(GenerationJob.prompt_snapshot_id.in_(list(by_id))):
            snap = by_id[job.prompt_snapshot_id]
            used[(snap.shot_id, snap.use)] = used.get((snap.shot_id, snap.use), 0) + 1
    shots = []
    for shot in sequence.shots:
        if shot.kind != "shot":
            continue
        prompts = {}
        for use in USES:
            made = compose(session, shot, use)
            made["jobs"] = used.get((shot.id, use), 0)
            prompts[use] = made
        shots.append({"shot_id": shot.id, "code": shot.code, "description": shot.description, "prompts": prompts})
    return {"sequence": {"id": sequence.id, "code": sequence.code, "name": sequence.name},
            "uses": [{"key": k, "label": v} for k, v in USES.items()],
            "places": [{"key": k, "label": v} for k, v in PLACES.items()],
            "blocks": [block_out(b) for b in blocks(session, sequence)], "shots": shots}


def block_out(block: PromptBlock) -> dict[str, Any]:
    return {"id": block.id, "key": block.key, "label": block.label, "place": block.place, "order": block.order,
            "text": block.text, "chars": len(block.text),
            "updated_at": block.updated_at.isoformat() if block.updated_at else None}
