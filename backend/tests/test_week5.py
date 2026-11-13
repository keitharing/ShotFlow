"""Week 5: Blender integration, 3D guide recipe wiring, layer composites, external (Seedance) packages."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from shotflow.config import ROOT
from shotflow.db import init_db, session_scope
from shotflow.models import GenerationJob, Origin, Project, Recipe, Sequence, Shot, Version, VersionStatus
from shotflow.services import composite as comp
from shotflow.services import external as ext
from shotflow.services.blender import runner as blender
from shotflow.services.comfy import recipes as rec
from shotflow.services.comfy import runner as comfy_runner
from shotflow.services.comfy import server
from shotflow.services.media import ffmpeg_exe, probe, read_frame, sha256_file

from .conftest import FPS, H, W, moving_frames, write_video

SAMPLE = blender.SAMPLE_SCENES / "perfume_rain" / "perfume_keyframe.blend"


@pytest.fixture
def project(settings, tmp_path):
    init_db(settings)
    clip = write_video(tmp_path / "shot.mp4", moving_frames(30))
    with session_scope(settings) as session:
        p = Project(code="PRJ", name="P", fps=FPS, width=W, height=H)
        seq = Sequence(project=p, code="SEQ", name="SEQ")
        shot = Shot(sequence=seq, code="S01")
        info = probe(clip)
        v = Version(shot=shot, code="v01", path=str(clip), sha256=sha256_file(clip), width=info.width, height=info.height,
                    fps=info.fps, frame_count=info.frame_count, has_audio=True, status=VersionStatus.APPROVED)
        shot.approved_version = v
        session.add(p)
        session.flush()
        ids = {"shot": shot.id, "v01": v.id}
    return settings, ids


def _layer_job(settings, frames: int = 10) -> int:
    """A finished Blender layer job made of synthetic RGBA frames: one bright streak moving down."""
    folder = settings.data_dir / "blender" / "fake_layer"
    folder.mkdir(parents=True)
    for i in range(frames):
        rgba = np.zeros((H, W, 4), np.uint8)
        rgba[10 + i * 4: 30 + i * 4, 50:53] = (255, 255, 255, 255)
        cv2.imwrite(str(folder / f"layer_{i + 1:04d}.png"), rgba)
    with session_scope(settings) as session:
        job = GenerationJob(provider="blender", status="completed",
                            params={"purpose": "layer", "scene_name": "fake", "outputs": {"frames_dir": str(folder)}})
        session.add(job)
        session.flush()
        return job.id


# ---------------------------------------------------------------- composite maths

def test_blend_screen_never_darkens_and_leaves_clear_pixels_untouched():
    base = np.random.default_rng(1).integers(0, 255, (8, 8, 3), dtype=np.uint8)
    layer = np.zeros((8, 8, 4), np.uint8)
    assert np.array_equal(comp.blend(base, layer, 0.8, "screen"), base)  # alpha 0 -> exactly the source
    layer[...] = (200, 200, 200, 255)
    out = comp.blend(base, layer, 1.0, "screen")
    assert (out.astype(int) >= base.astype(int)).all()
    over = comp.blend(base, layer, 1.0, "over")
    assert np.array_equal(over, np.full_like(base, 200))


def test_layer_index_offset_and_loop():
    assert comp.layer_index(0, 0, 10, True) == 0
    assert comp.layer_index(12, 0, 10, True) == 2
    assert comp.layer_index(12, 0, 10, False) is None
    assert comp.layer_index(3, 5, 10, False) == 8


def test_composite_job_creates_child_version_with_audio(project):
    settings, ids = project
    layer_id = _layer_job(settings)
    with session_scope(settings) as session:
        version = session.get(Version, ids["v01"])
        layer = session.get(GenerationJob, layer_id)
        jpeg = comp.preview_png(version, layer, 3, 1.0, "screen")
        assert cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR).shape == (H, W, 3)
        job_id = comp.create_job(session, version, layer, opacity=1.0, mode="screen").id
    result = comp.run_job(job_id, settings)
    with session_scope(settings) as session:
        out = session.get(Version, result["version_id"])
        assert out.code == "v01_comp_001" and out.origin == Origin.FX and out.parent_id == ids["v01"]
        assert out.frame_count == 30 and out.has_audio and out.qc_runs[-1].status in ("pass", "warning")
        assert out.generation_job.params["layer_job_id"] == layer_id
        frame = read_frame(Path(out.path), 2)
        source = read_frame(Path(session.get(Version, ids["v01"]).path), 2)
    streak = frame[20:36, 50:53].astype(int).mean() - source[20:36, 50:53].astype(int).mean()
    elsewhere = abs(frame[60:100, 120:180].astype(int).mean() - source[60:100, 120:180].astype(int).mean())
    assert streak > 40 and elsewhere < 3  # the layer lands where it was drawn and nowhere else


def test_composite_keeps_every_frame_when_the_sound_is_shorter_than_the_picture(project, tmp_path):
    """Generated clips often carry an audio track a few frames shorter than the picture (FIRST DROP: 120 frames of
    picture, 4.92 s of sound). The composite must still come out with every source frame."""
    settings, ids = project
    picture = write_video(tmp_path / "pic.mp4", moving_frames(30), audio=None)
    short = tmp_path / "short_sound.mp4"
    subprocess.run([ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(picture), "-f", "lavfi", "-i",
                    f"sine=frequency=440:sample_rate=48000:duration={20 / FPS}", "-c:v", "copy", "-c:a", "aac", str(short)],
                   check=True)
    layer_id = _layer_job(settings)
    with session_scope(settings) as session:
        info = probe(short)
        assert info.frame_count == 30
        version = Version(shot_id=ids["shot"], code="v02", path=str(short), sha256=sha256_file(short), width=info.width,
                          height=info.height, fps=info.fps, frame_count=info.frame_count, has_audio=True)
        session.add(version)
        session.flush()
        job_id = comp.create_job(session, version, session.get(GenerationJob, layer_id), opacity=1.0, mode="over",
                                 loop=False).id
    result = comp.run_job(job_id, settings)
    with session_scope(settings) as session:
        out = session.get(Version, result["version_id"])
        assert out.frame_count == 30 and out.has_audio
        # frame 25 has no layer on it (10 layer frames, no loop): the picture must come back with the same colour,
        # otherwise a composited clip is visibly darker than the untouched cut it is joined to
        after, before = read_frame(Path(out.path), 25).astype(float), read_frame(short, 25).astype(float)
        assert np.abs(after.reshape(-1, 3).mean(axis=0) - before.reshape(-1, 3).mean(axis=0)).max() < 0.5


def test_composite_rejects_stills_and_bad_mode(project, tmp_path):
    settings, ids = project
    layer_id = _layer_job(settings)
    with session_scope(settings) as session:
        version, layer = session.get(Version, ids["v01"]), session.get(GenerationJob, layer_id)
        with pytest.raises(ValueError):
            comp.create_job(session, version, layer, opacity=0.5, mode="multiply")
        with pytest.raises(ValueError):
            comp.create_job(session, version, layer, opacity=1.5, mode="screen")


# ---------------------------------------------------------------- external generation

def test_external_package_and_result_import(project, tmp_path):
    settings, ids = project
    with session_scope(settings) as session:
        version = session.get(Version, ids["v01"])
        job = ext.create_package(session, settings, version, prompt="slow rain", model="seedance_2_5", duration_s=5)
        folder = Path(job.params["package_dir"])
        assert {p.name for p in folder.iterdir()} == {"start_frame.png", "prompt.txt", "settings.json", "안내.txt"}
        assert cv2.imread(str(folder / "start_frame.png")).shape == (1080, 1920, 3)
        assert json.loads((folder / "settings.json").read_text(encoding="utf-8"))["duration_s"] == 5
        job_id = job.id
        # a package can also carry the end frame and an identity reference, at the cut's own length
        both = ext.create_package(session, settings, version, prompt="pour", model="seedance_2_5", duration_s=4,
                                  end_version=version, reference_version=version)
        folder2 = Path(both.params["package_dir"])
        assert {"end_frame.png", "reference.png"} <= {p.name for p in folder2.iterdir()}
        packed = json.loads((folder2 / "settings.json").read_text(encoding="utf-8"))
        assert packed["duration_s"] == 4 and "end_frame" in packed and "reference" in packed
        assert "end_frame.png" in (folder2 / "안내.txt").read_text(encoding="utf-8")
        assert both.params["end_version_id"] == version.id and ext.job_out(both)["reference_version_id"] == version.id
        with pytest.raises(ValueError, match="4 ~ 15"):
            ext.create_package(session, settings, version, prompt="x", model="seedance_2_5", duration_s=3)
    payload = write_video(tmp_path / "seedance.mp4", moving_frames(24, seed=3)).read_bytes()
    with session_scope(settings) as session:
        job = session.get(GenerationJob, job_id)
        with pytest.raises(ValueError):
            ext.import_result(session, settings, job, "notes.txt", b"hello", credits=60, external_id=None)
        version = ext.import_result(session, settings, job, "result.mp4", payload, credits=60, external_id="abc-1")
        assert version.code == "sd_001" and version.parent_id == ids["v01"] and job.status == "completed"
        assert job.credits == 60 and job.external_id == "abc-1" and version.qc_runs
        with pytest.raises(ValueError):
            ext.import_result(session, settings, job, "result.mp4", payload, credits=60, external_id=None)


def test_external_package_validation(project):
    settings, ids = project
    with session_scope(settings) as session:
        version = session.get(Version, ids["v01"])
        for kwargs in ({"prompt": " ", "model": "seedance_2_5", "duration_s": 5},
                       {"prompt": "x", "model": "unknown", "duration_s": 5},
                       {"prompt": "x", "model": "seedance_2_5", "duration_s": 3},      # shorter than the service allows
                       {"prompt": "x", "model": "seedance_2_5", "duration_s": 16}):
            with pytest.raises(ValueError):
                ext.create_package(session, settings, version, **kwargs)


# ---------------------------------------------------------------- recipe + model wiring

def test_guide_recipe_needs_blender_depth(project):
    settings, ids = project
    snapshot = ROOT / "data" / "comfy" / rec.SNAPSHOT  # real ComfyUI schema when this PC has one
    object_info = json.loads(snapshot.read_text(encoding="utf-8")) if snapshot.is_file() else {}
    with session_scope(settings) as session:
        rec.sync(session, object_info)
        rv = session.query(Recipe).filter_by(kind="guide").one().versions[-1]
        shot, version = session.get(Shot, ids["shot"]), session.get(Version, ids["v01"])
        with pytest.raises(ValueError, match="깊이"):
            comfy_runner.create_jobs(session, rv, shot=shot, values={}, source_version=version)
        jobs = comfy_runner.create_jobs(session, rv, shot=shot, values={}, source_version=version, seeds=[1, 2],
                                        guide={"depth_path": "depth.png", "pass_job_id": 7})
        assert len(jobs) == 2 and jobs[0].params["guide"]["depth_path"] == "depth.png"
        assert comfy_runner.CODE_PREFIX["guide"] == "g3d"


def test_shotflow_models_folder_is_loaded_and_preferred(settings, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "MODELS_DIR", tmp_path / "models")
    (tmp_path / "models" / "controlnet").mkdir(parents=True)
    (tmp_path / "models" / "controlnet" / "x.safetensors").write_bytes(b"0")
    assert server.model_path("controlnet", "x.safetensors") == tmp_path / "models" / "controlnet" / "x.safetensors"
    assert server.model_path("checkpoints", "none.safetensors").is_relative_to(server.APP)
    server._node_args(settings)
    yaml_text = (settings.data_dir / "comfy" / "extra_paths.yaml").read_text(encoding="utf-8")
    assert f'base_path: "{(tmp_path / "models").as_posix()}"' in yaml_text and "  controlnet: controlnet" in yaml_text


# ---------------------------------------------------------------- Blender runner helpers

def test_blender_messages_and_progress(settings, monkeypatch):
    assert "카메라" in blender._friendly("...\nNO_CAMERA\n", 1)
    assert "SF_ERROR" not in blender._friendly("SF_ERROR KeyError: 'x'\n", 1)
    seen = []
    monkeypatch.setattr(blender, "_set", lambda s, j, **f: seen.append(f["params"]))
    reader = blender._progress_reader(settings, 1, "layer", 4)
    for _ in range(4):
        reader("Saved: 'x.png'\n")
    assert seen[-1]["stage"] == "레이어 렌더 4/4프레임"
    reader = blender._progress_reader(settings, 1, "passes", 1)
    reader("Rendering 64 / 64 samples\n")
    assert seen[-1]["stage"] == "일반 렌더"


def test_blender_job_validation(project):
    settings, ids = project
    with session_scope(settings) as session:
        shot = session.get(Shot, ids["shot"])
        with pytest.raises(ValueError):
            blender.create_job(session, "passes", SAMPLE, shot=None)
        with pytest.raises(ValueError):
            blender.create_job(session, "layer", SAMPLE, frame_start=10, frame_end=5)
        with pytest.raises(ValueError):
            blender.create_job(session, "passes", Path("missing.blend"), shot=shot)
        job = blender.create_job(session, "passes", SAMPLE, shot=shot, frame=1)
        assert job.provider == "blender" and job.status == "queued"


def test_api_scene_routes(project):
    from shotflow.api import create_app

    settings, ids = project
    client = TestClient(create_app(settings))
    assert "installed" in client.get("/api/blender/status").json()
    names = {s["name"] for s in client.get("/api/blender/scenes").json()}
    assert {"perfume_keyframe", "rain_layer_r05"} <= names
    assert client.post("/api/blender/scenes/inspect", json={"path": "C:/nope.txt"}).status_code == 422
    layer_id = _layer_job(settings)
    assert client.post("/api/blender/guide", json={"pass_job_id": layer_id}).status_code == 422
    body = {"version_id": ids["v01"], "layer_job_id": layer_id, "opacity": 0.7, "mode": "screen", "frame": 2}
    assert client.post("/api/composite/preview", json=body).headers["content-type"] == "image/jpeg"
    pkg = client.post("/api/external/packages", json={"version_id": ids["v01"], "prompt": "rain"}).json()
    assert client.get(pkg["start_frame_url"]).status_code == 200
    assert [p["id"] for p in client.get("/api/external/packages").json()] == [pkg["id"]]


# ---------------------------------------------------------------- real Blender (skipped when not installed)

@pytest.mark.skipif(blender.blender_exe() is None, reason="Blender is not installed on this machine")
def test_real_blender_passes_register_beauty_depth_and_mask(project):
    settings, ids = project
    with session_scope(settings) as session:
        job_id = blender.create_job(session, "passes", SAMPLE, shot=session.get(Shot, ids["shot"]), frame=60,
                                    width=320, height=180).id
    blender.run_job(job_id, settings)
    with session_scope(settings) as session:
        job = session.get(GenerationJob, job_id)
        assert job.status == "completed", job.error
        beauty = session.get(Version, job.params["registered"]["beauty"])
        assert beauty.origin == Origin.BLENDER and beauty.code == "b3d_001" and (beauty.width, beauty.height) == (320, 180)
        depth = cv2.imread(job.params["outputs"]["depth"], cv2.IMREAD_GRAYSCALE)
        mask = cv2.imread(job.params["outputs"]["mask"], cv2.IMREAD_GRAYSCALE)
    assert depth.shape == (180, 320) and depth.max() > 200 and depth.min() < 30
    assert set(np.unique(mask)) >= {0, 255}
    assert mask[90, 160] > 200 and mask[10, 10] < 30  # bottle in the middle, background outside
