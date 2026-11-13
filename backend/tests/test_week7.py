"""Week 7: a film's own delivery size, starting an edit by hand, registering images made outside ShotFlow."""

from __future__ import annotations

import sqlite3

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from shotflow.api import create_app
from shotflow.db import init_db, session_scope
from shotflow.models import ActivityLog, Origin, Project, Sequence, Shot, Version, VersionStatus
from shotflow.services import structure
from shotflow.services.assemble import assemble, update_edit
from shotflow.services.media import probe, sha256_file
from shotflow.services.qc import run_qc

from .conftest import FPS, H, W, moving_frames, write_video


def png_bytes(width: int = 64, height: int = 36) -> bytes:
    image = np.full((height, width, 3), 120, np.uint8)
    cv2.rectangle(image, (8, 8), (width - 8, height - 8), (40, 90, 200), -1)
    return cv2.imencode(".png", image)[1].tobytes()


def test_import_video_registers_an_fx_version(settings, film):
    """A clip finished outside ShotFlow (effects composited over a generated clip) becomes an FX version."""
    clip = write_video(settings.data_dir / "comp.mp4", moving_frames(24, seed=5))
    with session_scope(settings) as session:
        shot = session.get(Shot, film["shots"][0])
        parent = session.get(Version, film["versions"][0])
        version = structure.import_image(session, shot, "C1_fx_v01.mp4", clip.read_bytes(), parent=parent,
                                         note="Blender 파편 · 먼지 합성", actor="tester", settings=settings)
        assert (version.code, version.origin, version.parent_id) == ("fx_001", Origin.FX, parent.id)
        assert (version.width, version.height, version.frame_count) == (W, H, 24)
        assert version.fps == pytest.approx(FPS) and version.duration_s == pytest.approx(1.0, abs=0.1) and version.has_audio
        assert version.path.endswith("fx_001.mp4") and len(version.qc_runs) == 1
        with pytest.raises(ValueError, match="읽을 수 없는"):
            structure.import_image(session, shot, "broken.mp4", b"not a video", settings=settings)
        assert [v.code for v in shot.versions] == ["sd_001", "fx_001"]


@pytest.fixture
def film(settings) -> dict:
    """One project (1920x1080) with a hand-registered film of two shots, each with a small video version."""
    init_db(settings)
    with session_scope(settings) as session:
        project = Project(code="TEST_FILMS", name="Test Films")
        session.add(project)
        session.flush()
        sequence = structure.create_sequence(session, project, code="COLLAPSE_DAY", name="무너지는 날",
                                             shots=[{"code": "C1"}, {"code": "C2"}])
        versions = []
        for i, shot in enumerate(sequence.shots):
            path = write_video(settings.data_dir / f"clip{i}.mp4", moving_frames(36, seed=i))
            info = probe(path)
            version = Version(shot=shot, code="sd_001", origin=Origin.GENERATED, status=VersionStatus.IN_REVIEW,
                              path=str(path), sha256=sha256_file(path), bytes=path.stat().st_size, width=info.width,
                              height=info.height, fps=info.fps, frame_count=info.frame_count,
                              duration_s=info.duration_s, video_codec=info.video_codec, has_audio=info.has_audio)
            session.add(version)
            session.flush()
            versions.append(version.id)
        return {"project": project.id, "sequence": sequence.id, "shots": [s.id for s in sequence.shots],
                "versions": versions}


def test_old_database_gets_the_new_columns(settings):
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(settings.db_path) as raw:  # a database made before week 7: no width/height
        raw.execute("CREATE TABLE sequence (id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, code VARCHAR(64) NOT NULL, "
                    "name VARCHAR(200) NOT NULL, \"order\" INTEGER, target_duration_s FLOAT)")
        raw.execute("INSERT INTO sequence (id, project_id, code, name, \"order\") VALUES (1, 1, 'OLD_FILM', 'Old', 0)")
    init_db(settings)
    init_db(settings)  # running twice must not fail
    with sqlite3.connect(settings.db_path) as raw:
        columns = {row[1] for row in raw.execute("PRAGMA table_info(sequence)")}
        assert {"width", "height"} <= columns
        assert raw.execute("SELECT code, width, height FROM sequence").fetchall() == [("OLD_FILM", None, None)]


def test_frame_size_falls_back_to_the_project(settings, film):
    with session_scope(settings) as session:
        sequence = session.get(Sequence, film["sequence"])
        assert sequence.frame_size == (1920, 1080)
        structure.set_frame_size(session, sequence, 1280, 720)
        assert sequence.frame_size == (1280, 720)
        structure.set_frame_size(session, sequence, None, None)
        assert sequence.frame_size == (1920, 1080)
        assert [a.action for a in session.query(ActivityLog).filter_by(action="sequence.frame_size")] == ["sequence.frame_size"] * 2


@pytest.mark.parametrize("width, height, message", [
    (1280, None, "함께"), (None, 720, "함께"), (8, 720, "사이"), (1280, 9000, "사이"), (1281, 720, "짝수"),
])
def test_frame_size_rejects_bad_values(settings, film, width, height, message):
    with session_scope(settings) as session:
        sequence = session.get(Sequence, film["sequence"])
        with pytest.raises(ValueError, match=message):
            structure.set_frame_size(session, sequence, width, height)
        assert sequence.width is None and sequence.height is None


def test_qc_checks_against_the_films_own_size(settings, film):
    with session_scope(settings) as session:
        version = session.get(Version, film["versions"][0])
        before = run_qc(session, version)
        assert [i.message for i in before.issues if i.check == "spec"] == [f"규격 불일치: 해상도 {W}x{H} (기준 1920x1080)"]
        structure.set_frame_size(session, version.shot.sequence, W, H)
        after = run_qc(session, version)
        assert [i for i in after.issues if i.check == "spec"] == []


def test_create_edit_and_assemble_at_the_films_size(settings, film):
    with session_scope(settings) as session:
        sequence = session.get(Sequence, film["sequence"])
        structure.set_frame_size(session, sequence, W, H)
        edit = structure.create_edit(session, sequence, name="rough_v01")
        assert edit.entries == [] and edit.settings == {"fade_in_s": 0.12, "fade_out_s": 0.25}
        with pytest.raises(ValueError, match="이미"):
            structure.create_edit(session, sequence, name=" rough_v01 ")
        with pytest.raises(ValueError, match="이름"):
            structure.create_edit(session, sequence, name="  ")
        update_edit(session, edit, [{"version_id": film["versions"][0], "src_in": 0, "src_out": 24},
                                    {"version_id": film["versions"][1], "src_in": 6, "src_out": 30}], None)
        result = assemble(session, edit, settings)
        assert (result.width, result.height) == (W, H)
        assert result.frame_count == 48 and result.shot.kind == "edit"
        assert abs(result.fps - FPS) < 0.01


def test_import_image_registers_a_version_with_qc(settings, film):
    with session_scope(settings) as session:
        shot = session.get(Shot, film["shots"][0])
        other = session.get(Shot, film["shots"][1])
        parent = session.get(Version, film["versions"][0])
        version = structure.import_image(session, shot, "C3_clean.png", png_bytes(), parent=parent,
                                         note="배경 얼룩을 지운 그림", actor="tester", settings=settings)
        assert (version.code, version.origin, version.status) == ("ext_001", Origin.IMPORTED, VersionStatus.IN_REVIEW)
        assert (version.width, version.height) == (64, 36) and version.parent_id == parent.id
        assert version.path.endswith("ext_001.png") and len(version.qc_runs) == 1
        assert [n.body for n in version.notes] == ["배경 얼룩을 지운 그림"]
        again = structure.import_image(session, shot, "again.JPG", cv2.imencode(".jpg", np.zeros((36, 64, 3), np.uint8))[1].tobytes(),
                                       settings=settings)
        assert again.code == "ext_002" and again.parent_id is None
        for name, payload, wrong_parent, message in [
            ("notes.txt", b"hello", None, "그림 파일"), ("empty.png", b"", None, "비었거나"),
            ("broken.png", b"not a png at all", None, "읽을 수 없는"),
            ("ok.png", png_bytes(), session.get(Version, film["versions"][1]), "이 샷의 버전이 아닙니다"),
        ]:
            with pytest.raises(ValueError, match=message):
                structure.import_image(session, shot, name, payload, parent=wrong_parent, settings=settings)
        assert [v.code for v in shot.versions if v.code.startswith("ext_")] == ["ext_001", "ext_002"]
        assert [v.code for v in other.versions] == ["sd_001"]


def test_week7_routes(settings, film):
    with TestClient(create_app(settings)) as client:
        seq = film["sequence"]
        size = client.put(f"/api/sequences/{seq}/frame-size", json={"width": 1280, "height": 720})
        assert size.status_code == 200 and size.json() == {"id": seq, "code": "COLLAPSE_DAY", "width": 1280,
                                                            "height": 720, "own_size": True}
        assert client.put(f"/api/sequences/{seq}/frame-size", json={"width": 1281, "height": 720}).status_code == 422

        renamed = client.put(f"/api/sequences/{seq}/name", json={"name": "  BEFORE THE DUST "})
        assert renamed.status_code == 200 and renamed.json() == {"id": seq, "code": "COLLAPSE_DAY", "name": "BEFORE THE DUST"}
        assert client.put(f"/api/sequences/{seq}/name", json={"name": "  "}).status_code == 422
        assert client.put("/api/sequences/99999/name", json={"name": "x"}).status_code == 404
        listed = client.get(f"/api/projects/{film['project']}/sequences").json()
        assert listed[0]["frame_size"] == [1280, 720] and listed[0]["own_frame_size"] is True

        edit = client.post(f"/api/sequences/{seq}/edits", json={"name": "rough_v01"})
        assert edit.status_code == 201 and edit.json()["sequence_id"] == seq
        assert client.post(f"/api/sequences/{seq}/edits", json={"name": "rough_v01"}).status_code == 422
        assert client.get(f"/api/edits/{edit.json()['id']}").status_code == 200

        shot = film["shots"][0]
        done = client.post(f"/api/shots/{shot}/versions/import", files={"file": ("C3_clean.png", png_bytes(), "image/png")},
                           data={"parent_version_id": str(film["versions"][0]), "note": "얼룩 지움", "author": "tester"})
        assert done.status_code == 201 and done.json()["version_code"] == "ext_001" and done.json()["qc"] is not None
        bad = client.post(f"/api/shots/{shot}/versions/import", files={"file": ("a.txt", b"x", "text/plain")})
        assert bad.status_code == 422 and "그림 파일" in bad.json()["detail"]
        assert client.post("/api/shots/99999/versions/import", files={"file": ("a.png", png_bytes(), "image/png")}).status_code == 404
