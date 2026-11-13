"""Runs inside Blender for ShotFlow (services/blender/runner.py starts it):

    blender -b --factory-startup <scene.blend> --python-exit-code 1 --python shotflow_blender.py -- <job.json>

job.json: {"mode": "inspect" | "passes" | "layer", "out_dir": ..., "width": ..., "height": ..., "frame": ...,
           "frame_start": ..., "frame_end": ..., "samples": ...}
Results go to <out_dir>/result.json. The .blend file is opened read-only: nothing is ever saved back to it.

passes: beauty.png (the scene's own look), depth.png (near = white, normalised inverse distance, the form the
        depth ControlNet was trained on), mask.png (objects in the "SF_MASK" collection, occluded by the rest).
        Objects in the "SF_BEAUTY_ONLY" collection (glow, light beams: see-through things drawn over the scene)
        show in beauty.png only. They are left out of the mask and never hide a mask object.
layer: layer_####.png RGBA sequence on a transparent background, for compositing over a shot.
"""

from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector

MASK_COLLECTION = "SF_MASK"
BEAUTY_ONLY_COLLECTION = "SF_BEAUTY_ONLY"


def load_job() -> dict:
    return json.loads(Path(sys.argv[sys.argv.index("--") + 1]).read_text(encoding="utf-8"))


def write_result(out_dir: Path, data: dict) -> None:
    (out_dir / "result.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def scene_info(scene: bpy.types.Scene) -> dict:
    cameras = sorted(o.name for o in scene.objects if o.type == "CAMERA")
    meshes = [o for o in scene.objects if o.type == "MESH" and not o.hide_render]
    mask = bpy.data.collections.get(MASK_COLLECTION)
    return {
        "blender": bpy.app.version_string, "file": bpy.data.filepath, "scene": scene.name,
        "camera": scene.camera.name if scene.camera else None, "cameras": cameras,
        "frame_start": scene.frame_start, "frame_end": scene.frame_end, "frame_current": scene.frame_current,
        "fps": scene.render.fps / scene.render.fps_base,
        "resolution": [scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage],
        "engine": scene.render.engine, "meshes": len(meshes),
        "lights": sum(1 for o in scene.objects if o.type == "LIGHT"),
        "mask_objects": len(mask.all_objects) if mask else 0,
        "transparent": scene.render.film_transparent,
    }


def use_gpu_for_cycles(scene: bpy.types.Scene) -> str:
    """A Cycles scene renders on the graphics card when one is available (ShotFlow starts Blender without the
    user's device preferences, so Cycles would otherwise fall back to the CPU and take minutes per frame)."""
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
    except Exception:
        return "cpu"
    for kind in ("OPTIX", "CUDA", "HIP", "METAL", "ONEAPI"):
        try:
            prefs.compute_device_type = kind
            prefs.get_devices()
        except Exception:
            continue
        if any(dev.type == kind for dev in prefs.devices):
            for dev in prefs.devices:
                dev.use = dev.type == kind
            scene.cycles.device = "GPU"
            return kind.lower()
    return "cpu"


def prepare(scene: bpy.types.Scene, job: dict) -> None:
    if scene.camera is None:
        raise SystemExit("NO_CAMERA")
    if scene.render.engine == "CYCLES":
        print("SF_CYCLES_DEVICE", use_gpu_for_cycles(scene))
    if job.get("width") and job.get("height"):
        scene.render.resolution_x, scene.render.resolution_y = int(job["width"]), int(job["height"])
        scene.render.resolution_percentage = 100
    if job.get("samples") and scene.render.engine == "BLENDER_EEVEE_NEXT":
        scene.eevee.taa_render_samples = int(job["samples"])
    scene.render.use_compositing = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_depth = "8"


def render_still(scene: bpy.types.Scene, path: Path, alpha: bool) -> float:
    scene.render.image_settings.color_mode = "RGBA" if alpha else "RGB"
    scene.render.filepath = str(path)
    started = time.perf_counter()
    bpy.ops.render.render(write_still=True)
    return round(time.perf_counter() - started, 2)


def far_distance(scene: bpy.types.Scene) -> float:
    cam = scene.camera.matrix_world.translation
    far = 1.0
    for obj in scene.objects:
        if obj.type == "MESH" and not obj.hide_render:
            for corner in obj.bound_box:
                far = max(far, (obj.matrix_world @ Vector(corner) - cam).length)
    return min(far * 1.05, 1000.0)


def mist_compositor(scene: bpy.types.Scene, out_dir: Path, far: float) -> None:
    """Write the linear mist pass (distance / far) as float EXR next to the beauty render."""
    scene.view_layers[0].use_pass_mist = True
    world = scene.world or bpy.data.worlds.new("SF_World")
    scene.world = world
    world.mist_settings.start, world.mist_settings.depth, world.mist_settings.falloff = 0.0, far, "LINEAR"
    scene.use_nodes = True
    tree = scene.node_tree
    tree.nodes.clear()
    layers = tree.nodes.new("CompositorNodeRLayers")
    composite = tree.nodes.new("CompositorNodeComposite")
    tree.links.new(layers.outputs["Image"], composite.inputs["Image"])
    if "Alpha" in composite.inputs:
        tree.links.new(layers.outputs["Alpha"], composite.inputs["Alpha"])
    output = tree.nodes.new("CompositorNodeOutputFile")
    output.base_path = str(out_dir)
    output.format.file_format, output.format.color_mode, output.format.color_depth = "OPEN_EXR", "RGB", "32"
    output.file_slots[0].path = "mist_"
    tree.links.new(layers.outputs["Mist"], output.inputs[0])


def depth_from_mist(exr: Path, far: float, out: Path) -> dict:
    image = bpy.data.images.load(str(exr))
    w, h = image.size
    buf = np.empty(w * h * 4, dtype=np.float32)
    image.pixels.foreach_get(buf)
    dist = np.clip(buf.reshape(h, w, 4)[..., 0], 1e-6, 1.0) * far
    geometry = dist < far * 0.995  # mist reaches 1.0 where no surface was hit
    if not geometry.any():
        raise SystemExit("EMPTY_DEPTH")
    near_d = float(np.percentile(dist[geometry], 0.5))
    far_d = float(np.percentile(dist[geometry], 99.5))
    inv = (1.0 / dist - 1.0 / far_d) / max(1.0 / near_d - 1.0 / far_d, 1e-9)
    depth = np.clip(inv, 0.0, 1.0)
    depth[~geometry] = 0.0
    rgba = np.dstack([depth, depth, depth, np.ones_like(depth)]).astype(np.float32)
    result = bpy.data.images.new("SF_Depth", w, h, alpha=False, float_buffer=False)
    result.colorspace_settings.name = "Non-Color"  # store the numbers as they are, no display transform
    result.pixels.foreach_set(rgba.ravel())
    result.filepath_raw, result.file_format = str(out), "PNG"
    result.save()
    return {"near_m": round(near_d, 4), "far_m": round(far_d, 4), "coverage": round(float(geometry.mean()), 4)}


RENDERABLE = {"MESH", "CURVE", "SURFACE", "META", "FONT"}


def render_mask(scene: bpy.types.Scene, out: Path) -> bool:
    mask = bpy.data.collections.get(MASK_COLLECTION)
    if mask is None or not mask.all_objects:
        return False
    keep = set(mask.all_objects)
    white = bpy.data.materials.new("SF_MaskWhite")
    white.use_nodes = True
    nodes = white.node_tree.nodes
    nodes.remove(nodes["Principled BSDF"])
    emit = nodes.new("ShaderNodeEmission")
    white.node_tree.links.new(emit.outputs[0], nodes["Material Output"].inputs["Surface"])
    scene.view_layers[0].material_override = white
    beauty_only = bpy.data.collections.get(BEAUTY_ONLY_COLLECTION)
    unseen = set(beauty_only.all_objects) - keep if beauty_only else set()
    for obj in unseen:
        obj.visible_camera = False  # not a keyed property, so it holds even when the scene keys hide_render per frame
    for obj in scene.objects:
        if obj in unseen:
            continue
        if obj.type in RENDERABLE and obj not in keep:      # curves, text and metaballs render too, so they must be held out as well
            obj.is_holdout = True  # still hides the product where it is in front, but adds no pixels
    scene.render.film_transparent = True
    scene.camera.data.dof.use_dof = False
    scene.use_nodes = False
    if scene.render.engine == "BLENDER_EEVEE_NEXT":
        scene.eevee.taa_render_samples = 8
    render_still(scene, out, alpha=True)
    image = bpy.data.images.load(str(out))
    w, h = image.size
    buf = np.empty(w * h * 4, dtype=np.float32)
    image.pixels.foreach_get(buf)
    alpha = buf.reshape(h, w, 4)[..., 3]
    silhouette = bpy.data.images.new("SF_Mask", w, h, alpha=False, float_buffer=False)
    silhouette.colorspace_settings.name = "Non-Color"
    silhouette.pixels.foreach_set(np.dstack([alpha, alpha, alpha, np.ones_like(alpha)]).astype(np.float32).ravel())
    silhouette.filepath_raw, silhouette.file_format = str(out), "PNG"
    silhouette.save()  # white = product, black = everything else
    return True


def run() -> None:
    job = load_job()
    out_dir = Path(job["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    scene = bpy.context.scene
    info = scene_info(scene)
    mode = job["mode"]
    if mode == "inspect":
        if scene.camera is not None:
            prepare(scene, {"width": 640, "height": 360, "samples": 16})
            scene.frame_set(int(job.get("frame") or scene.frame_current))
            info["preview_seconds"] = render_still(scene, out_dir / "preview.png", alpha=scene.render.film_transparent)
        write_result(out_dir, {"mode": mode, "info": info})
        return

    prepare(scene, job)
    if mode == "passes":
        frame = int(job.get("frame") or scene.frame_current)
        scene.frame_set(frame)
        far = far_distance(scene)
        mist_compositor(scene, out_dir, far)
        seconds = render_still(scene, out_dir / "beauty.png", alpha=False)
        exr = out_dir / f"mist_{frame:04d}.exr"
        depth = depth_from_mist(exr, far, out_dir / "depth.png")
        exr.unlink(missing_ok=True)
        has_mask = render_mask(scene, out_dir / "mask.png")
        write_result(out_dir, {"mode": mode, "info": info, "frame": frame, "beauty_seconds": seconds,
                               "depth": depth, "mask": has_mask})
    elif mode == "layer":
        start, end = int(job["frame_start"]), int(job["frame_end"])
        scene.frame_start, scene.frame_end = start, end
        scene.render.film_transparent = True
        scene.render.image_settings.color_mode = "RGBA"
        scene.render.filepath = str(out_dir / "layer_####")
        started = time.perf_counter()
        bpy.ops.render.render(animation=True)
        write_result(out_dir, {"mode": mode, "info": info, "frame_start": start, "frame_end": end,
                               "seconds": round(time.perf_counter() - started, 2)})
    else:
        raise SystemExit(f"UNKNOWN_MODE {mode}")


try:
    run()
except SystemExit:
    raise
except Exception as exc:  # reported to ShotFlow through the exit code and the log
    print(f"SF_ERROR {type(exc).__name__}: {exc}")
    raise
