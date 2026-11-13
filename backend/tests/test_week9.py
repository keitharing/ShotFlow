"""Week 9: animatic -> Blender blocking (D-015). Camera solve, cut and drawing detection, API, real Blender build."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from shotflow.api import create_app
from shotflow.db import init_db, session_scope
from shotflow.models import GenerationJob, Project, ShotLayout
from shotflow.services import animatic as anim
from shotflow.services import layout as lay
from shotflow.services import structure
from shotflow.services.blender import runner as blender
from shotflow.services.layout_solve import LayoutError, solve_frame, solve_layout

from .conftest import H, W, write_video

ASPECT = 16 / 9


def project(point, cam_h, pitch_deg, lens, aspect=ASPECT):
    """World point -> (x, y) as fractions of the drawing, independent of the solver's code."""
    w, h = 1000 * aspect, 1000.0
    f = lens / 36 * w
    t = math.radians(pitch_deg)
    vx, vy, vz = point[0], point[1], point[2] - cam_h
    xc = vx
    yc = vy * math.sin(t) + vz * math.cos(t)
    zc = vy * math.cos(t) - vz * math.sin(t)
    return (w / 2 + f * xc / zc) / w, (h / 2 - f * yc / zc) / h


def box_for(x, y, width, height, cam_h, pitch, lens):
    left = project((x - width / 2, y, 0), cam_h, pitch, lens)
    right = project((x + width / 2, y, 0), cam_h, pitch, lens)
    top = project((x, y, height), cam_h, pitch, lens)
    return [left[0], top[1], right[0], left[1]]


def horizon_for(pitch, lens, aspect=ASPECT):
    f = lens / 36 * 1000 * aspect
    return (500 - f * math.tan(math.radians(pitch))) / 1000


def test_solver_recovers_a_known_camera():
    cam_h, pitch, lens = 1.5, 12.0, 35.0
    marks = {"horizon": horizon_for(pitch, lens), "objects": [
        {"kind": "person", "label": "사람", "box": box_for(1.0, 6.0, 0.5, 1.7, cam_h, pitch, lens), "height_m": 1.7, "anchor": True},
        {"kind": "box", "label": "상자", "box": box_for(-2.0, 9.0, 1.2, 0.8, cam_h, pitch, lens)},
    ]}
    result = solve_frame(marks, aspect=ASPECT, lens_mm=lens)
    camera = result["camera"]
    assert camera["height_m"] == pytest.approx(cam_h, rel=0.01)
    assert camera["pitch_deg"] == pytest.approx(pitch, abs=0.01)
    assert camera["rotation_deg"] == pytest.approx([90 - pitch, 0, 0], abs=0.01)
    person, crate = result["objects"]
    assert person["anchor"] and person["mask"] and person["location"] == pytest.approx([0, 0, 0], abs=1e-6)
    assert camera["location"] == pytest.approx([-1.0, -6.0, cam_h], rel=0.01)
    # The box had no height given: both its place and its size come from the drawing.
    assert crate["location"][:2] == pytest.approx([-3.0, 3.0], rel=0.02)
    assert crate["size"][0] == pytest.approx(1.2, rel=0.02)
    assert crate["size"][2] == pytest.approx(0.8, rel=0.02)
    assert result["warnings"] == []


def test_first_drop_a3_drawing_matches_the_hand_built_camera():
    """FIRST DROP A3 storyboard page (drawing 1546x864 px): bottle box measured on the drawing, level camera, 60 mm.
    The scene built by hand (scenes/first_drop/build_scenes.py) has the camera at 0.13 m, 0.95 m in front."""
    result = solve_layout({"lens_mm": 60, "frames": [{"role": "start", "horizon": 0.5, "objects": [
        {"kind": "bottle", "label": "병", "box": [0.42, 0.14, 0.57, 0.88], "height_m": 0.245}]}]}, aspect=1546 / 864)
    camera = result["cameras"][0]
    assert camera["height_m"] == pytest.approx(0.13, rel=0.1)
    assert camera["distance_m"] == pytest.approx(0.95, rel=0.1)


def test_end_drawing_moves_the_camera_from_the_same_anchor():
    lens = 50.0
    start = {"role": "start", "horizon": 0.5, "objects": [
        {"kind": "bottle", "label": "병", "box": box_for(0, 1.0, 0.08, 0.25, 0.12, 0, lens), "height_m": 0.25}]}
    end = {"role": "end", "horizon": 0.5, "objects": [
        {"kind": "bottle", "label": "병", "box": box_for(0, 0.7, 0.08, 0.25, 0.12, 0, lens), "height_m": 0.25}]}
    result = solve_layout({"lens_mm": lens, "frames": [start, end]}, aspect=ASPECT)
    first, last = result["cameras"]
    assert (first["role"], last["role"]) == ("start", "end")
    assert first["location"][1] == pytest.approx(-1.0, rel=0.01)
    assert last["location"][1] == pytest.approx(-0.7, rel=0.01)   # moved in, bottle stays at the origin


def test_floating_object_sits_at_the_anchor_distance():
    lens = 50.0
    result = solve_frame({"horizon": 0.5, "objects": [
        {"kind": "bottle", "label": "병", "box": box_for(0, 1.0, 0.08, 0.25, 0.12, 0, lens), "height_m": 0.25},
        {"kind": "round", "label": "과일", "grounded": False, "box": [0.30, 0.20, 0.36, 0.30]},
    ]}, aspect=ASPECT, lens_mm=lens)
    fruit = result["objects"][1]
    assert fruit["location"][1] == pytest.approx(0.0, abs=1e-6)        # same distance as the bottle
    assert fruit["location"][2] > 0.12                                   # above the camera height: drawn high
    assert not fruit["grounded"] and not fruit["mask"]


@pytest.mark.parametrize("marks, message", [
    ({"horizon": 0.5, "objects": []}, "상자를 하나 이상"),
    ({"horizon": 0.9, "objects": [{"kind": "box", "box": [0.4, 0.3, 0.6, 0.8]}]}, "수평선"),
    ({"horizon": 0.5, "objects": [{"kind": "round", "grounded": False, "box": [0.4, 0.3, 0.6, 0.8]}]}, "바닥에 닿은"),
    ({"horizon": 0.5, "objects": [{"kind": "box", "box": [0.4, 0.6, 0.4, 0.8]}]}, "너무 작은"),
])
def test_solver_rejects_marks_that_cannot_describe_a_camera(marks, message):
    with pytest.raises(LayoutError, match=message):
        solve_frame(marks, aspect=ASPECT, lens_mm=50)


def test_unknown_height_falls_back_with_a_warning():
    result = solve_frame({"horizon": 0.5, "objects": [{"kind": "box", "box": [0.4, 0.3, 0.6, 0.8]}]},
                         aspect=ASPECT, lens_mm=50)
    assert result["camera"]["height_m"] == 1.6
    assert "1.6m" in result["warnings"][0]


# ---------------------------------------------------------------- animatic

def page(drawing: int | None) -> np.ndarray:
    """A storyboard page: dark page, light paper panel with a dark shape; None = title page (small text only)."""
    img = np.full((H, W, 3), 22, np.uint8)
    img[4:9, 10:70] = 200  # header text
    if drawing is None:
        img[45:60, 70:120] = 230
        return img
    img[12:88, 24:168] = (235, 240, 242)
    x = 30 + drawing * 40
    img[25:80, x:x + 38] = 40
    return img


def storyboard_video(path: Path) -> Path:
    frames = [page(None)] * 20 + [page(0)] * 30
    a, b = page(1).astype(float), page(2).astype(float)
    frames += [page(1)] * 16 + [((1 - k / 8) * a + k / 8 * b).astype(np.uint8) for k in range(1, 9)] + [page(2)] * 16
    frames += [page(0)] * 30
    return write_video(path, np.stack(frames), audio=None)


def test_cuts_and_drawing_area_of_a_storyboard_animatic(tmp_path):
    video = storyboard_video(tmp_path / "board.mp4")
    ranges, n = anim.detect_cuts(video)
    assert n == 120
    assert ranges == [(0, 20), (20, 50), (50, 90), (90, 120)]       # the cross-fade inside the 3rd cut is no cut
    assert anim.find_panel(page(None)) is None
    assert anim.find_panel(page(1)) == [24, 12, 144, 76]
    full = np.full((H, W, 3), 160, np.uint8)
    assert anim.find_panel(full) == [0, 0, W, H]                       # an ordinary animatic: the whole frame


def test_drawing_frames_are_saved_under_a_korean_folder(tmp_path, monkeypatch):
    """The real data folder has Korean in its path; OpenCV's own writer fails there without an error."""
    from shotflow.config import load_settings
    monkeypatch.setenv("SHOTFLOW_DATA", str(tmp_path / "작업 데이터"))
    settings = load_settings()
    init_db(settings)
    video = storyboard_video(tmp_path / "board.mp4")
    with session_scope(settings) as session:
        project = Project(code="TEST", name="Test")
        session.add(project)
        session.flush()
        sequence = structure.create_sequence(session, project, code="FIRST_DROP", name="첫 영상", shots=[{"code": "A1"}])
        animatic = anim.import_animatic(session, sequence, "보드.mp4", video.read_bytes(), settings)
        path = anim.frame_image(animatic, 30, settings)
    assert path.is_file() and path.read_bytes()[:2] == b"\xff\xd8"


@pytest.fixture
def film(settings):
    init_db(settings)
    with session_scope(settings) as session:
        project = Project(code="TEST", name="Test")
        session.add(project)
        session.flush()
        sequence = structure.create_sequence(session, project, code="FIRST_DROP", name="FIRST DROP",
                                             shots=[{"code": "A1"}, {"code": "A2"}, {"code": "A3"}])
        return sequence.id, [s.id for s in sequence.shots]


def bottle_marks(frame: int | None = None) -> dict:
    start = {"role": "start", "horizon": 0.5,
             "objects": [{"kind": "bottle", "label": "병", "box": [0.42, 0.14, 0.57, 0.88], "height_m": 0.245}]}
    if frame is not None:
        start["frame"] = frame
    return {"lens_mm": 60, "frames": [start]}


def test_api_animatic_to_layout(settings, film, tmp_path, monkeypatch):
    sequence_id, shot_ids = film
    submitted = []
    monkeypatch.setattr(lay, "submit", lambda job_id, s: submitted.append(job_id))
    client = TestClient(create_app(settings))
    video = storyboard_video(tmp_path / "board.mp4")
    response = client.post("/api/animatics", data={"sequence_id": str(sequence_id)},
                           files={"file": ("FirstDrop_Animatic.mp4", video.read_bytes(), "video/mp4")})
    assert response.status_code == 201, response.text
    animatic = response.json()
    assert animatic["crop"] == [24, 12, 144, 76] and animatic["aspect"] == pytest.approx(144 / 76, rel=1e-3)
    cuts = animatic["cuts"]
    assert [c["has_panel"] for c in cuts] == [False, True, True, True]
    assert [c["shot_id"] for c in cuts] == [None, *shot_ids]          # drawings linked to A1..A3 in order
    assert 50 <= cuts[2]["frames"]["start"] < cuts[2]["frames"]["end"] < 90

    frame = client.get(f"/api/animatics/{animatic['id']}/frames/{cuts[1]['frames']['start']}")
    assert frame.status_code == 200 and frame.headers["content-type"] == "image/jpeg"

    bad = client.patch(f"/api/animatics/{animatic['id']}/cuts", json={"changes": [{"index": 1, "frames": {"start": 5}}]})
    assert bad.status_code == 422 and "사이" in bad.json()["detail"]
    moved = client.patch(f"/api/animatics/{animatic['id']}/cuts",
                         json={"changes": [{"index": 3, "shot_id": None}, {"index": 1, "frames": {"end": 45}}]})
    assert moved.json()["cuts"][3]["shot_id"] is None and moved.json()["cuts"][1]["frames"]["end"] == 45

    solved = client.post("/api/layouts/solve", json={"animatic_id": animatic["id"], "cut_index": 1, "marks": bottle_marks()})
    assert solved.status_code == 200, solved.text
    assert solved.json()["cameras"][0]["height_m"] > 0
    title = client.post("/api/layouts", json={"animatic_id": animatic["id"], "cut_index": 0, "marks": bottle_marks()})
    assert title.status_code == 422 and "그림이 없습니다" in title.json()["detail"]
    unlinked = client.post("/api/layouts", json={"animatic_id": animatic["id"], "cut_index": 3, "marks": bottle_marks()})
    assert unlinked.status_code == 422 and "샷에 연결" in unlinked.json()["detail"]
    outside = client.post("/api/layouts", json={"animatic_id": animatic["id"], "cut_index": 1, "marks": bottle_marks(frame=5)})
    assert outside.status_code == 422

    first = client.post("/api/layouts", json={"animatic_id": animatic["id"], "cut_index": 1, "marks": bottle_marks()})
    second = client.post("/api/layouts", json={"animatic_id": animatic["id"], "cut_index": 1, "marks": bottle_marks()})
    assert first.status_code == 202, first.text
    assert (first.json()["code"], second.json()["code"]) == ("lay_001", "lay_002")
    assert first.json()["job"]["purpose"] == "layout" and first.json()["job"]["status"] == "queued"
    assert submitted == [first.json()["job"]["id"], second.json()["job"]["id"]]
    listed = client.get(f"/api/shots/{shot_ids[0]}/layouts").json()
    assert [r["code"] for r in listed] == ["lay_002", "lay_001"]
    assert client.get("/api/blender/jobs", params={"purpose": "passes"}).json() == []   # layout jobs stay out of the depth list


@pytest.mark.skipif(blender.blender_exe() is None, reason="Blender is not installed on this machine")
def test_layout_builds_a_new_blend_in_blender(settings, film, tmp_path):
    sequence_id, shot_ids = film
    video = storyboard_video(tmp_path / "board.mp4")
    with session_scope(settings) as session:
        from shotflow.models import Sequence
        animatic = anim.import_animatic(session, session.get(Sequence, sequence_id), "board.mp4", video.read_bytes(), settings)
        marks = bottle_marks()
        marks["frames"][0]["objects"].append({"kind": "round", "label": "과일", "grounded": False, "box": [0.2, 0.2, 0.3, 0.32]})
        layout = lay.create_layout(session, animatic, 1, marks)
        job_id, layout_id = layout.generation_job_id, layout.id
    with session_scope(settings) as session:
        session.get(GenerationJob, job_id).status = "running"
    lay.run_build(job_id, settings)
    with session_scope(settings) as session:
        job = session.get(GenerationJob, job_id)
        layout = session.get(ShotLayout, layout_id)
        assert job.status == "completed", job.error
        blend = Path(layout.blend_path)
        assert blend.is_file() and blend.name == "FIRST_DROP_A1_lay_001.blend"
        assert Path(job.params["outputs"]["preview_start"]).is_file()
    entry = next(s for s in blender.scenes(settings) if s["path"] == str(blend))
    assert entry["camera"] == "SF_Camera" and entry["mask_objects"] == 1
    # The 3D keyframe step can open it like any scene: inspect reads the camera back from the saved file.
    info = blender.inspect(blend, settings)
    assert info["cameras"] == ["SF_Camera"] and info["resolution"][:2] == [1920, 1080]
