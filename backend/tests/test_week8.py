"""Week 8: prompt management. Shared blocks per film, a scene text per shot and use, and the record of which
generation was made from which text."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from shotflow.api import create_app
from shotflow.db import get_engine, init_db, session_scope
from shotflow.models import GenerationJob, Project, PromptSnapshot, Sequence, Shot
from shotflow.services import prompts, structure


def png_bytes(width: int = 64, height: int = 36) -> bytes:
    image = np.full((height, width, 3), 120, np.uint8)
    cv2.rectangle(image, (8, 8), (width - 8, height - 8), (40, 90, 200), -1)
    return cv2.imencode(".png", image)[1].tobytes()


@pytest.fixture
def film(settings) -> dict:
    """A film of three shots; the first two have a start image."""
    init_db(settings)
    with session_scope(settings) as session:
        project = Project(code="TEST_FILMS", name="Test Films")
        session.add(project)
        session.flush()
        sequence = structure.create_sequence(session, project, code="LIGHT_KID", name="빛을 쥔 아이",
                                             shots=[{"code": "S01"}, {"code": "S02"}, {"code": "S03"}])
        versions = [structure.import_image(session, shot, f"{shot.code}.png", png_bytes(), actor="tester", settings=settings).id
                    for shot in sequence.shots[:2]]
        return {"sequence": sequence.id, "shots": [s.id for s in sequence.shots], "versions": versions}


def test_a_prompt_is_composed_from_shared_blocks_and_the_scene_text(settings, film):
    with session_scope(settings) as session:
        sequence = session.get(Sequence, film["sequence"])
        s01, s02, s03 = (session.get(Shot, i) for i in film["shots"])
        prompts.set_block(session, sequence, "style", label="그림체", text="Hand-drawn 2D cel animation frame.", place="after")
        prompts.set_block(session, sequence, "guide", label="참고 그림 쓰는 법", text="Keep the composition of the reference.", place="before")
        prompts.set_block(session, sequence, "avoid", label="넣지 않을 것", text="text, watermark", place="negative")
        prompts.set_block(session, sequence, "night", label="밤", text="Deep navy night palette.", place="after")
        prompts.set_shot_prompt(session, s01, "image", scene="A single candle on a dark table.",
                                block_keys=["night", "style", "guide", "avoid", "style"])
        made = prompts.compose(session, s01, "image")
        # before-blocks, the scene, then after-blocks in the film's block order (not the order they were ticked in)
        assert made["text"] == ("Keep the composition of the reference. A single candle on a dark table. "
                                "Hand-drawn 2D cel animation frame. Deep navy night palette.")
        assert made["negative"] == "text, watermark" and made["block_keys"] == ["style", "guide", "avoid", "night"]
        assert [p["place"] for p in made["parts"]] == ["before", "scene", "after", "after", "negative"]
        assert prompts.compose(session, s01, "video")["text"] == ""  # each use is kept apart

        # one step puts the look into every shot; a shot without a scene text is flagged, not hidden
        assert prompts.apply_blocks(session, sequence, "image", add=["style", "guide"]) == 2
        other = prompts.compose(session, s02, "image")
        assert other["scene_missing"] and other["text"] == "Keep the composition of the reference. Hand-drawn 2D cel animation frame."
        assert prompts.apply_blocks(session, sequence, "image", remove=["guide"], shot_ids=[s02.id]) == 1
        assert prompts.compose(session, s02, "image")["block_keys"] == ["style"]

        # the frozen text survives an edit of the block; the next prompt of every shot follows the edit
        first = prompts.snapshot(session, s01, "image")
        assert prompts.snapshot(session, s01, "image").id == first.id
        prompts.set_block(session, sequence, "style", label="그림체", text="Flat cel shading, clean ink outlines.", place="after")
        second = prompts.snapshot(session, s01, "image")
        assert second.id != first.id and "Hand-drawn" in session.get(PromptSnapshot, first.id).text
        assert "Flat cel shading" in second.text and "Flat cel shading" in prompts.compose(session, s03, "image")["text"]

        with pytest.raises(ValueError, match="쓰는 컷이 있습니다"):
            prompts.delete_block(session, sequence, "style")
        prompts.set_block(session, sequence, "unused", label="안 쓰는 것", text="x", place="after")
        prompts.delete_block(session, sequence, "unused")
        for bad in (lambda: prompts.set_block(session, sequence, "Bad Key", label="", text="x"),
                    lambda: prompts.set_block(session, sequence, "ok", label="", text="  "),
                    lambda: prompts.set_block(session, sequence, "ok", label="", text="x", place="middle"),
                    lambda: prompts.set_shot_prompt(session, s01, "poster", scene="x", block_keys=[]),
                    lambda: prompts.set_shot_prompt(session, s01, "image", scene="x", block_keys=["nope"]),
                    lambda: prompts.snapshot(session, s03, "video")):
            with pytest.raises(ValueError):
                bad()


def test_generations_record_the_prompt_they_were_made_from(settings, film):
    seq, (s01, s02, _s03), (v01, v02) = film["sequence"], film["shots"], film["versions"]
    with TestClient(create_app(settings)) as client:
        assert client.put(f"/api/sequences/{seq}/prompt-blocks/style",
                          json={"label": "그림체", "text": "2D cel animation.", "place": "after"}).status_code == 200
        assert client.put(f"/api/sequences/{seq}/prompt-blocks/Bad Key", json={"text": "x"}).status_code == 422
        saved = client.put(f"/api/shots/{s01}/prompt/video", json={"scene": "The light rises into the sky.", "block_keys": ["style"]}).json()
        assert saved["text"] == "The light rises into the sky. 2D cel animation." and saved["chars"] == len(saved["text"])
        assert client.put(f"/api/shots/{s01}/prompt/video", json={"scene": "x", "block_keys": ["nope"]}).status_code == 422

        # 1) the package takes the kept prompt: nothing is typed again
        pkg = client.post("/api/external/packages", json={"version_id": v01, "prompt_use": "video", "duration_s": 4}).json()
        assert pkg["prompt"] == saved["text"] and pkg["prompt_snapshot_id"]
        # 2) a prompt pasted by hand that is exactly the kept one is linked to the same frozen text
        pasted = client.post("/api/external/packages", json={"version_id": v01, "prompt": saved["text"] + "  "}).json()
        assert pasted["prompt_snapshot_id"] == pkg["prompt_snapshot_id"]
        # 3) any other text is still allowed and stays unlinked
        free = client.post("/api/external/packages", json={"version_id": v01, "prompt": "rain on glass"}).json()
        assert free["prompt"] == "rain on glass" and free["prompt_snapshot_id"] is None
        assert client.post("/api/external/packages", json={"version_id": v02, "prompt_use": "video"}).status_code == 422  # S02 has none
        assert client.post("/api/external/packages", json={"version_id": v01}).status_code == 422  # no prompt at all

        detail = client.get(f"/api/shots/{s01}/prompt/video").json()
        assert [len(h["jobs"]) for h in detail["history"]] == [2]
        assert {j["id"] for j in detail["history"][0]["jobs"]} == {pkg["id"], pasted["id"]}

        # the film's page: every shot, every use, and how many generations used each
        page = client.get(f"/api/sequences/{seq}/prompts").json()
        assert [b["key"] for b in page["blocks"]] == ["style"] and [u["key"] for u in page["uses"]] == ["image", "image_local", "video"]
        assert [s["code"] for s in page["shots"]] == ["S01", "S02", "S03"]
        assert page["shots"][0]["prompts"]["video"]["jobs"] == 2 and page["shots"][0]["prompts"]["image"]["jobs"] == 0

        assert client.post(f"/api/sequences/{seq}/prompts/apply-blocks", json={"use": "image", "add": ["style"]}).json() == {"changed": 3}
        assert client.delete(f"/api/sequences/{seq}/prompt-blocks/style").status_code == 422
        assert client.post(f"/api/sequences/{seq}/prompts/apply-blocks", json={"use": "image", "remove": ["style"]}).json() == {"changed": 3}
        client.put(f"/api/shots/{s01}/prompt/video", json={"scene": "The light rises.", "block_keys": []})
        assert client.delete(f"/api/sequences/{seq}/prompt-blocks/style").json() == {"deleted": "style"}
        snap = client.post(f"/api/shots/{s01}/prompt/video/snapshot").json()
        assert snap["text"] == "The light rises." and snap["id"] != pkg["prompt_snapshot_id"]
    with session_scope(settings) as session:  # the earlier package still points at the text it was made from
        job = session.get(GenerationJob, pkg["id"])
        assert session.get(PromptSnapshot, job.prompt_snapshot_id).text == "The light rises into the sky. 2D cel animation."


def test_an_older_database_gets_the_prompt_column(settings):
    init_db(settings)
    engine = get_engine(settings)
    with engine.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE generation_job DROP COLUMN prompt_snapshot_id")
        assert "prompt_snapshot_id" not in {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(generation_job)")}
    init_db(settings)
    with engine.begin() as conn:
        assert "prompt_snapshot_id" in {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(generation_job)")}


def test_an_image_package_takes_one_reference_and_registers_a_still(settings, film):
    """A still made on Higgsfield goes through the same package: reference image + the shot's kept prompt out, the
    downloaded picture back in as a version with its credits."""
    seq, (s01, _s02, _s03), (v01, _v02) = film["sequence"], film["shots"], film["versions"]
    with TestClient(create_app(settings)) as client:
        client.put(f"/api/sequences/{seq}/prompt-blocks/style", json={"label": "그림체", "text": "2D cel animation.", "place": "after"})
        client.put(f"/api/shots/{s01}/prompt/image", json={"scene": "A candle.", "block_keys": ["style"]})
        pkg = client.post("/api/external/packages", json={"version_id": v01, "prompt_use": "image", "model": "nano_banana_pro"}).json()
        assert (pkg["kind"], pkg["model_name"], pkg["duration_s"], pkg["prompt"]) == ("image", "Nano Banana Pro", None, "A candle. 2D cel animation.")
        folder = Path(pkg["folder"])
        assert (folder / "reference.png").is_file() and not (folder / "start_frame.png").exists()
        assert "reference.png 한 장만" in (folder / "안내.txt").read_text(encoding="utf-8")
        assert client.get(pkg["start_frame_url"]).status_code == 200
        assert client.post("/api/external/packages", json={"version_id": v01, "prompt": "x", "model": "nano_banana_pro",
                                                           "reference_version_id": v01}).status_code == 422
        bad = client.post(f"/api/external/packages/{pkg['id']}/result", files={"file": ("clip.mp4", b"x", "video/mp4")}, data={"credits": "2"})
        assert bad.status_code == 422
        done = client.post(f"/api/external/packages/{pkg['id']}/result", files={"file": ("out.png", png_bytes(128, 72), "image/png")},
                           data={"credits": "2", "external_id": "hf-1"}).json()
        assert (done["status"], done["credits"], done["version_code"]) == ("completed", 2.0, "img_001")
        history = client.get(f"/api/shots/{s01}/prompt/image").json()["history"]
        assert history[0]["jobs"][0]["version_ids"] == [done["version_id"]] and history[0]["jobs"][0]["credits"] == 2.0
        video = client.post("/api/external/packages", json={"version_id": v01, "prompt": "rain"}).json()  # video packages are unchanged
        assert video["kind"] == "video" and (Path(video["folder"]) / "start_frame.png").is_file()
