"""Assemble an edit (EDL) into a movie with FFmpeg and register it as a new version of the sequence."""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy.orm import Session

from ..config import Settings, load_settings, writable_path
from ..models import Edit, EditEntry, Origin, Shot, Version, VersionStatus, log_activity
from .endcard import render_end_card
from .media import mean_lab, probe, read_frame, run_ffmpeg, sha256_file

AUDIO_RATE = 48000


@dataclass
class Segment:
    """One piece of the timeline: a trimmed clip or the end card."""

    label: str
    frames: int
    transition: str = "cut"  # how this segment joins the previous one
    transition_frames: int = 0
    path: str | None = None
    src_in: int = 0
    has_audio: bool = True
    color: dict[str, float] = field(default_factory=dict)
    is_card: bool = False


@dataclass
class Plan:
    segments: list[Segment]
    total_frames: int
    cut_frames: list[int]  # hard cuts only; dissolves are gradual and not cut-detectable
    dissolve_ranges: list[list[int]]
    static_ranges: list[list[int]]  # end card: intentionally still and silent
    fade_in_s: float
    fade_out_s: float


def build_plan(edit: Edit, fps: float, card_path: str | None = None) -> Plan:
    settings = edit.settings or {}
    segments = [
        Segment(label=e.version.shot.code, frames=e.frames, transition=e.transition if i else "cut",
                transition_frames=e.transition_frames if i and e.transition == "dissolve" else 0,
                path=e.version.path, src_in=e.src_in, has_audio=bool(e.version.has_audio),
                color=dict(e.color or {}))
        for i, e in enumerate(edit.entries)
    ]
    card = settings.get("end_card")
    if card and card_path:
        segments.append(Segment(label="END", frames=round(card.get("duration_s", 2.0) * fps),
                                transition=card.get("transition", "dissolve"),
                                transition_frames=card.get("transition_frames", 12)
                                if card.get("transition", "dissolve") == "dissolve" else 0,
                                path=card_path, is_card=True, has_audio=False))
    for seg in segments:
        if seg.frames <= 0:
            raise ValueError(f"{seg.label}: segment has no frames")
        if seg.transition_frames >= seg.frames:
            raise ValueError(f"{seg.label}: dissolve longer than the segment")

    position, cuts, dissolves, static = 0, [], [], []
    for i, seg in enumerate(segments):
        if i == 0:
            start = 0
        elif seg.transition == "dissolve":
            start = position - seg.transition_frames
            dissolves.append([start, position])
        else:
            start = position
            cuts.append(position)
        if seg.is_card:
            static.append([start, start + seg.frames])
        position = start + seg.frames
    return Plan(segments, position, cuts, dissolves, static,
                float(settings.get("fade_in_s", 0.0)), float(settings.get("fade_out_s", 0.0)))


def _eq(color: dict[str, float]) -> str:
    parts = [f"{k}={float(color[k]):.4f}" for k in ("brightness", "contrast", "saturation", "gamma") if k in color]
    return f",eq={':'.join(parts)}" if parts else ""


def build_ffmpeg_args(plan: Plan, out: Path, fps: float, width: int, height: int) -> list[str]:
    inputs: list[str] = []
    graph: list[str] = []
    fit = (f"fps={fps:g},scale={width}:{height}:force_original_aspect_ratio=decrease,"
           f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p,settb=AVTB")
    for i, seg in enumerate(plan.segments):
        seconds = seg.frames / fps
        if seg.is_card:
            # Feed half a second extra: an exact -t drops the last looped frame; trim below cuts it to size.
            inputs += ["-loop", "1", "-framerate", f"{fps:g}", "-t", f"{seconds + 0.5:.6f}", "-i", seg.path]
            # Slow push-in so the card is a designed hold, not a frozen frame.
            graph.append(f"[{i}:v]scale={width * 2}:{height * 2},"
                         f"zoompan=z='1+0.0008*on':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                         f"d=1:s={width}x{height}:fps={fps:g},trim=end_frame={seg.frames},"
                         f"setpts=PTS-STARTPTS,{fit}[v{i}]")
        else:
            inputs += ["-i", seg.path]
            graph.append(f"[{i}:v]trim=start_frame={seg.src_in}:end_frame={seg.src_in + seg.frames},"
                         f"setpts=PTS-STARTPTS,{fit}{_eq(seg.color)}[v{i}]")
        if seg.has_audio:
            start = seg.src_in / fps
            graph.append(f"[{i}:a]atrim=start={start:.6f}:end={start + seconds:.6f},asetpts=PTS-STARTPTS,"
                         f"aresample={AUDIO_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo,"
                         f"afade=t=in:d=0.04,afade=t=out:st={max(0.0, seconds - 0.06):.6f}:d=0.06[a{i}]")
        else:
            graph.append(f"anullsrc=r={AUDIO_RATE}:cl=stereo,atrim=duration={seconds:.6f}[a{i}]")

    acc_v, acc_a, acc_frames = "v0", "a0", plan.segments[0].frames
    for i, seg in enumerate(plan.segments[1:], start=1):
        nv, na = f"jv{i}", f"ja{i}"
        if seg.transition == "dissolve" and seg.transition_frames:
            d = seg.transition_frames / fps
            offset = (acc_frames - seg.transition_frames) / fps
            graph.append(f"[{acc_v}][v{i}]xfade=transition=fade:duration={d:.6f}:offset={offset:.6f}[{nv}]")
            graph.append(f"[{acc_a}][a{i}]acrossfade=d={d:.6f}[{na}]")
            acc_frames += seg.frames - seg.transition_frames
        else:
            graph.append(f"[{acc_v}][{acc_a}][v{i}][a{i}]concat=n=2:v=1:a=1[{nv}][{na}]")
            acc_frames += seg.frames
        acc_v, acc_a = nv, na

    total_s = plan.total_frames / fps
    vf, af = [], []
    if plan.fade_in_s:
        vf.append(f"fade=t=in:st=0:d={plan.fade_in_s}")
        af.append(f"afade=t=in:st=0:d={plan.fade_in_s}")
    if plan.fade_out_s:
        vf.append(f"fade=t=out:st={total_s - plan.fade_out_s:.6f}:d={plan.fade_out_s}")
        af.append(f"afade=t=out:st={total_s - plan.fade_out_s:.6f}:d={plan.fade_out_s}")
    graph.append(f"[{acc_v}]{','.join(vf) or 'null'},trim=end_frame={plan.total_frames}[outv]")
    graph.append(f"[{acc_a}]{','.join(af) or 'anull'},atrim=duration={total_s:.6f}[outa]")

    return ["-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(graph),
            "-map", "[outv]", "-map", "[outa]", "-r", f"{fps:g}",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)]


def suggest_brightness(edit: Edit, reference_shot: str, strength: float = 0.5, limit: float = 0.04) -> dict[str, float]:
    """Brightness offsets that pull each shot part-way toward the reference shot's lightness (CIE L*)."""
    def lightness(entry: EditEntry) -> float:
        frame = read_frame(Path(entry.version.path), entry.src_in + entry.frames // 2)
        return float(mean_lab(frame)[0])

    levels = {e.version.shot.code: lightness(e) for e in edit.entries}
    if reference_shot not in levels:
        raise ValueError(f"Reference shot {reference_shot} is not in the edit")
    ref = levels[reference_shot]
    return {code: round(float(np.clip((ref - level) / 100.0 * strength, -limit, limit)), 4)
            for code, level in levels.items()}


def _next_code(shot: Shot, wanted: str) -> str:
    taken = {v.code for v in shot.versions}
    if wanted not in taken:
        return wanted
    n = 2
    while f"{wanted}_r{n}" in taken:
        n += 1
    return f"{wanted}_r{n}"


def assemble(session: Session, edit: Edit, settings: Settings | None = None,
             version_code: str | None = None, actor: str = "system") -> Version:
    settings = settings or load_settings()
    sequence = edit.sequence
    project = sequence.project
    fps = project.fps
    if len(edit.entries) < 1:
        raise ValueError("Edit has no entries")
    edit_shot = next((s for s in sequence.shots if s.kind == "edit"), None)
    if edit_shot is None:
        edit_shot = Shot(sequence=sequence, code="EDIT", kind="edit", order=999, description="시퀀스 편집본")
        session.add(edit_shot)
        session.flush()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    card_path = None
    card = (edit.settings or {}).get("end_card")
    if card:
        last = edit.entries[-1]
        tone = read_frame(Path(last.version.path), last.src_out - 1).reshape(-1, 3).mean(axis=0)
        card_path = str(render_end_card(f"{sequence.code}_edit{edit.id}_{stamp}", card.get("title", sequence.name),
                                        card.get("subtitle", ""), tuple(tone), sequence.frame_size,
                                        settings))
    plan = build_plan(edit, fps, card_path)
    code = _next_code(edit_shot, version_code or edit.name)
    out = writable_path(settings.data_dir / "renders" / f"{sequence.code}_{code}_{stamp}.mp4", settings)

    started = time.perf_counter()
    result = run_ffmpeg(build_ffmpeg_args(plan, out, fps, *sequence.frame_size), timeout=1800)
    if result.returncode != 0:
        out.unlink(missing_ok=True)
        raise RuntimeError(f"Assembly failed: {result.stderr[-1200:]}")
    elapsed = round(time.perf_counter() - started, 2)

    info = probe(out)
    previous = edit.result_version
    version = Version(shot=edit_shot, code=code, origin=Origin.EDIT, status=VersionStatus.IN_REVIEW,
                      path=str(out), sha256=sha256_file(out), bytes=out.stat().st_size,
                      width=info.width, height=info.height, fps=info.fps, frame_count=info.frame_count,
                      duration_s=info.duration_s, video_codec=info.video_codec, has_audio=info.has_audio,
                      parent=previous or _base_result(session, edit))
    session.add(version)
    session.flush()

    render = {
        "version_id": version.id, "rendered_at": stamp, "seconds": elapsed,
        "planned_frames": plan.total_frames, "actual_frames": info.frame_count,
        "cut_frames": plan.cut_frames, "dissolve_ranges": plan.dissolve_ranges,
        "static_ranges": plan.static_ranges, "end_card": card_path,
        "entries": [{"shot": s.label, "frames": s.frames, "src_in": s.src_in, "transition": s.transition,
                     "transition_frames": s.transition_frames, "color": s.color} for s in plan.segments],
    }
    new_settings = copy.deepcopy(edit.settings or {})
    new_settings.setdefault("renders", {})[str(version.id)] = render
    edit.settings = new_settings  # reassign: JSON columns do not track in-place changes
    edit.result_version = version
    log_activity(session, actor, "assemble", edit, version_id=version.id, frames=info.frame_count, seconds=elapsed)
    if info.frame_count != plan.total_frames:
        log_activity(session, actor, "assemble_frame_mismatch", version,
                     planned=plan.total_frames, actual=info.frame_count)
    return version


def _base_result(session: Session, edit: Edit) -> Version | None:
    base_id = (edit.settings or {}).get("based_on_edit_id")
    if not base_id:
        return None
    base = session.get(Edit, base_id)
    return base.result_version if base else None


def clone_edit(session: Session, base: Edit, name: str, actor: str = "system") -> Edit:
    """New EDL starting from an existing one, pointing at each shot's currently approved version."""
    clone = Edit(sequence_id=base.sequence_id, name=name,
                 settings={k: v for k, v in copy.deepcopy(base.settings or {}).items()
                           if k not in ("renders", "boundary_sheet", "boundaries")} | {"based_on_edit_id": base.id})
    session.add(clone)  # add before attaching entries so autoflush never sees a half-built edit
    for entry in base.entries:
        approved = entry.version.shot.approved_version or entry.version
        clone.entries.append(EditEntry(order=entry.order, version=approved, src_in=entry.src_in,
                                       src_out=entry.src_out, transition=entry.transition,
                                       transition_frames=entry.transition_frames, color=dict(entry.color or {})))
    session.flush()
    session.refresh(clone)
    log_activity(session, actor, "edit_cloned", clone, base_edit_id=base.id)
    return clone


def update_edit(session: Session, edit: Edit, entries: list[dict[str, Any]] | None,
                settings_patch: dict[str, Any] | None, actor: str = "system") -> Edit:
    if entries is not None:
        new_entries = []
        for order, item in enumerate(entries):
            version = session.get(Version, int(item["version_id"]))
            if version is None or version.shot.sequence_id != edit.sequence_id or version.shot.kind != "shot":
                raise ValueError(f"Entry {order}: version {item.get('version_id')} is not a shot of this sequence")
            src_in, src_out = int(item["src_in"]), int(item["src_out"])
            if not 0 <= src_in < src_out <= (version.frame_count or src_out):
                raise ValueError(f"Entry {order} ({version.shot.code}): frames {src_in}-{src_out} out of range")
            transition = item.get("transition", "cut")
            if transition not in ("cut", "dissolve"):
                raise ValueError(f"Entry {order}: unknown transition {transition}")
            new_entries.append(EditEntry(order=order, version=version, src_in=src_in, src_out=src_out,
                                         transition=transition,
                                         transition_frames=int(item.get("transition_frames", 0) or 0),
                                         color={k: float(v) for k, v in (item.get("color") or {}).items()}))
        edit.entries = new_entries
    if settings_patch:
        merged = copy.deepcopy(edit.settings or {})
        for key in ("fade_in_s", "fade_out_s", "end_card", "entry_notes", "auto_color"):
            if key in settings_patch:
                merged[key] = settings_patch[key]
        edit.settings = merged
    session.flush()
    build_plan(edit, edit.sequence.project.fps)  # validate transitions before anyone renders
    log_activity(session, actor, "edit_updated", edit, entries=len(edit.entries))
    return edit
