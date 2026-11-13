from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from shotflow.config import load_settings
from shotflow.services.media import ffmpeg_exe

W, H, FPS = 192, 108, 24


def moving_frames(n: int, seed: int = 0) -> np.ndarray:
    """Smoothly drifting gradient with mild noise: behaves like a clean, slowly moving shot."""
    rng = np.random.default_rng(seed)
    x = np.linspace(0, 1, W)[None, :]
    y = np.linspace(0, 1, H)[:, None]
    frames = []
    for i in range(n):
        phase = i / 48.0
        r = 90 + 60 * np.sin(2 * np.pi * (x + phase))
        g = 110 + 40 * np.cos(2 * np.pi * (y + phase * 0.7))
        b = 100 + 30 * np.sin(2 * np.pi * (x * 0.5 + y * 0.5 + phase))
        img = np.stack([b + 0 * y, g + 0 * x, r + 0 * y], axis=-1)
        img = img + rng.normal(0, 1.5, img.shape)
        frames.append(np.clip(img, 0, 255).astype(np.uint8))
    return np.stack(frames)


def other_scene(n: int) -> np.ndarray:
    frames = moving_frames(n, seed=7)[:, :, ::-1]  # swap colour channels and flip -> different shot
    return np.ascontiguousarray(frames[:, ::-1, :] * 0.6 + 60).astype(np.uint8)


def write_video(path: Path, frames: np.ndarray, audio: str | None = "tone") -> Path:
    """Encode BGR frames to H.264 MP4. audio: 'tone', 'gap' (silence 0.5-1.5s) or None."""
    n = len(frames)
    duration = n / FPS
    cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-"]
    if audio == "tone":
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={duration}"]
    elif audio == "gap":
        cmd += ["-f", "lavfi", "-i",
                f"aevalsrc='0.3*sin(2*PI*440*t)*(1-between(t,0.5,1.5))':s=48000:d={duration}"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "18", "-g", "1"]
    if audio:
        cmd += ["-c:a", "aac", "-shortest"]
    cmd.append(str(path))
    subprocess.run(cmd, input=frames.tobytes(), check=True)
    return path


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("SHOTFLOW_DATA", str(tmp_path / "data"))
    return load_settings()


@pytest.fixture
def real_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("SHOTFLOW_DATA", str(tmp_path / "data"))
    s = load_settings()
    if not (s.production_v04 / "05_Records" / "Selected_Timeline.json").is_file():
        pytest.skip("Production v0.4 records are not on this machine")
    return replace(s)
