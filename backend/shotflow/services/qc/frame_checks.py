"""Per-frame analysis with OpenCV: flicker spikes, hidden cuts and motion level."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from .thresholds import QCThresholds


@dataclass
class FrameReport:
    frame_count: int
    luma: np.ndarray
    hist_distance: np.ndarray  # distance between frame i-1 and i (index 0 = 0)
    motion: np.ndarray  # mean abs luma diff between frame i-1 and i
    flicker_frames: list[int] = field(default_factory=list)
    cut_frames: list[int] = field(default_factory=list)


def _histogram(small_bgr: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(small_bgr, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1, 2], None, [16, 8, 8], [0, 180, 0, 256, 0, 256])
    cv2.normalize(hist, hist)
    return hist


def analyze(path: Path, th: QCThresholds) -> FrameReport:
    capture = cv2.VideoCapture(str(path))
    luma: list[float] = []
    dist: list[float] = [0.0]
    motion: list[float] = [0.0]
    prev_hist = prev_gray = None
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            h, w = frame.shape[:2]
            small = cv2.resize(frame, (th.analysis_width, max(1, round(h * th.analysis_width / w))))
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32)
            hist = _histogram(small)
            luma.append(float(gray.mean()))
            if prev_hist is not None:
                dist.append(float(cv2.compareHist(prev_hist, hist, cv2.HISTCMP_BHATTACHARYYA)))
                motion.append(float(np.abs(gray - prev_gray).mean()))
            prev_hist, prev_gray = hist, gray
    finally:
        capture.release()

    report = FrameReport(
        frame_count=len(luma),
        luma=np.array(luma),
        hist_distance=np.array(dist[: len(luma)]),
        motion=np.array(motion[: len(luma)]),
    )
    report.flicker_frames = find_flicker(report.luma, th)
    report.cut_frames = find_cuts(report.motion, report.luma, report.flicker_frames, th)
    return report


def find_flicker(luma: np.ndarray, th: QCThresholds) -> list[int]:
    """A flicker is a brightness jump that comes back within 2 frames (a flash, not a fade or a cut)."""
    if len(luma) < 3:
        return []
    diffs = np.diff(luma)
    typical = float(np.median(np.abs(diffs))) or 0.01
    jump = max(th.flicker_min_jump, typical * th.flicker_sigma)
    frames: list[int] = []
    for i in range(1, len(luma) - 1):
        for back in (1, 2):
            j = i + back
            if j >= len(luma):
                continue
            up = luma[i] - luma[i - 1]
            down = luma[j] - luma[j - 1]
            returns = abs(luma[j] - luma[i - 1]) < jump / 2
            if abs(up) > jump and abs(down) > jump and np.sign(up) != np.sign(down) and returns:
                frames.append(i)
                break
    return frames


def motion_ratio(motion: np.ndarray, window: int) -> np.ndarray:
    """How many times larger each frame difference is than the median of its neighbours."""
    ratio = np.zeros_like(motion)
    for i in range(1, len(motion)):
        lo, hi = max(1, i - window), min(len(motion), i + window + 1)
        neighbours = np.concatenate([motion[lo:i], motion[i + 1:hi]])
        base = float(np.median(neighbours)) if len(neighbours) else 0.0
        ratio[i] = motion[i] / max(base, 0.05)
    return ratio


def find_cuts(motion: np.ndarray, luma: np.ndarray, flicker_frames: list[int], th: QCThresholds) -> list[int]:
    # Color histograms miss cuts between similar-looking shots (D-002), so use the frame-difference spike.
    flicker = set(flicker_frames) | {f + 1 for f in flicker_frames} | {f + 2 for f in flicker_frames}
    ratio = motion_ratio(motion, th.cut_window)
    cuts = []
    for i in range(1, len(motion)):
        if motion[i] < th.cut_min_motion or ratio[i] < th.cut_min_ratio or i in flicker:
            continue
        if min(luma[i - 1], luma[i]) < th.dark_luma:
            continue
        # A cut changes the picture in one frame; a fade or whip spreads the change over neighbours.
        before = motion[i - 1] if i > 1 else 0.0
        after = motion[i + 1] if i + 1 < len(motion) else 0.0
        if max(before, after) > motion[i] * 0.5:
            continue
        cuts.append(i)
    return cuts
