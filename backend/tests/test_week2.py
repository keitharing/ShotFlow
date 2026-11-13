"""Week 2: review decisions, edit assembly, proxies and the new API routes (synthetic media, fast)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from shotflow.db import init_db, session_scope
from shotflow.models import Edit, EditEntry, Project, Sequence, Shot, Version, VersionStatus
from shotflow.services import assemble as asm
from shotflow.services import proxy
from shotflow.services.media import probe, sha256_file
from shotflow.services.qc import run_qc
from shotflow.services.review import add_note

from .conftest import FPS, H, W, moving_frames, other_scene, write_video


@pytest.fixture
def project_db(settings, tmp_path):
    """Tiny project: one sequence, three 2-second shots (one with two versions), one edit."""
    init_db(settings)
    clips = {
        "S01": write_video(tmp_path / "s01.mp4", moving_frames(48, seed=1)),
        "S02": write_video(tmp_path / "s02.mp4", other_scene(48)),
        "S03": write_video(tmp_path / "s03.mp4", moving_frames(48, seed=3)[:, ::-1, :].copy()),
        "S03b": write_video(tmp_path / "s03b.mp4", moving_frames(48, seed=4)),
    }
    with session_scope(settings) as session:
        project = Project(code="TEST", name="Test", fps=FPS, width=W, height=H)
        seq = Sequence(project=project, code="SEQ", name="SEQ")
        shots = {code: Shot(sequence=seq, code=code, order=i) for i, code in enumerate(["S01", "S02", "S03"])}
        versions = {}
        for key, path in clips.items():
            shot = shots[key[:3]]
            info = probe(path)
            v = Version(shot=shot, code="v02" if key.endswith("b") else "v01", path=str(path),
                        sha256=sha256_file(path), width=info.width, height=info.height, fps=info.fps,
                        frame_count=info.frame_count, duration_s=info.duration_s, video_codec=info.video_codec,
                        has_audio=info.has_audio, status=VersionStatus.IN_REVIEW)
            versions[key] = v
        for code in ("S01", "S02", "S03"):
            versions[code].status = VersionStatus.APPROVED
            shots[code].approved_version = versions[code]
        edit = Edit(sequence=seq, name="base", settings={"fade_in_s": 0.0, "fade_out_s": 0.0})
        for i, code in enumerate(["S01", "S02", "S03"]):
            edit.entries.append(EditEntry(order=i, version=versions[code], src_in=0, src_out=48))
        session.add(project)
        session.flush()
        ids = {k: v.id for k, v in versions.items()} | {"edit": edit.id}
    return settings, ids


def test_approve_supersedes_previous_and_reject_clears(project_db):
    settings, ids = project_db
    with session_scope(settings) as session:
        new = session.get(Version, ids["S03b"])
        add_note(session, new, author="QA", body="better", decision="approve", frame=10)
    with session_scope(settings) as session:
        new, old = session.get(Version, ids["S03b"]), session.get(Version, ids["S03"])
        assert new.shot.approved_version_id == new.id
        assert (new.status, old.status) == (VersionStatus.APPROVED, VersionStatus.IN_REVIEW)
        assert new.notes[-1].frame == 10
        add_note(session, new, author="QA", body="flicker", decision="reject")
    with session_scope(settings) as session:
        new = session.get(Version, ids["S03b"])
        assert new.status == VersionStatus.REJECTED and new.shot.approved_version_id is None


def test_note_validation(project_db):
    settings, ids = project_db
    with session_scope(settings) as session:
        version = session.get(Version, ids["S01"])
        with pytest.raises(ValueError):
            add_note(session, version, author="a", body="", decision="comment")
        with pytest.raises(ValueError):
            add_note(session, version, author="a", body="x", decision="comment", frame=999)
        with pytest.raises(ValueError):
            add_note(session, version, author="a", body="x", decision="ship-it")


def test_plan_math_cuts_dissolves_and_card(project_db):
    settings, ids = project_db
    with session_scope(settings) as session:
        edit = session.get(Edit, ids["edit"])
        edit.entries[2].transition, edit.entries[2].transition_frames = "dissolve", 12
        edit.settings = {"end_card": {"title": "T", "duration_s": 1.0, "transition": "cut"}}
        plan = asm.build_plan(edit, FPS, card_path="card.png")
        assert plan.total_frames == 48 + 48 + 48 - 12 + 24
        assert plan.cut_frames == [48, 132]  # S01|S02 cut, S03|card cut; the S02→S03 dissolve is not a cut
        assert plan.dissolve_ranges == [[84, 96]]
        assert plan.static_ranges == [[132, 156]]


def test_dissolve_longer_than_clip_is_rejected(project_db):
    settings, ids = project_db
    with session_scope(settings) as session:
        edit = session.get(Edit, ids["edit"])
        edit.entries[1].transition, edit.entries[1].transition_frames = "dissolve", 48
        with pytest.raises(ValueError):
            asm.build_plan(edit, FPS)


def test_assemble_renders_exact_frames_and_passes_qc(project_db):
    settings, ids = project_db
    with session_scope(settings) as session:
        edit = session.get(Edit, ids["edit"])
        asm.update_edit(session, edit, [
            {"version_id": ids["S01"], "src_in": 0, "src_out": 40},
            {"version_id": ids["S02"], "src_in": 4, "src_out": 48, "color": {"brightness": 0.02}},
            {"version_id": ids["S03"], "src_in": 0, "src_out": 48, "transition": "dissolve", "transition_frames": 12},
        ], {"end_card": {"title": "Test Film", "subtitle": "sub", "duration_s": 1.0,
                         "transition": "dissolve", "transition_frames": 6}})
        version = asm.assemble(session, edit, settings, "v1")
        session.flush()
        run = run_qc(session, version)
        render = edit.settings["renders"][str(version.id)]
        assert Path(version.path).resolve().is_relative_to(settings.data_dir)
        assert version.frame_count == render["planned_frames"] == 40 + 44 + 48 - 12 + 24 - 6
        assert run.measurements["detected_cuts"] == render["cut_frames"] == [40]
        assert not [i for i in run.issues if i.check in ("hidden_cut", "expected_cut")]
        card_start = render["static_ranges"][0][0]
        assert card_start == 40 + 44 + 48 - 12 - 6
        card_issues = [i for i in run.issues if i.check in ("freeze", "silence") and i.start_frame >= card_start]
        assert card_issues and all(i.severity == "info" for i in card_issues)
        again = asm.assemble(session, edit, settings, "v1")
        assert again.code == "v1_r2" and again.parent_id == version.id


def test_update_edit_rejects_bad_ranges(project_db):
    settings, ids = project_db
    with session_scope(settings) as session:
        edit = session.get(Edit, ids["edit"])
        with pytest.raises(ValueError):
            asm.update_edit(session, edit, [{"version_id": ids["S01"], "src_in": 10, "src_out": 99}], None)
        with pytest.raises(ValueError):
            asm.update_edit(session, edit, [{"version_id": ids["S01"], "src_in": 0, "src_out": 10,
                                             "transition": "wipe"}], None)


def test_clone_points_at_approved_versions(project_db):
    settings, ids = project_db
    with session_scope(settings) as session:
        add_note(session, session.get(Version, ids["S03b"]), author="QA", body="", decision="approve")
    with session_scope(settings) as session:
        clone = asm.clone_edit(session, session.get(Edit, ids["edit"]), "v2")
        assert [e.version_id for e in clone.entries] == [ids["S01"], ids["S02"], ids["S03b"]]
        assert clone.settings["based_on_edit_id"] == ids["edit"]


def test_suggest_brightness_is_limited(project_db):
    settings, ids = project_db
    with session_scope(settings) as session:
        offsets = asm.suggest_brightness(session.get(Edit, ids["edit"]), "S01", strength=0.5, limit=0.04)
        assert offsets["S01"] == 0.0
        assert all(abs(v) <= 0.04 for v in offsets.values())


def test_proxy_and_thumbnail_are_cached_in_data(project_db):
    settings, ids = project_db
    with session_scope(settings) as session:
        version = session.get(Version, ids["S01"])
        path = proxy.ensure_proxy(version, settings)
        assert path.resolve().is_relative_to(settings.data_dir)
        info = probe(path)
        assert info.video_codec == "h264" and info.frame_count == 48
        assert proxy.ensure_proxy(version, settings) == path  # second call hits the cache
        thumb = proxy.ensure_thumbnail(version, settings)
        assert thumb.is_file() and thumb.stat().st_size > 1000


def test_week2_api_flow(project_db):
    from shotflow.api import create_app
    from shotflow.services.worker import worker

    settings, ids = project_db
    client = TestClient(create_app(settings))
    r = client.post(f"/api/versions/{ids['S03b']}/notes", json={"author": "QA", "body": "ok", "decision": "approve", "frame": 3})
    assert r.status_code == 201 and r.json()["status"] == "approved"
    assert client.post(f"/api/versions/{ids['S01']}/notes", json={"decision": "nope"}).status_code == 422

    clone = client.post(f"/api/edits/{ids['edit']}/clone", json={"name": "v2"}).json()
    assert client.post(f"/api/edits/{ids['edit']}/clone", json={"name": "v2"}).status_code == 409
    updated = client.put(f"/api/edits/{clone['id']}", json={
        "entries": [{"version_id": ids["S01"], "src_in": 0, "src_out": 24},
                    {"version_id": ids["S02"], "src_in": 0, "src_out": 24}],
        "settings": {"fade_in_s": 0.1}}).json()
    assert updated["total_frames"] == 48 and updated["settings"]["fade_in_s"] == 0.1

    task = client.post(f"/api/edits/{clone['id']}/assemble", json={"version_code": "api"}).json()
    worker.wait_idle(timeout=180)
    done = client.get(f"/api/tasks/{task['id']}").json()
    assert done["status"] == "done", done["error"]
    result = done["result"]
    assert result["frames"] == 48 and result["qc_status"] in ("pass", "warning")
    assert client.get(f"/api/edits/{clone['id']}/boundary-sheet").status_code == 200
    assert client.get(f"/api/versions/{result['version_id']}/proxy").status_code == 200
    assert client.get(f"/api/versions/{result['version_id']}/thumbnail").status_code == 200
    seq = client.get(f"/api/sequences/{updated['sequence_id']}").json()
    assert {e["name"] for e in seq["edit_list"]} == {"base", "v2"}
    assert np.isclose(client.get(f"/api/edits/{clone['id']}/renders").json()[0]["actual_frames"], 48)
