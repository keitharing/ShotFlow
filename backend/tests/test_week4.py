"""Week 4: ComfyUI node bridge, publishing, delivery packages, production stats."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from shotflow.config import ROOT
from shotflow.db import init_db, session_scope
from shotflow.models import Delivery, Project, Sequence, Shot, Version, VersionStatus
from shotflow.services import delivery as dlv
from shotflow.services.comfy import publish as pub
from shotflow.services.comfy import server
from shotflow.services.media import probe, sha256_file

from .conftest import FPS, moving_frames, write_video

NODES = ROOT / "integrations" / "comfyui"


@pytest.fixture
def core(monkeypatch):
    monkeypatch.syspath_prepend(str(NODES))
    module = importlib.import_module("shotflow_nodes.core")
    monkeypatch.setattr(module, "SHOTFLOW_URL", "http://127.0.0.1:9")  # nothing listens here
    return module


@pytest.fixture
def project(settings, tmp_path):
    init_db(settings)
    clip = write_video(tmp_path / "a.mp4", moving_frames(24))
    with session_scope(settings) as session:
        p = Project(code="PRJ", name="P", fps=FPS, width=192, height=108)
        seq = Sequence(project=p, code="SEQ A", name="SEQ A")
        shot = Shot(sequence=seq, code="S01")
        edit_shot = Shot(sequence=seq, code="EDIT", kind="edit", order=99)
        info = probe(clip)
        v = Version(shot=shot, code="v01", path=str(clip), sha256=sha256_file(clip), width=info.width,
                    height=info.height, fps=info.fps, frame_count=info.frame_count, status=VersionStatus.APPROVED)
        shot.approved_version = v
        draft = Version(shot=shot, code="v02", path=str(clip), sha256=sha256_file(clip), status=VersionStatus.IN_REVIEW)
        session.add_all([p, edit_shot, draft])
        session.flush()
        ids = {"shot": shot.id, "edit_shot": edit_shot.id, "v01": v.id, "v02": draft.id, "seq": seq.id}
    return settings, ids


def _png(color=(30, 120, 200), size=(96, 54)) -> bytes:
    img = np.zeros((size[1], size[0], 3), np.uint8)
    img[:] = color
    cv2.circle(img, (40, 27), 10, (240, 240, 240), -1)
    return cv2.imencode(".png", img)[1].tobytes()


# ---------------------------------------------------------------- node helpers

def test_parse_id_accepts_labels_and_numbers(core):
    assert core.parse_id("RAIN ATLAS · R05 · v01 · #14") == 14
    assert core.parse_id("27") == 27 and core.parse_id(5) == 5
    with pytest.raises(core.ShotFlowError):
        core.parse_id("R05 v01")


def test_png_array_roundtrip_is_lossless(core):
    rng = np.random.default_rng(1)
    pixels = rng.integers(0, 256, (20, 30, 3), dtype=np.uint8)
    array = pixels.astype(np.float32) / 255.0
    back = core.png_to_array(core.array_to_png(array))
    assert back.shape == (20, 30, 3) and np.array_equal(np.rint(back * 255).astype(np.uint8), pixels)


def test_catalog_is_empty_when_shotflow_is_down_and_nodes_fall_back(core, monkeypatch):
    assert core.catalog("versions") == []
    nodes = importlib.import_module("shotflow_nodes")
    monkeypatch.setattr(nodes.core, "SHOTFLOW_URL", "http://127.0.0.1:9")
    assert nodes.ShotFlowLoadVersion.INPUT_TYPES()["required"]["version"][0] == "STRING"
    assert set(nodes.NODE_CLASS_MAPPINGS) == {"ShotFlowLoadVersion", "ShotFlowPublish"}
    assert nodes.ShotFlowPublish.OUTPUT_NODE


def test_multipart_body_contains_fields_and_files(core):
    body, ctype = core._multipart({"note": "메모"}, [("files", "a.png", b"\x89PNGdata")])
    boundary = ctype.split("boundary=")[1]
    assert body.count(boundary.encode()) == 3
    assert "메모".encode() in body and b"\x89PNGdata" in body


def test_launch_args_load_only_shotflow_nodes(settings):
    args = server._node_args(settings)
    assert args[-2:] == ["--whitelist-custom-nodes", "shotflow_nodes"]
    yaml_text = Path(args[1]).read_text(encoding="utf-8")
    assert f'custom_nodes: "{NODES.as_posix()}"' in yaml_text
    assert Path(args[1]).resolve().is_relative_to(settings.data_dir)


# ---------------------------------------------------------------- publishing

def test_publish_registers_versions_with_graph_parent_and_note(project):
    settings, ids = project
    graph = {"1": {"class_type": "KSampler", "inputs": {"seed": 77, "model": ["2", 0]}},
             "2": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd15.safetensors"}},
             "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "rain", "clip": ["2", 1]}}}
    with session_scope(settings) as session:
        versions = pub.register_published(session, settings, session.get(Shot, ids["shot"]),
                                          [("a.png", _png()), ("b.png", _png((90, 20, 20)))],
                                          graph=graph, workflow={"nodes": []}, parent_id=ids["v01"],
                                          note="노드에서 등록", author="tester")
        assert [v.code for v in versions] == ["pub_001", "pub_002"]
        job = versions[0].generation_job
        assert (job.provider, job.seed, job.model, job.prompt) == ("comfyui-node", 77, "sd15.safetensors", "rain")
        assert versions[0].parent_id == ids["v01"] and versions[0].notes[0].body == "노드에서 등록"
        assert versions[0].qc_runs[-1].status in ("pass", "warning")
        folder = Path(versions[0].path).parent
        assert (folder / "workflow.json").is_file() and json.loads((folder / "graph.json").read_text(encoding="utf-8"))["1"]
        assert folder.resolve().is_relative_to(settings.data_dir)


def test_publish_rejects_bad_targets(project):
    settings, ids = project
    with session_scope(settings) as session:
        shot, edit_shot = session.get(Shot, ids["shot"]), session.get(Shot, ids["edit_shot"])
        for kwargs, target in [({}, edit_shot), ({"parent_id": 99999}, shot)]:
            with pytest.raises(ValueError):
                pub.register_published(session, settings, target, [("a.png", _png())], graph={}, workflow=None,
                                       parent_id=kwargs.get("parent_id"), note="", author="t")
        with pytest.raises(ValueError):
            pub.register_published(session, settings, shot, [("a.png", b"not an image")], graph={}, workflow=None,
                                   parent_id=None, note="", author="t")


def test_publish_api_and_catalog(project):
    from shotflow.api import create_app

    settings, ids = project
    client = TestClient(create_app(settings))
    shots = client.get("/api/catalog/shots").json()
    assert shots == [{"id": ids["shot"], "label": f"SEQ A · S01 · #{ids['shot']}"}]
    assert any(v["label"].endswith(f"#{ids['v01']}") for v in client.get("/api/catalog/versions").json())
    res = client.post(f"/api/shots/{ids['shot']}/publish",
                      files=[("files", ("x.png", _png(), "image/png"))],
                      data={"graph": "{}", "note": "api", "parent_version_id": str(ids["v01"])})
    assert res.status_code == 201 and res.json()["versions"][0]["code"] == "pub_001"
    jobs = client.get("/api/generation/jobs").json()
    assert jobs[0]["purpose"] == "publish" and jobs[0]["outputs"][0]["version_id"] == res.json()["versions"][0]["id"]
    bad = client.post(f"/api/shots/{ids['edit_shot']}/publish", files=[("files", ("x.png", _png(), "image/png"))])
    assert bad.status_code == 422


# ---------------------------------------------------------------- deliveries

def test_delivery_copies_approved_only_and_verifies(project):
    settings, ids = project
    with session_scope(settings) as session:
        versions = dlv.collect(session, "approved_shots", [ids["seq"]])
        assert [v.id for v in versions] == [ids["v01"]]
        with pytest.raises(ValueError):
            dlv.collect(session, "versions", version_ids=[ids["v02"]])  # not approved
        with pytest.raises(ValueError):
            dlv.collect(session, "approved_edits", [ids["seq"]])  # nothing approved there
        d = dlv.create(session, settings, "first delivery", versions)
        folder = Path(d.target_dir)
        assert d.items[0].file_name == "PRJ_SEQ_A_S01_v01.mp4"
        assert {p.name for p in folder.iterdir()} == {"PRJ_SEQ_A_S01_v01.mp4", "manifest.json", "manifest.csv", "checksums.sha256"}
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["items"][0]["version"]["status"] == "approved"
        assert dlv.verify(session, d)["ok"]

        target = folder / d.items[0].file_name
        target.write_bytes(target.read_bytes()[:-1] + b"\x00")  # one altered byte
        (folder / "stray.txt").write_text("x")
        report = dlv.verify(session, d)
        assert not report["ok"] and report["results"][0]["status"] == "changed" and report["extra_files"] == ["stray.txt"]
        target.unlink()
        assert dlv.verify(session, d)["results"][0]["status"] == "missing"
        assert session.get(Delivery, d.id).verified is False


def test_delivery_and_stats_api(project):
    from shotflow.api import create_app

    settings, ids = project
    client = TestClient(create_app(settings))
    assert client.post("/api/deliveries", json={"name": "x", "scope": "versions", "version_ids": [ids["v02"]]}).status_code == 422
    created = client.post("/api/deliveries", json={"name": "pkg", "scope": "approved_shots"}).json()
    assert created["files"] == 1 and created["verified"]
    assert client.post(f"/api/deliveries/{created['id']}/verify").json()["ok"]
    stats = client.get("/api/stats/production").json()
    assert stats["deliveries"] == 1 and stats["local_jobs"] == 0
