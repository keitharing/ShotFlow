"""Storyboard animatics: store the file, split it into cuts, find the drawing on each page (decision D-015).

Cuts are found with the hidden-cut rule of D-002 (a difference spike in a single frame), with a lower minimum:
storyboard pages keep the header and caption, so a new drawing changes only part of the frame (FIRST DROP v03:
12.0 and 14.6 at the real cuts, 0.3 between them). Cross-fades between drawings of one cut spread over several
frames and are not cuts.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from sqlalchemy.orm import Session

from ..config import Settings, load_settings, writable_path
from ..models import Animatic, Sequence, log_activity
from .media import probe, sha256_file
from .qc.frame_checks import analyze
from .qc.thresholds import DEFAULT

SUFFIXES = {".mp4", ".mov", ".webm", ".mkv", ".avi"}
MAX_BYTES = 500 * 1024 * 1024
CUT_MIN_MOTION = 4.0
MIN_CUT_FRAMES = 6          # shorter pieces are merged into the previous cut
PANEL_MIN_AREA = 0.15       # a drawing area smaller than this part of the frame is text (a title page)


def detect_cuts(path: Path) -> tuple[list[tuple[int, int]], int]:
    """[(start, end)] frame ranges (end exclusive) and the frame count."""
    report = analyze(path, replace(DEFAULT, cut_min_motion=CUT_MIN_MOTION))
    n = report.frame_count
    bounds = [0]
    for frame in report.cut_frames:
        if frame - bounds[-1] >= MIN_CUT_FRAMES:
            bounds.append(frame)
    if n - bounds[-1] < MIN_CUT_FRAMES and len(bounds) > 1:
        bounds.pop()
    bounds.append(n)
    return [(a, b) for a, b in zip(bounds, bounds[1:])], n


def find_panel(frame: np.ndarray) -> list[int] | None:
    """Drawing area [x, y, w, h]: the largest bright rectangle (paper on a dark page), the whole frame for a normal
    full-frame animatic, None for a page without a drawing (a title card)."""
    h, w = frame.shape[:2]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    m = max(2, round(min(w, h) * 0.02))
    border = np.concatenate([gray[:m].ravel(), gray[-m:].ravel(), gray[:, :m].ravel(), gray[:, -m:].ravel()])
    if np.median(border) > 60:
        return [0, 0, w, h]  # no dark page around it: the picture fills the frame
    bright = cv2.morphologyEx((gray > 200).astype(np.uint8), cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    contours, _ = cv2.findContours(bright, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    x, y, cw, ch = cv2.boundingRect(max(contours, key=cv2.contourArea))
    if cw * ch < PANEL_MIN_AREA * w * h or cw < 0.3 * w:
        return None
    return [int(x), int(y), int(cw), int(ch)]


def read_frame(path: Path, index: int) -> np.ndarray:
    capture = cv2.VideoCapture(str(path))
    try:
        capture.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, frame = capture.read()
    finally:
        capture.release()
    if not ok:
        raise ValueError(f"{index}번 프레임을 읽을 수 없습니다")
    return frame


def _representative(start: int, end: int) -> dict[str, int]:
    """Frames that show the first and the last drawing of a cut (a cut can hold several drawings)."""
    n = end - start
    return {"start": start + min(n // 2, max(2, round(n * 0.15))), "end": end - 1 - min(n // 2, max(2, round(n * 0.15)))}


def import_animatic(session: Session, sequence: Sequence, filename: str, payload: bytes,
                    settings: Settings | None = None, actor: str = "ui") -> Animatic:
    settings = settings or load_settings()
    suffix = Path(filename or "").suffix.lower()
    if suffix not in SUFFIXES:
        raise ValueError("영상 파일(mp4, mov, webm)을 올려 주세요")
    if not payload or len(payload) > MAX_BYTES:
        raise ValueError("파일이 비었거나 500MB를 넘습니다")
    folder = settings.data_dir / "animatics" / f"seq{sequence.id:04d}"
    n = 1 + sum(1 for p in folder.glob("animatic_*") if p.is_file()) if folder.is_dir() else 1
    path = writable_path(folder / f"animatic_{n:03d}{suffix}", settings)
    path.write_bytes(payload)
    try:
        info = probe(path)
        if not info.frame_count or not info.width:
            raise ValueError
        ranges, frame_count = detect_cuts(path)
    except ValueError as exc:
        path.unlink(missing_ok=True)
        raise ValueError("영상으로 읽을 수 없는 파일입니다") from exc

    shots = [s for s in sequence.shots if s.kind == "shot"]
    cuts, panels, k = [], [], 0
    for i, (start, end) in enumerate(ranges):
        frames = _representative(start, end)
        panel = find_panel(read_frame(path, frames["start"]))
        has_panel = panel is not None
        if has_panel:
            panels.append(panel)
        shot_id = shots[k].id if has_panel and k < len(shots) else None
        k += has_panel
        cuts.append({"index": i, "start": start, "end": end, "has_panel": has_panel, "shot_id": shot_id,
                     "frames": frames})
    crop = [int(np.median([p[j] for p in panels])) for j in range(4)] if panels else [0, 0, info.width, info.height]

    animatic = Animatic(sequence=sequence, path=str(path), original_name=Path(filename).name[:200],
                        sha256=sha256_file(path), width=info.width, height=info.height, fps=info.fps or 24.0,
                        frame_count=frame_count, crop=crop, cuts=cuts)
    session.add(animatic)
    session.flush()
    log_activity(session, actor, "animatic_imported", animatic, sequence=sequence.code, cuts=len(cuts),
                 drawings=sum(c["has_panel"] for c in cuts), original_name=animatic.original_name)
    return animatic


def update_cuts(session: Session, animatic: Animatic, changes: list[dict[str, Any]], actor: str = "ui") -> Animatic:
    """Link cuts to shots and pick which frames show the first / last drawing."""
    shot_ids = {s.id for s in animatic.sequence.shots if s.kind == "shot"}
    cuts = [dict(c) for c in animatic.cuts]
    for change in changes:
        index = change.get("index")
        if not isinstance(index, int) or not 0 <= index < len(cuts):
            raise ValueError("컷 번호를 확인하세요")
        cut = cuts[index]
        if "shot_id" in change:
            if change["shot_id"] is not None and change["shot_id"] not in shot_ids:
                raise ValueError("이 영상의 샷만 연결할 수 있습니다")
            cut["shot_id"] = change["shot_id"]
        for role in ("start", "end"):
            frame = (change.get("frames") or {}).get(role)
            if frame is not None:
                if not cut["start"] <= int(frame) < cut["end"]:
                    raise ValueError(f"{index + 1}번 컷의 프레임은 {cut['start']}~{cut['end'] - 1} 사이여야 합니다")
                cut["frames"] = {**cut["frames"], role: int(frame)}
    animatic.cuts = cuts
    log_activity(session, actor, "animatic_cuts_updated", animatic, changes=len(changes))
    return animatic


def frame_image(animatic: Animatic, frame: int, settings: Settings | None = None) -> Path:
    """The drawing area of one frame as JPEG (cached next to the animatic)."""
    settings = settings or load_settings()
    if not 0 <= frame < animatic.frame_count:
        raise ValueError("프레임 번호를 확인하세요")
    source = Path(animatic.path)
    out = settings.data_dir / "cache" / "animatics" / f"a{animatic.id:04d}" / f"f{frame:05d}.jpg"
    if not out.is_file():
        x, y, w, h = animatic.crop
        image = read_frame(source, frame)[y:y + h, x:x + w]
        out = writable_path(out, settings)
        ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 90])
        if not ok:
            raise ValueError("그림을 저장할 수 없습니다")
        out.write_bytes(encoded.tobytes())  # cv2.imwrite cannot write to non-ASCII paths on Windows (this data folder)
    return out


def animatic_out(animatic: Animatic) -> dict[str, Any]:
    x, y, w, h = animatic.crop
    return {"id": animatic.id, "sequence_id": animatic.sequence_id, "original_name": animatic.original_name,
            "width": animatic.width, "height": animatic.height, "fps": animatic.fps,
            "frame_count": animatic.frame_count, "crop": animatic.crop, "aspect": round(w / h, 5),
            "cuts": animatic.cuts, "created_at": animatic.created_at.isoformat() if animatic.created_at else None}
