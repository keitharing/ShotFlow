from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np


@lru_cache(maxsize=1)
def ffmpeg_exe() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def run_ffmpeg(args: list[str], timeout: float = 600) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [ffmpeg_exe(), "-hide_banner", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def sha256_file(path: Path, chunk: int = 4 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class MediaInfo:
    width: int
    height: int
    fps: float
    frame_count: int
    duration_s: float
    video_codec: str
    has_audio: bool
    audio_codec: str | None
    audio_sample_rate: int | None


_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):([\d.]+)")
_VIDEO = re.compile(r"Stream #\S+.*?Video:\s*(\w+).*?,\s*(\d{2,5})x(\d{2,5})")
_FPS = re.compile(r"([\d.]+)\s*fps")
_AUDIO = re.compile(r"Stream #\S+.*?Audio:\s*(\w+).*?(\d{4,6})\s*Hz")


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def is_image(path: Path | str) -> bool:
    return Path(path).suffix.lower() in IMAGE_SUFFIXES


def _probe_image(path: Path) -> MediaInfo:
    image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    if image is None:
        raise ValueError(f"Cannot read image {path}")
    h, w = image.shape[:2]
    return MediaInfo(width=w, height=h, fps=0.0, frame_count=1, duration_s=0.0,
                     video_codec=Path(path).suffix.lower().lstrip("."), has_audio=False,
                     audio_codec=None, audio_sample_rate=None)


def probe(path: Path) -> MediaInfo:
    """Read stream info from ffmpeg's banner (no ffprobe on this machine) and exact frames from OpenCV."""
    if is_image(path):
        return _probe_image(Path(path))
    banner = run_ffmpeg(["-i", str(path)], timeout=60).stderr
    video = _VIDEO.search(banner)
    if not video:
        raise ValueError(f"No video stream: {path}")
    duration = _DURATION.search(banner)
    fps_match = _FPS.search(banner[video.start():].splitlines()[0])
    audio = _AUDIO.search(banner)
    fps = float(fps_match.group(1)) if fps_match else 0.0
    seconds = (
        int(duration.group(1)) * 3600 + int(duration.group(2)) * 60 + float(duration.group(3))
        if duration
        else 0.0
    )
    capture = cv2.VideoCapture(str(path))
    frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    capture.release()
    return MediaInfo(
        width=int(video.group(2)),
        height=int(video.group(3)),
        fps=fps,
        frame_count=frames,
        duration_s=frames / fps if fps and frames else seconds,
        video_codec=video.group(1),
        has_audio=audio is not None,
        audio_codec=audio.group(1) if audio else None,
        audio_sample_rate=int(audio.group(2)) if audio else None,
    )


def read_frame(path: Path, index: int) -> np.ndarray:
    capture = cv2.VideoCapture(str(path))
    try:
        capture.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, frame = capture.read()
    finally:
        capture.release()
    if not ok:
        raise ValueError(f"Cannot read frame {index} from {path}")
    return frame


def mean_lab(frame_bgr: np.ndarray) -> np.ndarray:
    lab = cv2.cvtColor(frame_bgr.astype(np.float32) / 255.0, cv2.COLOR_BGR2LAB)
    return lab.reshape(-1, 3).mean(axis=0)


def delta_e(frame_a: np.ndarray, frame_b: np.ndarray) -> float:
    return float(np.linalg.norm(mean_lab(frame_a) - mean_lab(frame_b)))
