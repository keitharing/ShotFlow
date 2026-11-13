"""Week 6: registering new sequences and shots by hand (service, API, CLI, importer guard)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from shotflow.api import create_app
from shotflow.cli import main as cli_main
from shotflow.db import init_db, session_scope
from shotflow.importers.production_v04 import import_production_v04
from shotflow.models import ActivityLog, Project, Sequence
from shotflow.services import structure

SHOTS = [{"code": "C1", "description": "아래에서 올려다본 건물"}, {"code": "C2", "description": "옆에서 넓게"}]


@pytest.fixture
def project_id(settings) -> int:
    init_db(settings)
    with session_scope(settings) as session:
        project = Project(code="TEST_FILMS", name="Test Films")
        session.add(project)
        session.flush()
        return project.id


def test_create_sequence_with_shots(settings, project_id):
    with session_scope(settings) as session:
        project = session.get(Project, project_id)
        sequence = structure.create_sequence(session, project, code="COLLAPSE_DAY", name="무너지는 날",
                                             target_duration_s=16, shots=SHOTS)
        assert sequence.id is not None
        assert [(s.code, s.order, s.kind) for s in sequence.shots] == [("C1", 0, "shot"), ("C2", 1, "shot")]
        assert sequence.shots[0].description == "아래에서 올려다본 건물"
        assert sequence.order == 0
        second = structure.create_sequence(session, project, code="FIRST_SIP", name="첫 모금", shots=[{"code": "A1"}])
        assert second.order == 1 and second.shots[0].description is None
        actions = [a.action for a in session.query(ActivityLog).order_by(ActivityLog.id)]
        assert actions == ["sequence.create", "sequence.create"]


@pytest.mark.parametrize("kwargs, message", [
    ({"code": "collapse", "name": "x", "shots": SHOTS}, "영상 코드"),
    ({"code": "OK_CODE", "name": "  ", "shots": SHOTS}, "이름"),
    ({"code": "OK_CODE", "name": "x", "shots": []}, "하나 이상"),
    ({"code": "OK_CODE", "name": "x", "shots": [{"code": "EDIT"}]}, "예약"),
    ({"code": "OK_CODE", "name": "x", "shots": [{"code": "C1"}, {"code": "C1"}]}, "겹칩니다"),
    ({"code": "OK_CODE", "name": "x", "shots": [{"code": "c1"}]}, "샷 코드"),
    ({"code": "OK_CODE", "name": "x", "shots": SHOTS, "target_duration_s": 0}, "0보다"),
])
def test_create_sequence_rejects_bad_input(settings, project_id, kwargs, message):
    with session_scope(settings) as session:
        project = session.get(Project, project_id)
        with pytest.raises(ValueError, match=message):
            structure.create_sequence(session, project, **kwargs)
        assert session.query(Sequence).count() == 0


def test_duplicate_sequence_and_add_shot(settings, project_id):
    with session_scope(settings) as session:
        project = session.get(Project, project_id)
        sequence = structure.create_sequence(session, project, code="COLLAPSE_DAY", name="무너지는 날", shots=SHOTS)
        with pytest.raises(ValueError, match="이미 있습니다"):
            structure.create_sequence(session, project, code="COLLAPSE_DAY", name="다시", shots=SHOTS)
        shot = structure.add_shot(session, sequence, code="C3", description="얼굴 가까이")
        assert shot.order == 2
        with pytest.raises(ValueError, match="이미 있습니다"):
            structure.add_shot(session, sequence, code="C3")


def test_api_creates_sequence_and_shot(settings, project_id):
    client = TestClient(create_app(settings))
    payload = {"code": "COLLAPSE_DAY", "name": "무너지는 날", "target_duration_s": 16, "shots": SHOTS}
    created = client.post(f"/api/projects/{project_id}/sequences", json=payload)
    assert created.status_code == 201
    body = created.json()
    assert body["code"] == "COLLAPSE_DAY" and body["shots"] == 2 and body["target_duration_s"] == 16
    listed = client.get(f"/api/projects/{project_id}/sequences").json()
    assert [s["code"] for s in listed] == ["COLLAPSE_DAY"]
    shots = client.get(f"/api/sequences/{body['id']}/shots").json()
    assert [s["code"] for s in shots] == ["C1", "C2"]

    assert client.post(f"/api/projects/{project_id}/sequences", json=payload).status_code == 422
    assert client.post("/api/projects/9999/sequences", json=payload).status_code == 404
    added = client.post(f"/api/sequences/{body['id']}/shots", json={"code": "C3", "description": "얼굴"})
    assert added.status_code == 201 and added.json()["code"] == "C3"
    assert client.post(f"/api/sequences/{body['id']}/shots", json={"code": "EDIT"}).status_code == 422


def test_cli_add_sequence(settings, project_id, tmp_path, capsys):
    spec = tmp_path / "sequence.json"
    spec.write_text(json.dumps({"code": "COLLAPSE_DAY", "name": "무너지는 날", "target_duration_s": 16, "shots": SHOTS},
                               ensure_ascii=False), encoding="utf-8")
    assert cli_main(["add-sequence", str(spec)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["sequence"]["code"] == "COLLAPSE_DAY"
    assert [s["code"] for s in printed["shots"]] == ["C1", "C2"]
    with pytest.raises(SystemExit, match="이미 있습니다"):
        cli_main(["add-sequence", str(spec)])


@pytest.mark.realdata
def test_reimport_refuses_to_delete_hand_made_sequences(real_settings):
    init_db(real_settings)
    with session_scope(real_settings) as session:
        import_production_v04(session, real_settings)
    with session_scope(real_settings) as session:
        import_production_v04(session, real_settings)  # nothing hand-made yet: re-import still allowed
        project = session.query(Project).one()
        imported = len(project.sequences)
        structure.create_sequence(session, project, code="COLLAPSE_DAY", name="무너지는 날", shots=SHOTS)
    with session_scope(real_settings) as session:
        with pytest.raises(ValueError, match="COLLAPSE_DAY"):
            import_production_v04(session, real_settings)
    with session_scope(real_settings) as session:
        project = session.query(Project).one()
        assert len(project.sequences) == imported + 1
