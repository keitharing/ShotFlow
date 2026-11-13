"""Builds a blocking scene in Blender from ShotFlow's layout solution (services/layout.py starts it):

    blender -b --factory-startup --python-exit-code 1 --python shotflow_layout.py -- <job.json>

job.json: {"width", "height", "fps", "frame_count", "lens_mm", "cameras": [{"role", "location", "rotation_deg"}],
           "objects": [{"label", "kind", "location", "size", "mask", "grounded"}], "preview": [w, h],
           "out_blend", "out_dir", "marks"}
Writes a NEW .blend (never one that exists), then renders one preview per camera key for the overlay check.
Proxies are grey stand-ins; objects marked "mask" go to the SF_MASK collection so the 3D keyframe step can cut them out.
"""

from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

import bpy

MASK_COLLECTION = "SF_MASK"


def load_job() -> dict:
    return json.loads(Path(sys.argv[sys.argv.index("--") + 1]).read_text(encoding="utf-8"))


def material(name: str, rgb: tuple[float, float, float], rough: float = 0.6) -> bpy.types.Material:
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = rough
    return mat


def collection(name: str) -> bpy.types.Collection:
    coll = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(coll)
    return coll


def join(parts: list[bpy.types.Object], name: str) -> bpy.types.Object:
    bpy.ops.object.select_all(action="DESELECT")
    for part in parts:
        part.select_set(True)
    bpy.context.view_layer.objects.active = parts[0]
    if len(parts) > 1:
        bpy.ops.object.join()
    ob = bpy.context.view_layer.objects.active
    ob.name = name
    return ob


def proxy(kind: str, label: str, loc: list[float], size: list[float]) -> bpy.types.Object:
    """A grey stand-in standing on loc (bottom centre). size = width, depth, height."""
    x, y, z = loc
    w, d, h = (max(v, 0.002) for v in size)
    r = w / 2
    parts = []
    if kind == "bottle":
        bpy.ops.mesh.primitive_cylinder_add(vertices=48, radius=r, depth=0.6 * h, location=(x, y, z + 0.3 * h))
        parts.append(bpy.context.object)
        bpy.ops.mesh.primitive_cone_add(vertices=48, radius1=r, radius2=0.36 * r, depth=0.15 * h, location=(x, y, z + 0.675 * h))
        parts.append(bpy.context.object)
        bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=0.34 * r, depth=0.25 * h, location=(x, y, z + 0.875 * h))
        parts.append(bpy.context.object)
    elif kind == "person":
        bpy.ops.mesh.primitive_cylinder_add(vertices=24, radius=r * 0.8, depth=0.82 * h, location=(x, y, z + 0.41 * h))
        parts.append(bpy.context.object)
        parts[-1].scale.y = d / w
        bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=12, radius=0.09 * h, location=(x, y, z + 0.91 * h))
        parts.append(bpy.context.object)
    elif kind == "round":
        bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, radius=1.0, location=(x, y, z + h / 2))
        parts.append(bpy.context.object)
        parts[-1].scale = (w / 2, d / 2, h / 2)
    else:  # box, building, other
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=(x, y, z + h / 2))
        parts.append(bpy.context.object)
        parts[-1].scale = (w, d, h)
    for part in parts:
        bpy.context.view_layer.objects.active = part
        part.select_set(True)
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        part.select_set(False)
    ob = join(parts, label)
    for poly in ob.data.polygons:
        poly.use_smooth = kind in ("bottle", "round", "person")
    ob["sf_kind"] = kind
    return ob


def run() -> None:
    job = load_job()
    out_dir = Path(job["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    blend = Path(job["out_blend"])
    if blend.exists():
        raise SystemExit("BLEND_EXISTS")
    started = time.perf_counter()

    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.name = "SF_Layout"
    scene.render.engine = "BLENDER_EEVEE_NEXT"
    scene.render.resolution_x, scene.render.resolution_y = int(job["width"]), int(job["height"])
    scene.render.resolution_percentage = 100
    scene.render.fps = max(1, round(float(job["fps"])))
    scene.frame_start, scene.frame_end = 1, max(1, int(job["frame_count"]))
    world = bpy.data.worlds.new("SF_World")
    world.use_nodes = False
    world.color = (0.32, 0.33, 0.35)
    scene.world = world

    c_set, c_mask, c_cam = collection("SF_LAYOUT"), collection(MASK_COLLECTION), collection("SF_CAMERA")
    grey, key = material("SF_Proxy", (0.62, 0.62, 0.6)), material("SF_Main", (0.85, 0.55, 0.3), 0.4)

    bpy.ops.mesh.primitive_plane_add(size=200.0, location=(0, 0, 0))
    ground = bpy.context.object
    ground.name = "SF_Ground"
    ground.data.materials.append(material("SF_Ground", (0.45, 0.45, 0.44), 0.8))
    for c in list(ground.users_collection):
        c.objects.unlink(ground)
    c_set.objects.link(ground)

    for ob_spec in job["objects"]:
        ob = proxy(ob_spec["kind"], ob_spec["label"], ob_spec["location"], ob_spec["size"])
        ob.data.materials.append(key if ob_spec.get("mask") else grey)
        ob["sf_anchor"] = bool(ob_spec.get("anchor"))
        for c in list(ob.users_collection):
            c.objects.unlink(ob)
        (c_mask if ob_spec.get("mask") else c_set).objects.link(ob)

    cam_data = bpy.data.cameras.new("SF_Camera")
    cam_data.lens, cam_data.sensor_width, cam_data.sensor_fit = float(job["lens_mm"]), 36.0, "HORIZONTAL"
    cam_data.clip_start, cam_data.clip_end = 0.005, 1000.0
    cam = bpy.data.objects.new("SF_Camera", cam_data)
    c_cam.objects.link(cam)
    scene.camera = cam
    keys = {"start": scene.frame_start, "end": scene.frame_end}
    for spec in job["cameras"]:
        cam.location = spec["location"]
        cam.rotation_euler = [math.radians(a) for a in spec["rotation_deg"]]
        if len(job["cameras"]) > 1:
            cam.keyframe_insert("location", frame=keys[spec["role"]])
            cam.keyframe_insert("rotation_euler", frame=keys[spec["role"]])

    sun_data = bpy.data.lights.new("SF_Sun", "SUN")
    sun_data.energy, sun_data.angle = 3.0, math.radians(8)
    sun = bpy.data.objects.new("SF_Sun", sun_data)
    sun.rotation_euler = (math.radians(50), 0, math.radians(35))
    c_set.objects.link(sun)
    scene["sf_layout_marks"] = json.dumps(job.get("marks") or {}, ensure_ascii=False)

    bpy.ops.wm.save_as_mainfile(filepath=str(blend))

    # Previews at the drawing's shape so they can be laid over the animatic frame (not saved into the file).
    scene.render.resolution_x, scene.render.resolution_y = (int(v) for v in job["preview"])
    scene.eevee.taa_render_samples = 16
    scene.render.image_settings.file_format = "PNG"
    previews = {}
    for spec in job["cameras"]:
        scene.frame_set(keys[spec["role"]])
        path = out_dir / f"preview_{spec['role']}.png"
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        previews[spec["role"]] = str(path)

    info = {"blender": bpy.app.version_string, "file": str(blend), "scene": scene.name, "camera": cam.name,
            "cameras": [cam.name], "frame_start": scene.frame_start, "frame_end": scene.frame_end,
            "frame_current": scene.frame_start, "fps": float(scene.render.fps),
            "resolution": [int(job["width"]), int(job["height"]), 100], "engine": scene.render.engine,
            "meshes": sum(1 for o in scene.objects if o.type == "MESH"), "lights": 1,
            "mask_objects": len(c_mask.all_objects), "transparent": False}
    (out_dir / "result.json").write_text(json.dumps({"mode": "layout", "info": info, "previews": previews,
                                                      "seconds": round(time.perf_counter() - started, 2)},
                                                     ensure_ascii=False, indent=1), encoding="utf-8")


try:
    run()
except SystemExit:
    raise
except Exception as exc:  # reported to ShotFlow through the exit code and the log
    print(f"SF_ERROR {type(exc).__name__}: {exc}")
    raise
