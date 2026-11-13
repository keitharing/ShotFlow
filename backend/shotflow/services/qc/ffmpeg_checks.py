"""Black, freeze, silence and loudness checks from a single FFmpeg pass."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..media import run_ffmpeg
from .thresholds import QCThresholds


@dataclass
class Segment:
    start_s: float
    end_s: float

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


@dataclass
class FFmpegReport:
    black: list[Segment] = field(default_factory=list)
    freeze: list[Segment] = field(default_factory=list)
    silence: list[Segment] = field(default_factory=list)
    integrated_lufs: float | None = None
    true_peak_dbfs: float | None = None


_BLACK = re.compile(r"black_start:([\d.]+)\s+black_end:([\d.]+)")
_FREEZE_START = re.compile(r"freeze_start:\s*([\d.]+)")
_FREEZE_END = re.compile(r"freeze_end:\s*([\d.]+)")
_SIL_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SIL_END = re.compile(r"silence_end:\s*([\d.]+)")
_LUFS = re.compile(r"Integrated loudness:\s*\n\s*I:\s*(-?[\d.]+|-inf)\s*LUFS")
_PEAK = re.compile(r"True peak:\s*\n\s*Peak:\s*(-?[\d.]+|-inf)\s*dBFS")


def _pair(starts: list[float], ends: list[float], duration_s: float) -> list[Segment]:
    # A segment still open at end of file has no end marker; close it at the clip duration.
    segments = []
    for i, start in enumerate(starts):
        end = ends[i] if i < len(ends) else duration_s
        segments.append(Segment(max(0.0, start), end))
    return segments


def _number(text: str) -> float | None:
    return None if text == "-inf" else float(text)


def analyze(path: Path, duration_s: float, has_audio: bool, th: QCThresholds) -> FFmpegReport:
    args = [
        "-nostats",
        "-i",
        str(path),
        "-vf",
        f"blackdetect=d={th.black_min_s}:pix_th={th.black_pixel_th},"
        f"freezedetect=n={th.freeze_noise_db}dB:d={th.freeze_min_s}",
    ]
    if has_audio:
        args += [
            "-af",
            f"silencedetect=n={th.silence_noise_db}dB:d={th.silence_min_s},ebur128=peak=true",
        ]
    args += ["-f", "null", "-"]
    result = run_ffmpeg(args)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg analysis failed: {result.stderr[-800:]}")
    log = result.stderr

    report = FFmpegReport()
    report.black = [Segment(float(a), float(b)) for a, b in _BLACK.findall(log)]
    report.freeze = _pair(
        [float(x) for x in _FREEZE_START.findall(log)],
        [float(x) for x in _FREEZE_END.findall(log)],
        duration_s,
    )
    if has_audio:
        report.silence = _pair(
            [float(x) for x in _SIL_START.findall(log)],
            [float(x) for x in _SIL_END.findall(log)],
            duration_s,
        )
        lufs = _LUFS.findall(log)
        peak = _PEAK.findall(log)
        report.integrated_lufs = _number(lufs[-1]) if lufs else None
        report.true_peak_dbfs = _number(peak[-1]) if peak else None
    return report
