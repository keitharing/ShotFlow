"""Week 3: recipes, generation jobs and mask fixes against a fake ComfyUI (no GPU needed)."""

from __future__ import annotations

import base64
import json
import shutil
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from shotflow.config import ROOT
from shotflow.db import init_db, session_scope
from shotflow.models import GenerationJob, Project, Recipe, Sequence, Shot, Version, VersionStatus
from shotflow.services.comfy import fix as fixlib
from shotflow.services.comfy import recipes as rec
from shotflow.services.comfy import runner
from shotflow.services.comfy.client import ComfyError
from shotflow.services.media import probe, sha256_file
from shotflow.services.worker import worker

from .conftest import FPS, moving_frames, write_video

SNAPSHOT = ROOT / "data" / "comfy" / "object_info_snapshot.json"


@pytest.fixture
def object_info():
    if not SNAPSHOT.is_file():
        pytest.skip("ComfyUI object_info snapshot not captured on this machine")
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


class FakeComfy:
    """Stands in for ComfyClient: records graphs and returns a deterministic image/frames."""

    instances: list[FakeComfy] = []
    fail_oom_once = False

    def __init__(self, url: str):
        self.graphs: list[dict] = []
        self.uploads: list[str] = []
        FakeComfy.instances.append(self)

    def upload(self, path, subfolder, kind="input"):
        self.uploads.append(Path(path).name)
        return f"{subfolder}/{Path(path).name}"

    def queue(self, graph):
        if FakeComfy.fail_oom_once:
            FakeComfy.fail_oom_once = False
            raise ComfyError("CUDA out of memory", {"exception_message": "Allocation on device"})
        self.graphs.append(graph)
        return f"prompt{len(self.graphs)}"

    def wait(self, prompt_id, on_progress=None, should_abort=None, timeout=0):
        if on_progress:
            on_progress(1.0, None)
        graph = self.graphs[-1]
        out = next(k for k, v in graph.items() if v["class_type"] == "SaveImage")
        frames = 5 if any(v["class_type"] == "EmptyHunyuanLatentVideo" or v["class_type"] == "LoadVideo"
                          for v in graph.values()) else 1
        return {"outputs": {out: {"images": [{"filename": f"f{i}.png", "subfolder": "", "type": "output"}
                                              for i in range(frames)]}}}

    def view(self, filename, subfolder, kind="output"):
        img = np.full((64, 64, 3), 90 + 10 * int(filename[1]), np.uint8)
        cv2.circle(img, (32, 32), 12, (20, 200, 240), -1)
        return cv2.imencode(".png", img)[1].tobytes()

    def free(self):
        pass

    def close(self):
        pass


@pytest.fixture
def gen_env(settings, tmp_path, monkeypatch, object_info):
    init_db(settings)
    monkeypatch.setattr(runner, "ComfyClient", FakeComfy)
    monkeypatch.setattr(runner.server, "start", lambda s=None, wait=True: {"url": "http://fake"})
    monkeypatch.setattr(runner, "_model_hashes", lambda models, s: [{"file": m["file"], "sha256": "x"} for m in models])
    FakeComfy.instances.clear()
    clip = write_video(tmp_path / "shot.mp4", moving_frames(48))
    with session_scope(settings) as session:
        project = Project(code="T", name="T", fps=FPS, width=192, height=108)
        seq = Sequence(project=project, code="S", name="S")
        shot = Shot(sequence=seq, code="A01")
        info = probe(clip)
        version = Version(shot=shot, code="v01", path=str(clip), sha256=sha256_file(clip), width=info.width,
                          height=info.height, fps=info.fps, frame_count=info.frame_count, has_audio=True,
                          status=VersionStatus.APPROVED)
        shot.approved_version = version
        session.add(project)
        session.flush()
        rec.sync(session, object_info)
        ids = {"shot": shot.id, "version": version.id}
    return settings, ids


def _rv(session, key):
    return session.query(Recipe).filter_by(name=key).one().versions[-1]


def test_recipe_exposure_uses_sf_titles_and_schema(object_info):
    meta, api, _ = rec.load_dir(rec.RECIPES_DIR / "keyframe_sd15")
    exposed = {e.key: e for e in rec.exposed_inputs(api, object_info, meta["expose"])}
    assert set(exposed) == {"2.text", "3.text", "4.width", "4.height", "4.batch_size",
                            "5.seed", "5.steps", "5.cfg", "7.denoise"}
    assert exposed["5.cfg"].type == "FLOAT" and exposed["5.cfg"].max == 100.0
    assert exposed["2.text"].multiline
    with pytest.raises(ValueError):
        rec.validate_values(list(exposed.values()), {"7.denoise": 1.5})
    with pytest.raises(ValueError):
        rec.validate_values(list(exposed.values()), {"1.ckpt_name": "x"})  # not exposed


def test_every_recipe_uses_only_installed_nodes_and_models(object_info):
    for folder in rec.RECIPES_DIR.iterdir():
        meta, api, _ = rec.load_dir(folder)
        assert all(node["class_type"] in object_info for node in api.values()), folder.name
        assert all(m["present"] for m in rec.required_models(api)), folder.name
        rec.find_node(api, meta["output"]["node_title"])
        for spec in (meta.get("bindings") or {}).values():
            rec.find_node(api, spec["node_title"])


def test_recipe_version_bumps_only_when_files_change(gen_env, object_info, tmp_path):
    settings, _ = gen_env
    copy_dir = tmp_path / "recipes"
    shutil.copytree(rec.RECIPES_DIR / "keyframe_sd15", copy_dir / "keyframe_sd15")
    with session_scope(settings) as session:
        assert rec.sync(session, object_info, copy_dir)[0]["status"] == "unchanged"
        api_path = copy_dir / "keyframe_sd15" / "api.json"
        api_path.write_text(api_path.read_text(encoding="utf-8").replace('"steps": 20', '"steps": 22'), encoding="utf-8")
        assert rec.sync(session, object_info, copy_dir)[0] == {"recipe": "keyframe_sd15", "version": 2, "status": "registered"}


def test_grid_creates_seed_by_strength_jobs_and_limits(gen_env):
    settings, ids = gen_env
    with session_scope(settings) as session:
        shot, src = session.get(Shot, ids["shot"]), session.get(Version, ids["version"])
        jobs = runner.create_jobs(session, _rv(session, "img2img_sd15"), shot=shot, values={},
                                  seeds=[1, 2], strengths=[0.3, 0.5], source_version=src, source_frame=10)
        combos = sorted((j.seed, j.params["values"]["7.denoise"]) for j in jobs)
        assert combos == [(1, 0.3), (1, 0.5), (2, 0.3), (2, 0.5)] and len({j.batch_id for j in jobs}) == 1
        with pytest.raises(ValueError):
            runner.create_jobs(session, _rv(session, "img2img_sd15"), shot=shot, values={},
                               seeds=list(range(13)), source_version=src)
        with pytest.raises(ValueError):  # needs a source version
            runner.create_jobs(session, _rv(session, "img2img_sd15"), shot=shot, values={})
        with pytest.raises(ValueError):  # 4n+1 frame rule
            runner.create_jobs(session, _rv(session, "wan_t2v"), shot=shot, values={"7.length": 18})
        with pytest.raises(ValueError):  # area limit for 6GB
            runner.create_jobs(session, _rv(session, "wan_t2v"), shot=shot, values={"7.width": 512, "7.height": 512})


def test_job_runs_binds_source_and_registers_version(gen_env):
    settings, ids = gen_env
    with session_scope(settings) as session:
        jobs = runner.create_jobs(session, _rv(session, "img2img_sd15"), shot=session.get(Shot, ids["shot"]),
                                  values={"2.text": "test prompt"}, seeds=[5],
                                  source_version=session.get(Version, ids["version"]), source_frame=12)
        job_id = jobs[0].id
    result = runner.run_job(job_id, settings)
    assert result["status"] == "completed"
    fake = FakeComfy.instances[-1]
    graph = fake.graphs[-1]
    assert graph["4"]["inputs"]["image"].endswith("source.png") and graph["7"]["inputs"]["seed"] == 5
    assert graph["2"]["inputs"]["text"] == "test prompt"
    version_id = runner.register_output(job_id, 0, settings)
    assert runner.register_output(job_id, 0, settings) == version_id  # idempotent
    with session_scope(settings) as session:
        v = session.get(Version, version_id)
        assert v.code == "i2i_001" and v.parent_id == ids["version"] and v.generation_job_id == job_id
        assert Path(v.path).resolve().is_relative_to(settings.data_dir)
        assert v.qc_runs[-1].status in ("pass", "warning")


def test_video_recipe_encodes_frames_to_mp4(gen_env):
    settings, ids = gen_env
    with session_scope(settings) as session:
        job_id = runner.create_jobs(session, _rv(session, "wan_t2v"), shot=session.get(Shot, ids["shot"]),
                                    values={}, seeds=[3])[0].id
    runner.run_job(job_id, settings)
    with session_scope(settings) as session:
        out = Path(session.get(GenerationJob, job_id).params["outputs"][0])
    assert out.suffix == ".mp4" and probe(out).frame_count == 5 and probe(out).fps == 16


def test_out_of_memory_retries_once_with_smaller_size(gen_env):
    settings, ids = gen_env
    FakeComfy.fail_oom_once = True
    with session_scope(settings) as session:
        job_id = runner.create_jobs(session, _rv(session, "keyframe_sd15"), shot=session.get(Shot, ids["shot"]),
                                    values={}, seeds=[1])[0].id
    runner.run_job(job_id, settings)
    with session_scope(settings) as session:
        job = session.get(GenerationJob, job_id)
        assert job.status == "completed" and job.params["oom_retry"] == ["width 512→384", "height 288→216"]
    assert FakeComfy.instances[-1].graphs[-1]["4"]["inputs"]["width"] == 384


def test_cancel_queued_job_and_recover_interrupted(gen_env):
    settings, ids = gen_env
    with session_scope(settings) as session:
        a, b = runner.create_jobs(session, _rv(session, "keyframe_sd15"), shot=session.get(Shot, ids["shot"]),
                                  values={}, seeds=[1, 2])
        runner.cancel(session, a)
        b.status = "running"
        a_id, b_id = a.id, b.id
    assert runner.run_job(a_id, settings)["status"] == "cancelled"
    assert runner.recover_interrupted(settings) == 1
    with session_scope(settings) as session:
        assert session.get(GenerationJob, b_id).status == "interrupted"


def test_fix_stitch_never_touches_pixels_outside_mask(tmp_path, settings):
    rng = np.random.default_rng(0)
    frame = rng.integers(0, 255, (270, 480, 3), dtype=np.uint8)
    mask = np.zeros((270, 480, 4), np.uint8)
    cv2.ellipse(mask, (300, 120), (60, 40), 0, 0, 360, (255, 255, 255, 255), -1)
    prep = fixlib.prepare(frame, cv2.imencode(".png", mask)[1].tobytes(), settings.data_dir / "fix" / "t", 128, settings)
    assert prep.box[2] - prep.box[0] == prep.box[3] - prep.box[1] and prep.work_size == [128, 128]
    result = np.full((128, 128, 3), 255, np.uint8)  # wildly different patch
    out = fixlib.stitch(prep, result, settings.data_dir / "fix" / "t" / "out.png", settings)
    check = fixlib.protection_check(frame, fixlib.imread(out), fixlib.imread_gray(Path(prep.mask_path)))
    assert check["outside_changed"] == 0 and check["inside_changed"] > 0.9 * check["inside_pixels"]


def test_tone_match_restores_base_tone_keeps_detail():
    # Real fix regions are hundreds of pixels wide; a 3px rain streak is high-frequency detail there.
    region = np.full((256, 256, 3), 40.0, np.float32)
    patch = np.full((256, 256, 3), 90.0, np.float32)
    patch[126:129, :, :] = 200.0
    out = fixlib.tone_match(patch, region, 1.0)
    assert abs(out[20:80, 20:80].mean() - 40.0) < 3  # base tone back to the source
    assert out[127, 128].mean() - out[40, 128].mean() > 80  # streak survives


def test_api_generate_fix_and_register(gen_env, monkeypatch):
    from shotflow.api import create_app

    settings, ids = gen_env
    client = TestClient(create_app(settings))
    recipes = {r["key"]: r for r in client.get("/api/recipes").json()}
    assert set(recipes) == {"keyframe_sd15", "img2img_sd15", "inpaint_sd15", "wan_t2v", "wan_v2v", "guide_depth_sd15"}
    assert client.post("/api/generate", json={"recipe_version_id": recipes["wan_t2v"]["latest_version_id"],
                                              "shot_id": ids["shot"], "values": {"7.length": 18}}).status_code == 422
    jobs = client.post("/api/generate", json={"recipe_version_id": recipes["keyframe_sd15"]["latest_version_id"],
                                              "shot_id": ids["shot"], "seeds": [1, 2]}).json()
    worker.wait_idle(timeout=60)
    done = [client.get(f"/api/generation/jobs/{j['id']}").json() for j in jobs]
    assert [d["status"] for d in done] == ["completed", "completed"]
    assert client.get(done[0]["outputs"][0]["url"]).headers["content-type"] == "image/png"
    reg = client.post(f"/api/generation/jobs/{jobs[0]['id']}/register/0").json()
    assert client.get(f"/api/versions/{reg['version_id']}").json()["media_kind"] == "image"

    mask = np.zeros((108, 192, 4), np.uint8)
    cv2.rectangle(mask, (20, 20), (60, 60), (255, 255, 255, 255), -1)
    payload = {"frame": 10, "mask_png": base64.b64encode(cv2.imencode(".png", mask)[1].tobytes()).decode(),
               "seeds": [4]}
    fix_jobs = client.post(f"/api/versions/{ids['version']}/fix", json=payload).json()
    worker.wait_idle(timeout=60)
    fixed = client.get(f"/api/generation/jobs/{fix_jobs[0]['id']}").json()
    fixed_version = fixed["outputs"][0]["version_id"]
    detail = client.get(f"/api/versions/{fixed_version}").json()
    assert detail["code"] == "v01_f10_fix_a" and detail["parent_id"] == ids["version"]
    protect = next(i for i in detail["qc"]["issues"] if i["check"] == "protect")
    assert protect["severity"] == "info" and protect["value"] == 0
    assert client.get(f"/api/versions/{ids['version']}/frame/10").headers["content-type"] == "image/png"
