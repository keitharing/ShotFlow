"""Browser-friendly copies of source media: 720p H.264 proxies and thumbnails, cached under data/cache."""

from __future__ import annotations

import threading
from pathlib import Path

import cv2
import numpy as np

from ..config import Settings, load_settings, writable_path
from ..models import Version
from .media import is_image, read_frame, run_ffmpeg

PROXY_HEIGHT = 720
THUMB_WIDTH = 384
_lock = threading.Lock()  # proxies can be requested by several browser tabs at once


def _key(version: Version) -> str:
    return f"{version.id}_{(version.sha256 or 'nohash')[:12]}"


def proxy_path(version: Version, settings: Settings | None = None) -> Path:
    settings = settings or load_settings()
    return settings.cache_dir / "proxy" / f"{_key(version)}.mp4"


def thumbnail_path(version: Version, settings: Settings | None = None) -> Path:
    settings = settings or load_settings()
    return settings.cache_dir / "thumbs" / f"{_key(version)}.jpg"


def ensure_proxy(version: Version, settings: Settings | None = None) -> Path:
    settings = settings or load_settings()
    if is_image(version.path):  # stills are already browser-friendly
        return Path(version.path)
    target = proxy_path(version, settings)
    if target.is_file():
        return target
    with _lock:
        if target.is_file():
            return target
        out = writable_path(target, settings)
        tmp = out.with_suffix(".part.mp4")
        # Keyframe every 6 frames so frame stepping in the browser stays responsive.
        args = ["-y", "-loglevel", "error", "-i", version.path,
                "-vf", f"scale=-2:{PROXY_HEIGHT}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-g", "6", "-bf", "0", "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
        args += ["-c:a", "aac", "-b:a", "160k"] if version.has_audio else ["-an"]
        result = run_ffmpeg([*args, str(tmp)])
        if result.returncode != 0:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"Proxy encode failed: {result.stderr[-500:]}")
        tmp.replace(out)
    return target


def ensure_thumbnail(version: Version, settings: Settings | None = None) -> Path:
    settings = settings or load_settings()
    target = thumbnail_path(version, settings)
    if target.is_file():
        return target
    if is_image(version.path):
        frame = cv2.imdecode(np.fromfile(version.path, dtype=np.uint8), cv2.IMREAD_COLOR)
    else:
        middle = max(0, (version.frame_count or 1) // 2)
        frame = read_frame(Path(version.path), middle)
    h, w = frame.shape[:2]
    small = cv2.resize(frame, (THUMB_WIDTH, round(h * THUMB_WIDTH / w)), interpolation=cv2.INTER_AREA)
    out = writable_path(target, settings)
    ok, encoded = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise RuntimeError("Thumbnail encode failed")
    out.write_bytes(encoded.tobytes())  # cv2.imwrite cannot handle non-ASCII paths on Windows
    return target
