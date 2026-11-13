"""Mask fix with crop & stitch.

A 1080p frame is too large for SD1.5 on 6GB, so only the masked region (plus context) is sent at work
resolution. The result is pasted back with a feather that stays *inside* the mask, so every pixel outside
the mask is byte-identical to the source frame - and protection_check() proves it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np

from ...config import Settings, writable_path

MIN_CROP = 192
CONTEXT = 0.6  # extra context around the mask box, relative to its size
FEATHER_PX = 6
FEATHER_RATIO = 0.04  # feather grows with the edited area so large patches don't show a hard rim
TONE_LOCK = 0.85  # how much of the original low-frequency tone is restored inside the mask (D-006)


@dataclass
class FixPrep:
    frame_path: str
    mask_path: str
    crop_path: str
    crop_mask_path: str
    box: list[int]  # x0, y0, x1, y1 in frame pixels
    work_size: list[int]  # width, height sent to ComfyUI
    frame_size: list[int]
    mask_pixels: int
    tone_lock: float = TONE_LOCK
    feather_px: int = FEATHER_PX

    def as_dict(self) -> dict:
        return asdict(self)


def imread(path: Path) -> np.ndarray:
    data = np.fromfile(str(path), dtype=np.uint8)  # cv2.imread cannot open non-ASCII paths on Windows
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Cannot read image {path}")
    return image


def imread_gray(path: Path) -> np.ndarray:
    image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ValueError(f"Cannot read image {path}")
    return image


def imwrite(path: Path, image: np.ndarray) -> Path:
    ok, buf = cv2.imencode(path.suffix or ".png", image)
    if not ok:
        raise ValueError(f"Cannot encode {path}")
    buf.tofile(str(path))
    return path


def decode_mask(png_bytes: bytes, size: tuple[int, int]) -> np.ndarray:
    """Browser mask PNG (painted pixels opaque) -> uint8 0/255 at frame size."""
    raw = cv2.imdecode(np.frombuffer(png_bytes, np.uint8), cv2.IMREAD_UNCHANGED)
    if raw is None:
        raise ValueError("Mask is not a valid PNG")
    if raw.ndim == 3 and raw.shape[2] == 4:
        plane = raw[:, :, 3]
    elif raw.ndim == 3:
        plane = raw.max(axis=2)
    else:
        plane = raw
    plane = cv2.resize(plane, size, interpolation=cv2.INTER_NEAREST)
    return np.where(plane > 127, 255, 0).astype(np.uint8)


def _box(mask: np.ndarray, frame_w: int, frame_h: int) -> list[int]:
    ys, xs = np.nonzero(mask)
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()) + 1, int(ys.min()), int(ys.max()) + 1
    w, h = x1 - x0, y1 - y0
    side = max(MIN_CROP, int(max(w, h) * (1 + CONTEXT)))
    side = min(side, frame_w, frame_h)
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    bx0 = int(np.clip(cx - side // 2, 0, frame_w - side))
    by0 = int(np.clip(cy - side // 2, 0, frame_h - side))
    return [bx0, by0, bx0 + side, by0 + side]


def prepare(frame: np.ndarray, mask_png: bytes, out_dir: Path, work: int, settings: Settings) -> FixPrep:
    h, w = frame.shape[:2]
    mask = decode_mask(mask_png, (w, h))
    pixels = int((mask > 0).sum())
    if pixels == 0:
        raise ValueError("마스크가 비어 있습니다")
    x0, y0, x1, y1 = _box(mask, w, h)
    work = int(work // 8 * 8)
    crop = cv2.resize(frame[y0:y1, x0:x1], (work, work), interpolation=cv2.INTER_LANCZOS4)
    crop_mask = cv2.resize(mask[y0:y1, x0:x1], (work, work), interpolation=cv2.INTER_NEAREST)
    out = writable_path(out_dir / "frame.png", settings).parent
    paths = {name: out / f"{name}.png" for name in ("frame", "mask", "crop", "crop_mask")}
    imwrite(paths["frame"], frame)
    imwrite(paths["mask"], mask)
    imwrite(paths["crop"], crop)
    imwrite(paths["crop_mask"], cv2.merge([crop_mask, crop_mask, crop_mask]))
    ys, xs = np.nonzero(mask)
    feather = max(FEATHER_PX, int(min(np.ptp(xs) + 1, np.ptp(ys) + 1) * FEATHER_RATIO))
    return FixPrep(str(paths["frame"]), str(paths["mask"]), str(paths["crop"]), str(paths["crop_mask"]),
                   [x0, y0, x1, y1], [work, work], [w, h], pixels, TONE_LOCK, feather)


def inner_alpha(mask: np.ndarray, feather_px: int = FEATHER_PX) -> np.ndarray:
    """0..1 blend weight that is exactly 0 outside the mask and softens only inward."""
    binary = (mask > 0).astype(np.uint8)
    dist = cv2.distanceTransform(binary, cv2.DIST_L2, 5)
    return np.clip(dist / max(1, feather_px), 0.0, 1.0).astype(np.float32)


def tone_match(patch: np.ndarray, region: np.ndarray, strength: float) -> np.ndarray:
    """Keep the generated detail but pull its low-frequency tone back to the source (frequency separation).

    SD redraws the whole crop with a slightly different base colour; that offset is what makes a fixed
    area read as a box. Replacing only the blurred component removes the offset and keeps new detail.
    """
    if strength <= 0:
        return patch
    sigma = max(4.0, min(patch.shape[:2]) / 16)
    low_patch = cv2.GaussianBlur(patch, (0, 0), sigma)
    low_region = cv2.GaussianBlur(region, (0, 0), sigma)
    return patch + strength * (low_region - low_patch)


def stitch(prep: FixPrep, result: np.ndarray, out_path: Path, settings: Settings) -> Path:
    frame = imread(Path(prep.frame_path))
    mask = imread_gray(Path(prep.mask_path))
    x0, y0, x1, y1 = prep.box
    region = frame[y0:y1, x0:x1].astype(np.float32)
    patch = cv2.resize(result, (x1 - x0, y1 - y0), interpolation=cv2.INTER_LANCZOS4).astype(np.float32)
    patch = np.clip(tone_match(patch, region, prep.tone_lock), 0, 255)
    alpha = inner_alpha(mask, prep.feather_px)[y0:y1, x0:x1, None]
    blended = np.rint(region * (1 - alpha) + patch * alpha).astype(np.uint8)
    out = frame.copy()
    out[y0:y1, x0:x1] = np.where(alpha > 0, blended, frame[y0:y1, x0:x1])
    return imwrite(writable_path(out_path, settings), out)


def protection_check(source: np.ndarray, fixed: np.ndarray, mask: np.ndarray) -> dict[str, int]:
    """Count pixels outside the mask that differ from the source (must be 0)."""
    outside = mask == 0
    changed = np.any(source != fixed, axis=2) & outside
    inside_changed = np.any(source != fixed, axis=2) & ~outside
    return {"outside_pixels": int(outside.sum()), "outside_changed": int(changed.sum()),
            "inside_pixels": int((~outside).sum()), "inside_changed": int(inside_changed.sum())}
