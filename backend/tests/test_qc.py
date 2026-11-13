from __future__ import annotations

import numpy as np
import pytest

from shotflow.services.media import probe
from shotflow.services.qc import analyze_file, overall_status
from shotflow.services.qc.frame_checks import find_cuts, find_flicker
from shotflow.services.qc.thresholds import DEFAULT

from .conftest import FPS, H, W, moving_frames, other_scene, write_video


def checks(findings, severity=None):
    return [f.check for f in findings if severity is None or f.severity == severity]


def test_probe_reads_stream_info(tmp_path):
    path = write_video(tmp_path / "clip.mp4", moving_frames(48))
    info = probe(path)
    assert (info.width, info.height) == (W, H)
    assert info.fps == pytest.approx(FPS)
    assert info.frame_count == 48
    assert info.duration_s == pytest.approx(2.0)
    assert info.video_codec == "h264"
    assert info.has_audio


def test_clean_clip_has_no_warnings(tmp_path):
    path = write_video(tmp_path / "clean.mp4", moving_frames(72))
    findings, measurements = analyze_file(path, expected_width=W, expected_height=H, expected_fps=FPS)
    assert overall_status(findings) == "pass", [f.message for f in findings]
    assert measurements["frames_decoded"] == 72
    assert measurements["detected_cuts"] == []


def test_spec_mismatch_is_error(tmp_path):
    path = write_video(tmp_path / "clip.mp4", moving_frames(24))
    findings, _ = analyze_file(path, expected_width=1920, expected_height=1080, expected_fps=FPS)
    assert "spec" in checks(findings, "error")


def test_black_segment_in_middle_is_error(tmp_path):
    frames = moving_frames(72)
    frames[24:36] = 0
    findings, _ = analyze_file(write_video(tmp_path / "black.mp4", frames))
    black = [f for f in findings if f.check == "black"]
    assert len(black) == 1 and black[0].severity == "error"
    assert 22 <= black[0].start_frame <= 26 and 34 <= black[0].end_frame <= 38


def test_short_black_at_start_is_treated_as_fade(tmp_path):
    frames = moving_frames(72)
    frames[:4] = 0
    findings, _ = analyze_file(write_video(tmp_path / "fade.mp4", frames))
    black = [f for f in findings if f.check == "black"]
    assert black and all(f.severity == "info" for f in black)


def test_long_freeze_is_warning(tmp_path):
    frames = moving_frames(96)
    frames[24:60] = frames[24]
    findings, _ = analyze_file(write_video(tmp_path / "freeze.mp4", frames))
    freeze = [f for f in findings if f.check == "freeze"]
    assert freeze and freeze[0].severity == "warning"
    assert freeze[0].value == pytest.approx(1.5, abs=0.15)


def test_silence_gap_is_warning(tmp_path):
    findings, measurements = analyze_file(write_video(tmp_path / "gap.mp4", moving_frames(72), audio="gap"))
    silence = [f for f in findings if f.check == "silence"]
    assert len(silence) == 1 and silence[0].severity == "warning"
    assert silence[0].value == pytest.approx(1.0, abs=0.1)
    assert measurements["integrated_lufs"] is not None


def test_missing_audio_is_info(tmp_path):
    findings, _ = analyze_file(write_video(tmp_path / "mute.mp4", moving_frames(24), audio=None))
    assert "audio" in checks(findings, "info")


def test_single_bright_frame_is_flicker_not_cut(tmp_path):
    frames = moving_frames(72).astype(np.int16)
    frames[30] = np.clip(frames[30] + 70, 0, 255)
    findings, measurements = analyze_file(write_video(tmp_path / "flicker.mp4", frames.astype(np.uint8)))
    flicker = [f for f in findings if f.check == "flicker"]
    assert [f.start_frame for f in flicker] == [30]
    assert measurements["detected_cuts"] == []


def test_hidden_cut_inside_shot_is_warning(tmp_path):
    frames = np.concatenate([moving_frames(36), other_scene(36)])
    findings, measurements = analyze_file(write_video(tmp_path / "cut.mp4", frames))
    assert measurements["detected_cuts"] == [36]
    assert [f.start_frame for f in findings if f.check == "hidden_cut"] == [36]


def test_expected_cut_from_edl_is_not_flagged(tmp_path):
    frames = np.concatenate([moving_frames(36), other_scene(36)])
    findings, _ = analyze_file(write_video(tmp_path / "edit.mp4", frames), expected_cuts=[36])
    assert "hidden_cut" not in checks(findings)
    assert "expected_cut" not in checks(findings)


def test_missing_expected_cut_is_warning(tmp_path):
    findings, _ = analyze_file(write_video(tmp_path / "edit.mp4", moving_frames(72)), expected_cuts=[36])
    assert "expected_cut" in checks(findings, "warning")


def test_fade_ramp_is_not_a_cut():
    luma = np.concatenate([np.linspace(0, 120, 4), np.full(40, 120.0)])
    motion = np.concatenate([[0.0], np.abs(np.diff(luma))]) + 0.5
    assert find_cuts(motion, luma, [], DEFAULT) == []


def test_flicker_requires_return():
    step = np.concatenate([np.full(10, 100.0), np.full(10, 140.0)])
    assert find_flicker(step, DEFAULT) == []
