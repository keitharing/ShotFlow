from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from shotflow.config import ReadOnlySourceError, writable_path
from shotflow.db import init_db, session_scope
from shotflow.importers.production_v04 import import_production_v04
from shotflow.models import Edit, GenerationJob, Shot, Version, VersionStatus
from shotflow.services import jobs


def test_writes_outside_data_dir_are_refused(settings, tmp_path):
    assert writable_path(settings.data_dir / "cache" / "x.jpg", settings).parent.is_dir()
    with pytest.raises(ReadOnlySourceError):
        writable_path(tmp_path / "elsewhere.jpg", settings)
    with pytest.raises(ReadOnlySourceError):
        writable_path(settings.production_v04 / "01_Original_Videos" / "R01_v01_original.mp4", settings)


@pytest.fixture
def imported(real_settings):
    init_db(real_settings)
    with session_scope(real_settings) as session:
        report = import_production_v04(session, real_settings)
    return real_settings, report


@pytest.mark.realdata
def test_import_matches_v04_records(imported):
    settings, report = imported
    assert (report.sequences, report.shots, report.versions) == (3, 15, 16)
    assert (report.jobs_generated, report.jobs_reused, report.jobs_rejected) == (13, 3, 1)
    assert report.credits_total == 960.0
    assert report.notes == 15
    assert report.edits == 3 and report.edit_versions == 3
    assert report.sha_mismatches == [] and report.warnings == []

    with session_scope(settings) as session:
        shots = session.query(Shot).filter(Shot.kind == "shot").all()
        assert all(s.approved_version is not None for s in shots)
        b01 = {v.code: v for v in next(s for s in shots if s.code == "B01").versions}
        assert b01["v01"].status == VersionStatus.REJECTED
        assert b01["retry01"].parent_id == b01["v01"].id
        assert all(v.sha_verified for v in session.query(Version) if v.sha256_expected)
        rejected = session.query(GenerationJob).filter(GenerationJob.external_id.is_(None),
                                                       GenerationJob.credits == 0).one()
        assert "429" in rejected.error
        for edit in session.query(Edit):
            assert len(edit.entries) == 5
            assert sum(e.frames for e in edit.entries) == edit.result_version.frame_count


@pytest.mark.realdata
def test_reimport_is_idempotent(imported):
    settings, _ = imported
    with session_scope(settings) as session:
        import_production_v04(session, settings)
    with session_scope(settings) as session:
        assert session.query(Version).count() == 19
        assert session.query(Shot).count() == 18


@pytest.mark.realdata
def test_source_files_are_unchanged_by_import(imported):
    settings, _ = imported
    with session_scope(settings) as session:
        for version in session.query(Version):
            assert Path(version.path).is_file()
            if version.sha256_expected:
                assert version.sha_verified


@pytest.mark.realdata
def test_boundary_sheet_and_qc_on_real_edit(imported):
    settings, _ = imported
    with session_scope(settings) as session:
        rain = next(e for e in session.query(Edit) if e.sequence.code == "RAIN_ATLAS")
        edit_id, movie_id = rain.id, rain.result_version_id
    sheet = jobs.boundary_sheet(edit_id, settings)
    assert Path(sheet["path"]).is_file()
    assert Path(sheet["path"]).resolve().is_relative_to(settings.data_dir)
    assert [b["from_shot"] + b["to_shot"] for b in sheet["boundaries"]] == ["R01R02", "R02R03", "R03R04", "R04R05"]
    result = jobs.qc_version(movie_id, settings)
    with session_scope(settings) as session:
        run = session.get(Version, movie_id).qc_runs[-1]
        assert run.id == result["run_id"]
        assert run.measurements["detected_cuts"] == run.measurements["expected_cuts"] == [96, 192, 288, 384]
        assert not [i for i in run.issues if i.check in ("hidden_cut", "expected_cut")]


@pytest.mark.realdata
def test_api_end_to_end(imported):
    from shotflow.api import create_app
    from shotflow.services.worker import worker

    settings, _ = imported
    client = TestClient(create_app(settings))
    assert client.get("/api/health").json()["status"] == "ok"
    project = client.get("/api/projects").json()[0]
    sequences = client.get(f"/api/projects/{project['id']}/sequences").json()
    assert [s["name"] for s in sequences] == ["BASALT RUN", "RAIN ATLAS", "THE LAST LIGHT"]
    shots = client.get(f"/api/sequences/{sequences[1]['id']}/shots").json()
    r01 = next(s for s in shots if s["code"] == "R01")
    version = client.get(f"/api/versions/{r01['approved_version_id']}").json()
    assert version["generation_job"]["model"] == "seedance_2_5"
    assert version["notes"][0]["source"] == "imported"

    media = client.get(version["media_url"], headers={"Range": "bytes=0-99"})
    assert media.status_code == 206 and len(media.content) == 100

    task = client.post(f"/api/versions/{version['id']}/qc").json()
    worker.wait_idle(timeout=120)
    assert client.get(f"/api/tasks/{task['id']}").json()["status"] == "done"
    assert client.get(f"/api/versions/{version['id']}").json()["qc"]["status"] in ("pass", "warning")

    dash = client.get("/api/dashboard").json()
    assert dash["shots"] == 15 and dash["shots_approved"] == 15
    assert dash["credits_total"] == 960.0
    assert client.get("/api/versions/99999").status_code == 404
