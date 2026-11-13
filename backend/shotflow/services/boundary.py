"""Cut boundary sheets: last frame of each shot next to the first frame of the next (decision D-001)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ..config import Settings, load_settings, writable_path
from ..models import Edit
from .media import delta_e, read_frame
from .qc.thresholds import DEFAULT, QCThresholds

THUMB_W = 480
LABEL_H = 34
GAP = 12
FONT_CANDIDATES = [r"C:\Windows\Fonts\malgunbd.ttf", r"C:\Windows\Fonts\malgun.ttf"]


@dataclass
class BoundaryInfo:
    order: int
    from_shot: str
    to_shot: str
    out_frame: int
    in_frame: int
    delta_e: float
    flagged: bool


def _font(size: int) -> ImageFont.ImageFont:
    for candidate in FONT_CANDIDATES:
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def _thumb(frame_bgr: np.ndarray) -> Image.Image:
    h, w = frame_bgr.shape[:2]
    small = cv2.resize(frame_bgr, (THUMB_W, round(h * THUMB_W / w)), interpolation=cv2.INTER_AREA)
    return Image.fromarray(cv2.cvtColor(small, cv2.COLOR_BGR2RGB))


def boundaries(edit: Edit, th: QCThresholds = DEFAULT) -> tuple[list[BoundaryInfo], list[tuple[Image.Image, Image.Image]]]:
    infos, pairs = [], []
    for order, (a, b) in enumerate(zip(edit.entries, edit.entries[1:])):
        out_frame = read_frame(Path(a.version.path), a.src_out - 1)
        in_frame = read_frame(Path(b.version.path), b.src_in)
        de = delta_e(out_frame, in_frame)
        infos.append(BoundaryInfo(order, a.version.shot.code, b.version.shot.code, a.src_out - 1, b.src_in,
                                  round(de, 2), de > th.boundary_delta_e_info))
        pairs.append((_thumb(out_frame), _thumb(in_frame)))
    return infos, pairs


def render_sheet(edit: Edit, settings: Settings | None = None, th: QCThresholds = DEFAULT) -> tuple[Path, list[BoundaryInfo]]:
    settings = settings or load_settings()
    infos, pairs = boundaries(edit, th)
    if not pairs:
        raise ValueError("Edit needs at least two entries for a boundary sheet")
    thumb_h = pairs[0][0].height
    row_h = LABEL_H + thumb_h
    width = THUMB_W * 2 + GAP * 3
    sheet = Image.new("RGB", (width, GAP + len(pairs) * (row_h + GAP)), (23, 27, 34))
    draw = ImageDraw.Draw(sheet)
    font, small = _font(18), _font(14)
    for row, (info, (left, right)) in enumerate(zip(infos, pairs)):
        y = GAP + row * (row_h + GAP)
        color = (255, 200, 120) if info.flagged else (225, 232, 242)
        draw.text((GAP, y + 6), f"컷 {row + 1}:  {info.from_shot}  →  {info.to_shot}", font=font, fill=color)
        draw.text((width - GAP - 260, y + 9), f"평균색 차이 ΔE {info.delta_e:.1f}", font=small, fill=color)
        sheet.paste(left, (GAP, y + LABEL_H))
        sheet.paste(right, (GAP * 2 + THUMB_W, y + LABEL_H))
        draw.text((GAP + 8, y + LABEL_H + 6), f"{info.from_shot} 끝 f{info.out_frame}", font=small, fill=(255, 255, 255))
        draw.text((GAP * 2 + THUMB_W + 8, y + LABEL_H + 6), f"{info.to_shot} 시작 f{info.in_frame}", font=small, fill=(255, 255, 255))
    sequence = edit.sequence
    target = writable_path(settings.data_dir / "sheets" / f"{sequence.code}_edit{edit.id}_boundaries.jpg", settings)
    sheet.save(target, quality=90)
    return target, infos
