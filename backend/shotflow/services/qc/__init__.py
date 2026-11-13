from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from ...models import QCIssue, QCRun, Severity, Version, log_activity
from ..media import is_image, probe
from . import ffmpeg_checks, frame_checks
from .image_checks import analyze_image
from .thresholds import DEFAULT, QCThresholds

CUT_TOLERANCE_FRAMES = 2


@dataclass
class Finding:
    check: str
    severity: str
    message: str
    start_frame: int | None = None
    end_frame: int | None = None
    value: float | None = None
    threshold: float | None = None


def analyze_file(
    path: Path,
    *,
    expected_width: int | None = None,
    expected_height: int | None = None,
    expected_fps: float | None = None,
    target_lufs: float | None = None,
    expected_cuts: list[int] | None = None,
    static_ranges: list[list[int]] | None = None,
    th: QCThresholds = DEFAULT,
) -> tuple[list[Finding], dict]:
    """Run every automatic check on one media file. Pure function: no database access."""
    info = probe(path)
    fps = info.fps or expected_fps or 24.0

    def frame(seconds: float) -> int:
        return int(round(seconds * fps))

    findings: list[Finding] = []

    spec_problems = []
    if expected_width and (info.width, info.height) != (expected_width, expected_height):
        spec_problems.append(f"해상도 {info.width}x{info.height} (기준 {expected_width}x{expected_height})")
    if expected_fps and abs(info.fps - expected_fps) > 0.01:
        spec_problems.append(f"fps {info.fps:g} (기준 {expected_fps:g})")
    for problem in spec_problems:
        findings.append(Finding("spec", Severity.ERROR, f"규격 불일치: {problem}"))

    ff = ffmpeg_checks.analyze(path, info.duration_s, info.has_audio, th)
    for seg in ff.black:
        at_edge = seg.start_s < 0.05 or seg.end_s > info.duration_s - 0.05
        if at_edge and seg.duration_s < th.edge_fade_max_s:
            findings.append(Finding("black", Severity.INFO, f"시작/끝 {seg.duration_s:.2f}초 검은 화면 (페이드로 추정)",
                                    frame(seg.start_s), frame(seg.end_s), seg.duration_s, th.edge_fade_max_s))
        else:
            findings.append(Finding("black", Severity.ERROR, f"검은 화면 {seg.duration_s:.2f}초",
                                    frame(seg.start_s), frame(seg.end_s), seg.duration_s, th.black_min_s))
    for seg in ff.freeze:
        long_freeze = seg.duration_s >= th.freeze_warning_s
        findings.append(Finding(
            "freeze",
            Severity.WARNING if long_freeze else Severity.INFO,
            f"멈춘 화면 {seg.duration_s:.2f}초" + ("" if long_freeze else " (짧은 정지, 의도된 정지 샷일 수 있음)"),
            frame(seg.start_s), frame(seg.end_s), seg.duration_s, th.freeze_warning_s,
        ))

    if not info.has_audio:
        findings.append(Finding("audio", Severity.INFO, "오디오 트랙 없음"))
    else:
        for seg in ff.silence:
            findings.append(Finding("silence", Severity.WARNING, f"무음 {seg.duration_s:.2f}초",
                                    frame(seg.start_s), frame(seg.end_s), seg.duration_s, th.silence_min_s))
        if ff.true_peak_dbfs is not None and ff.true_peak_dbfs > th.true_peak_max_dbfs:
            findings.append(Finding("clipping", Severity.WARNING, f"트루 피크 {ff.true_peak_dbfs:.1f} dBFS (클리핑 위험)",
                                    value=ff.true_peak_dbfs, threshold=th.true_peak_max_dbfs))
        if target_lufs is not None and ff.integrated_lufs is not None:
            off = ff.integrated_lufs - target_lufs
            if abs(off) > th.loudness_tolerance_lu:
                findings.append(Finding("loudness", Severity.INFO,
                                        f"통합 음량 {ff.integrated_lufs:.1f} LUFS (목표 {target_lufs:g} ±{th.loudness_tolerance_lu:g})",
                                        value=ff.integrated_lufs, threshold=target_lufs))

    fr = frame_checks.analyze(path, th)
    for f in fr.flicker_frames:
        findings.append(Finding("flicker", Severity.WARNING, "밝기 깜빡임 (한두 프레임 튀었다 돌아옴)",
                                f, f + 1, float(fr.luma[f] - fr.luma[f - 1]), th.flicker_min_jump))
    expected = expected_cuts or []
    for cut in fr.cut_frames:
        if any(abs(cut - e) <= CUT_TOLERANCE_FRAMES for e in expected):
            continue
        label = "편집 기록에 없는 컷" if expected_cuts is not None else "샷 내부의 컷 (숨은 컷)"
        findings.append(Finding("hidden_cut", Severity.WARNING, label, cut, cut,
                                float(fr.motion[cut]), th.cut_min_motion))
    for e in expected:
        if not any(abs(cut - e) <= CUT_TOLERANCE_FRAMES for cut in fr.cut_frames):
            findings.append(Finding("expected_cut", Severity.WARNING,
                                    "편집 기록상 컷 위치에서 컷이 감지되지 않음 (편집 기록과 실제 영상 불일치 가능)", e, e))

    if static_ranges:
        _mark_intentional(findings, static_ranges)

    if fr.frame_count != info.frame_count:
        findings.append(Finding("decode", Severity.WARNING,
                                f"실제 디코딩 프레임 {fr.frame_count} / 파일 표기 {info.frame_count}",
                                value=fr.frame_count, threshold=info.frame_count))

    measurements = {
        "width": info.width, "height": info.height, "fps": info.fps,
        "frames_header": info.frame_count, "frames_decoded": fr.frame_count,
        "duration_s": round(info.duration_s, 4), "video_codec": info.video_codec,
        "has_audio": info.has_audio, "audio_codec": info.audio_codec,
        "integrated_lufs": ff.integrated_lufs, "true_peak_dbfs": ff.true_peak_dbfs,
        "mean_motion": round(float(fr.motion[1:].mean()), 3) if fr.frame_count > 1 else 0.0,
        "max_hist_distance": round(float(fr.hist_distance.max()), 3) if fr.frame_count else 0.0,
        "detected_cuts": fr.cut_frames, "expected_cuts": expected_cuts,
    }
    return findings, measurements


INTENTIONAL_TOLERANCE_FRAMES = 3


def _mark_intentional(findings: list[Finding], static_ranges: list[list[int]]) -> None:
    """Freeze/silence inside a planned still segment (end card) is by design: downgrade to info."""
    for f in findings:
        if f.check not in ("freeze", "silence") or f.start_frame is None or f.end_frame is None:
            continue
        for start, end in static_ranges:
            if f.start_frame >= start - INTENTIONAL_TOLERANCE_FRAMES and f.end_frame <= end + INTENTIONAL_TOLERANCE_FRAMES:
                f.severity = Severity.INFO
                f.message += " (편집 기록상 의도된 정지/무음 구간: 엔드카드)"
                break


def overall_status(findings: list[Finding]) -> str:
    severities = {f.severity for f in findings}
    if Severity.ERROR in severities:
        return "error"
    if Severity.WARNING in severities:
        return "warning"
    return "pass"


def render_info_for(version: Version) -> dict | None:
    """Assembly record saved by services.assemble for this exact movie (cuts, dissolves, end card)."""
    from ...models import Edit

    session = Session.object_session(version)
    if session is None or version.origin != "edit":
        return None
    for edit in session.query(Edit).filter(Edit.sequence_id == version.shot.sequence_id):
        render = ((edit.settings or {}).get("renders") or {}).get(str(version.id))
        if render is not None:
            return render
    return None


def expected_cuts_for(version: Version) -> list[int] | None:
    """For an assembled edit, the cut frames come from its EDL. Plain shots have none."""
    from ...models import Edit

    render = render_info_for(version)
    if render is not None:  # ShotFlow-assembled movie: only hard cuts are expected, not dissolves
        return list(render["cut_frames"])
    session = Session.object_session(version)
    edit = session.query(Edit).filter(Edit.result_version_id == version.id).one_or_none() if session else None
    if edit is None:
        return None
    cuts, position = [], 0
    for entry in edit.entries[:-1]:
        position += entry.frames
        cuts.append(position)
    return cuts


def run_qc(session: Session, version: Version, th: QCThresholds = DEFAULT, actor: str = "system") -> QCRun:
    project = version.shot.sequence.project
    width, height = version.shot.sequence.frame_size
    started = time.perf_counter()
    run = QCRun(version=version, status="failed", thresholds=th.as_dict())
    session.add(run)
    try:
        if is_image(version.path):  # keyframes and mask fixes are stills: image checks + mask protection
            from ..comfy.runner import protection_for

            findings, measurements = analyze_image(Path(version.path), (width, height),
                                                   protection_for(version))
        else:
            findings, measurements = analyze_file(
                Path(version.path),
                expected_width=width,
                expected_height=height,
                expected_fps=project.fps,
                target_lufs=project.target_lufs,
                expected_cuts=expected_cuts_for(version),
                static_ranges=(render_info_for(version) or {}).get("static_ranges"),
                th=th,
            )
    except Exception as exc:  # recorded on the run so one bad file does not stop a batch
        run.error = f"{type(exc).__name__}: {exc}"
        run.duration_s = time.perf_counter() - started
        log_activity(session, actor, "qc_failed", version, error=run.error)
        return run
    run.measurements = measurements
    run.status = overall_status(findings)
    run.duration_s = round(time.perf_counter() - started, 3)
    run.issues = [
        QCIssue(check=f.check, severity=f.severity, message=f.message, start_frame=f.start_frame,
                end_frame=f.end_frame, value=f.value, threshold=f.threshold)
        for f in sorted(findings, key=lambda f: (Severity.ORDER[f.severity], f.start_frame or 0))
    ]
    log_activity(session, actor, "qc_run", version, status=run.status, issues=len(run.issues))
    return run
