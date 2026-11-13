"""Register new sequences (films) and shots by hand.

Until week 6 sequences only came from the v0.4 importer. New work (films made from scratch
inside ShotFlow) needs a way in: this module creates the rows, the API and CLI call it.
"""

from __future__ import annotations

from pathlib import Path

import re
from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy.orm import Session

from ..config import Settings, load_settings, writable_path
from ..models import Edit, Note, Origin, Project, Sequence, Shot, Version, VersionStatus, log_activity

SEQUENCE_CODE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
SHOT_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,15}$")
RESERVED_SHOT_CODES = {"EDIT"}  # the per-sequence shot that holds assembled cuts


def _clean_shots(shots: Iterable[Mapping[str, Any]]) -> list[tuple[str, str | None]]:
    cleaned: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for entry in shots:
        code = str(entry.get("code", "")).strip()
        if not SHOT_CODE.match(code):
            raise ValueError(f"샷 코드는 영문 대문자로 시작하고 대문자·숫자·밑줄만 씁니다 (16자 이내): {code!r}")
        if code in RESERVED_SHOT_CODES:
            raise ValueError(f"샷 코드 {code}는 편집본 자리로 예약되어 있습니다")
        if code in seen:
            raise ValueError(f"샷 코드가 겹칩니다: {code}")
        seen.add(code)
        description = entry.get("description")
        cleaned.append((code, str(description).strip() or None if description is not None else None))
    return cleaned


def create_sequence(session: Session, project: Project, *, code: str, name: str,
                    target_duration_s: float | None = None,
                    shots: Iterable[Mapping[str, Any]] = (), actor: str = "user") -> Sequence:
    """Create a sequence with its shots. Raises ValueError with a Korean message on bad input."""
    code = code.strip()
    name = name.strip()
    if not SEQUENCE_CODE.match(code):
        raise ValueError(f"영상 코드는 영문 대문자로 시작하고 대문자·숫자·밑줄만 씁니다 (2~64자): {code!r}")
    if not name:
        raise ValueError("영상 이름이 비어 있습니다")
    if target_duration_s is not None and target_duration_s <= 0:
        raise ValueError("목표 길이는 0보다 커야 합니다")
    if any(s.code == code for s in project.sequences):
        raise ValueError(f"같은 코드의 영상이 이미 있습니다: {code}")
    cleaned = _clean_shots(shots)
    if not cleaned:
        raise ValueError("샷이 하나 이상 필요합니다")

    order = max((s.order for s in project.sequences), default=-1) + 1
    sequence = Sequence(project=project, code=code, name=name, order=order, target_duration_s=target_duration_s)
    for shot_order, (shot_code, description) in enumerate(cleaned):
        Shot(sequence=sequence, code=shot_code, order=shot_order, description=description)
    session.add(sequence)
    session.flush()
    log_activity(session, actor, "sequence.create", sequence, code=code, shots=[c for c, _ in cleaned])
    return sequence


def add_shot(session: Session, sequence: Sequence, *, code: str, description: str | None = None,
             actor: str = "user") -> Shot:
    """Append one shot to an existing sequence."""
    (shot_code, shot_description), = _clean_shots([{"code": code, "description": description}])
    if any(s.code == shot_code for s in sequence.shots):
        raise ValueError(f"같은 코드의 샷이 이미 있습니다: {shot_code}")
    order = max((s.order for s in sequence.shots if s.kind == "shot"), default=-1) + 1
    shot = Shot(sequence=sequence, code=shot_code, order=order, description=shot_description)
    session.add(shot)
    session.flush()
    log_activity(session, actor, "shot.create", shot, sequence=sequence.code, code=shot_code)
    return shot


# ---------- week 7: film size, first edit, images made outside ShotFlow ----------
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
MAX_IMAGE_BYTES = 50 * 1024 * 1024
VIDEO_SUFFIXES = {".mp4", ".mov"}          # a clip finished outside ShotFlow (e.g. effect layers composited over a generated clip)
MAX_VIDEO_BYTES = 500 * 1024 * 1024
DEFAULT_EDIT_SETTINGS = {"fade_in_s": 0.12, "fade_out_s": 0.25}


def set_frame_size(session: Session, sequence: Sequence, width: int | None, height: int | None,
                   actor: str = "user") -> Sequence:
    """Give one film its own delivery size (for example 1280x720). None/None goes back to the project size."""
    if (width is None) != (height is None):
        raise ValueError("가로와 세로를 함께 적어 주세요 (둘 다 비우면 프로젝트 규격을 씁니다)")
    if width is not None:
        if not (16 <= width <= 7680 and 16 <= height <= 4320):
            raise ValueError("규격은 가로 16~7680, 세로 16~4320 사이여야 합니다")
        if width % 2 or height % 2:
            raise ValueError("가로와 세로는 짝수여야 합니다 (영상 인코딩 규칙)")
    sequence.width, sequence.height = width, height
    session.flush()
    log_activity(session, actor, "sequence.frame_size", sequence, width=width, height=height)
    return sequence


def rename_sequence(session: Session, sequence: Sequence, name: str, *, actor: str = "user") -> Sequence:
    """Change the display name of a film. Its code stays, because file names and paths are built from the code."""
    name = (name or "").strip()
    if not name:
        raise ValueError("영상 이름을 적어 주세요")
    if len(name) > 120:
        raise ValueError("영상 이름은 120자까지입니다")
    old, sequence.name = sequence.name, name
    session.flush()
    log_activity(session, actor, "sequence.rename", sequence, old=old, new=name)
    return sequence


def create_edit(session: Session, sequence: Sequence, *, name: str, actor: str = "user") -> Edit:
    """Start an empty edit for a film registered by hand (imported films already have one)."""
    name = (name or "").strip()
    if not name:
        raise ValueError("편집본 이름을 적어 주세요")
    if len(name) > 80:
        raise ValueError("편집본 이름은 80자까지입니다")
    if any(e.name == name for e in sequence.edits):
        raise ValueError(f"같은 이름의 편집본이 이미 있습니다: {name}")
    edit = Edit(sequence=sequence, name=name, settings=dict(DEFAULT_EDIT_SETTINGS))
    session.add(edit)
    session.flush()
    log_activity(session, actor, "edit.create", edit, sequence=sequence.code, name=name)
    return edit


def import_image(session: Session, shot: Shot, filename: str, payload: bytes, *, parent: Version | None = None,
                 note: str = "", actor: str = "user", settings: Settings | None = None) -> Version:
    """Register a still or a clip made or finished outside ShotFlow as a version of a shot, then run QC on it.

    A still becomes an imported version (ext_NNN). A clip becomes an FX version (fx_NNN): it is normally a
    generated clip with effect layers composited on top, so its parent should be that generated version.
    """
    from .comfy.runner import _next_code
    from .media import probe, sha256_file
    from .qc import run_qc

    settings = settings or load_settings()
    if shot.kind != "shot":
        raise ValueError("편집본에는 그림을 등록할 수 없습니다 (샷을 고르세요)")
    suffix = Path(filename or "").suffix.lower()
    is_video = suffix in VIDEO_SUFFIXES
    if suffix not in IMAGE_SUFFIXES and not is_video:
        raise ValueError("그림 파일(png, jpg, webp)이나 영상 파일(mp4, mov)을 올려 주세요")
    if not payload or len(payload) > (MAX_VIDEO_BYTES if is_video else MAX_IMAGE_BYTES):
        raise ValueError(f"파일이 비었거나 {500 if is_video else 50}MB를 넘습니다")
    if parent is not None and parent.shot_id != shot.id:
        raise ValueError("원본 버전이 이 샷의 버전이 아닙니다")
    code = _next_code(shot, "fx_" if is_video else "ext_")
    path = writable_path(settings.data_dir / "imports" / f"shot{shot.id:05d}" / f"{code}{suffix}", settings)
    path.write_bytes(payload)
    try:
        info = probe(path)
    except ValueError as exc:
        path.unlink(missing_ok=True)
        raise ValueError("그림이나 영상으로 읽을 수 없는 파일입니다") from exc
    version = Version(shot=shot, code=code, origin=Origin.FX if is_video else Origin.IMPORTED, status=VersionStatus.IN_REVIEW, parent=parent,
                      path=str(path), sha256=sha256_file(path), bytes=path.stat().st_size,
                      width=info.width, height=info.height, fps=(info.fps or None) if is_video else None,
                      frame_count=info.frame_count, duration_s=(info.duration_s or None) if is_video else None,
                      video_codec=info.video_codec, has_audio=info.has_audio if is_video else False)
    session.add(version)
    session.flush()
    if note.strip():
        session.add(Note(version=version, author=actor, body=note.strip(), decision="comment", source="user"))
    log_activity(session, actor, "video_imported" if is_video else "image_imported", version, original_name=Path(filename).name,
                 parent=parent.code if parent else None)
    run_qc(session, version, actor=actor)
    return version
