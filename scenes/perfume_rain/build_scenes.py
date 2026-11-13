"""Builds the two sample Blender scenes used by ShotFlow's 3D integration (run inside Blender).

    blender -b --factory-startup --python build_scenes.py -- <output folder>

perfume_keyframe.blend  glass perfume bottle, amber liquid, wet floor, green studio, rain - a 3D guide for AI keyframes
rain_layer_r05.blend    slow-motion rain only (transparent background) for compositing over RAIN ATLAS R05

Both use the same camera, matched by eye to R05 (bottle body x 650-1220 px, base y 880 px, cap top y 140 px at
1920x1080). The rain scene carries holdout proxies of that bottle and the water surface so drops behind the
bottle are hidden. Real product assets can replace the bottle; keep it in the "SF_MASK" collection for masks.
"""

from __future__ import annotations

import math
import random
import sys
from pathlib import Path

import bmesh
import bpy
from mathutils import Vector

MM = 0.001
CAMERA = {"location": (0.0, -0.6, 0.045), "lens": 85.0, "sensor": 36.0}
BOTTLE = {"x": -3.3 * MM, "w": 75.4 * MM, "d": 50.0 * MM, "h": 68.8 * MM, "bevel": 11 * MM,
          "neck_r": 15.0 * MM, "neck_h": 6.6 * MM, "cap_r": 17.8 * MM, "cap_h": 21.4 * MM}
FRAMES = (1, 121)  # R05 v01: 121 frames at 24 fps
FALL_MM_PER_FRAME = 1.8  # R04 drops move about 13 px/frame at the bottle distance (slow motion)


# ---------------------------------------------------------------- helpers

def reset() -> bpy.types.Scene:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE_NEXT"
    scene.render.resolution_x, scene.render.resolution_y = 1920, 1080
    scene.render.fps = 24
    scene.frame_start, scene.frame_end = FRAMES
    scene.frame_set(60)
    world = bpy.data.worlds.new("World")
    scene.world = world
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.004, 0.008, 0.006, 1)
    return scene


def collection(name: str) -> bpy.types.Collection:
    col = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(col)
    return col


def link(obj: bpy.types.Object, col: bpy.types.Collection) -> bpy.types.Object:
    for old in list(obj.users_collection):
        old.objects.unlink(obj)
    col.objects.link(obj)
    return obj


def principled(name: str, **inputs) -> bpy.types.Material:
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    for key, value in inputs.items():
        bsdf.inputs[key].default_value = value
    return mat


def glass(name: str, roughness: float = 0.015) -> bpy.types.Material:
    """Thin-slab refraction: EEVEE cannot refract through glass *and* the liquid behind it, so the glass bends
    light like a thin wall and the liquid carries the colour."""
    mat = principled(name, **{"Base Color": (0.93, 0.97, 0.95, 1), "Roughness": roughness, "IOR": 1.45,
                              "Transmission Weight": 1.0, "Coat Weight": 0.4})
    if hasattr(mat, "use_raytrace_refraction"):
        mat.use_raytrace_refraction = True
    if hasattr(mat, "thickness_mode"):
        mat.thickness_mode = "SLAB"
    return mat


def amber_liquid() -> bpy.types.Material:
    """Glowing amber: brighter where the surface faces the camera, darker towards the edges (reads as depth)."""
    mat = principled("Amber_Liquid", **{"Base Color": (0.34, 0.09, 0.006, 1), "Roughness": 0.05,
                                        "Subsurface Weight": 0.25, "Subsurface Radius": (1.0, 0.3, 0.06)})
    tree = mat.node_tree
    bsdf = tree.nodes["Principled BSDF"]
    facing = tree.nodes.new("ShaderNodeLayerWeight")
    facing.inputs["Blend"].default_value = 0.5
    glow = tree.nodes.new("ShaderNodeMapRange")
    glow.inputs["To Min"].default_value, glow.inputs["To Max"].default_value = 1.15, 0.0
    tree.links.new(facing.outputs["Facing"], glow.inputs["Value"])
    tree.links.new(glow.outputs["Result"], bsdf.inputs["Emission Strength"])
    bsdf.inputs["Emission Color"].default_value = (1.0, 0.40, 0.06, 1)
    return mat


def holdout(name: str) -> bpy.types.Material:
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    nodes.remove(nodes["Principled BSDF"])
    out = nodes["Material Output"]
    mat.node_tree.links.new(nodes.new("ShaderNodeHoldout").outputs[0], out.inputs["Surface"])
    return mat


def assign(obj: bpy.types.Object, mat: bpy.types.Material) -> bpy.types.Object:
    obj.data.materials.clear()
    obj.data.materials.append(mat)
    return obj


def smooth(obj: bpy.types.Object) -> None:
    for poly in obj.data.polygons:
        poly.use_smooth = True


def rounded_box(name: str, size: tuple[float, float, float], center: tuple[float, float, float],
                bevel: float, segments: int = 10) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=1, location=center)
    obj = bpy.context.active_object
    obj.name = name
    obj.scale = size
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    mod = obj.modifiers.new("Bevel", "BEVEL")
    mod.width, mod.segments, mod.limit_method = bevel, segments, "NONE"
    mod.harden_normals = True
    smooth(obj)
    return obj


def cylinder(name: str, radius: float, depth: float, z0: float, bevel: float = 0.0,
             vertices: int = 96) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cylinder_add(vertices=vertices, radius=radius, depth=depth,
                                        location=(BOTTLE["x"], 0, z0 + depth / 2))
    obj = bpy.context.active_object
    obj.name = name
    if bevel:
        mod = obj.modifiers.new("Bevel", "BEVEL")
        mod.width, mod.segments, mod.limit_method = bevel, 4, "ANGLE"
        mod.harden_normals = True
    smooth(obj)
    return obj


def camera(scene: bpy.types.Scene, fstop: float | None) -> bpy.types.Object:
    data = bpy.data.cameras.new("SF_Camera")
    data.lens, data.sensor_width, data.sensor_fit = CAMERA["lens"], CAMERA["sensor"], "HORIZONTAL"
    data.clip_start, data.clip_end = 0.01, 20
    data.dof.use_dof = fstop is not None  # EEVEE turns defocused emissive drops on a transparent film into
    data.dof.focus_distance = 0.6         # solid discs, so the rain layer fakes depth with brightness instead
    data.dof.aperture_fstop = fstop or 2.8
    cam = bpy.data.objects.new("SF_Camera", data)
    cam.location = CAMERA["location"]
    cam.rotation_euler = (math.radians(90), 0, 0)
    scene.collection.objects.link(cam)
    scene.camera = cam
    return cam


# ---------------------------------------------------------------- the bottle

def bottle_parts(col: bpy.types.Collection, mats: dict[str, bpy.types.Material]) -> list[bpy.types.Object]:
    b = BOTTLE
    body = rounded_box("Bottle_Glass", (b["w"], b["d"], b["h"]), (b["x"], 0, b["h"] / 2), b["bevel"])
    wall, base = 6.5 * MM, 15 * MM
    liquid = rounded_box("Bottle_Liquid", (b["w"] - 2 * wall, b["d"] - 2 * wall, b["h"] - base - 9 * MM),
                         (b["x"], 0, base + (b["h"] - base - 9 * MM) / 2), b["bevel"] - wall)
    neck = cylinder("Bottle_Neck", b["neck_r"], b["neck_h"] + 2 * MM, b["h"] - 2 * MM)
    collar = cylinder("Bottle_Collar", b["neck_r"] + 0.8 * MM, 2.6 * MM, b["h"] + b["neck_h"] - 2.6 * MM, 0.4 * MM)
    cap = cylinder("Bottle_Cap", b["cap_r"], b["cap_h"], b["h"] + b["neck_h"], 1.4 * MM)
    parts = [assign(body, mats["glass"]), assign(liquid, mats["liquid"]), assign(neck, mats["glass"]),
             assign(collar, mats["silver"]), assign(cap, mats["cap"])]
    for part in parts:
        link(part, col)
    return parts


def surface_droplets(name: str, targets: list[bpy.types.Object], count: int, mat: bpy.types.Material,
                     seed: int) -> bpy.types.Object:
    """Small flattened drops scattered on the camera-facing surfaces (area weighted)."""
    rng = random.Random(seed)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    tris = []
    for obj in targets:
        mesh = obj.evaluated_get(depsgraph).to_mesh()
        mesh.calc_loop_triangles()
        mw = obj.matrix_world
        for tri in mesh.loop_triangles:
            normal = (mw.to_3x3() @ tri.normal).normalized()
            if normal.y > 0.35 or normal.z < -0.2:  # skip the back and the underside
                continue
            verts = [mw @ mesh.vertices[i].co for i in tri.vertices]
            area = ((verts[1] - verts[0]).cross(verts[2] - verts[0])).length / 2
            tris.append((area, verts, normal))
        obj.evaluated_get(depsgraph).to_mesh_clear()
    total = sum(t[0] for t in tris)
    bm = bmesh.new()
    for _ in range(count):
        pick, acc = rng.uniform(0, total), 0.0
        for area, verts, normal in tris:
            acc += area
            if acc >= pick:
                break
        u, v = rng.random(), rng.random()
        if u + v > 1:
            u, v = 1 - u, 1 - v
        point = verts[0] + (verts[1] - verts[0]) * u + (verts[2] - verts[0]) * v
        radius = rng.choice([0.35, 0.45, 0.55, 0.7, 0.9, 1.2, 1.6]) * MM
        geom = bmesh.ops.create_uvsphere(bm, u_segments=12, v_segments=8, radius=radius)
        drop = [g for g in geom["verts"]]
        rot = normal.to_track_quat("Z", "Y").to_matrix().to_4x4()
        stretch = 1.0 + (0.6 if abs(normal.z) < 0.5 and rng.random() < 0.4 else 0.0)  # some run down the sides
        for vert in drop:
            local = Vector((vert.co.x, vert.co.y * stretch, vert.co.z * 0.45))
            vert.co = point + rot @ local + normal * radius * 0.15
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    smooth(obj)
    return assign(obj, mat)


# ---------------------------------------------------------------- rain

def drop_shape(mat: bpy.types.Material) -> bpy.types.Mesh:
    """Bright head with a thin trailing tail, like R04's slow-motion drops (head at the origin, tail up +Z)."""
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=12, v_segments=8, radius=0.55 * MM)
    tail = bmesh.ops.create_cone(bm, cap_ends=True, segments=8, radius1=0.2 * MM, radius2=0.03 * MM, depth=7 * MM)
    bmesh.ops.translate(bm, verts=tail["verts"], vec=(0, 0, 3.6 * MM))
    mesh = bpy.data.meshes.new("SF_DropShape")
    bm.to_mesh(mesh)
    bm.free()
    mesh.materials.append(mat)
    return mesh


def drop_material(strength: float) -> bpy.types.Material:
    mat = bpy.data.materials.new("Rain_Drop")
    mat.use_nodes = True
    tree = mat.node_tree
    nodes, links = tree.nodes, tree.links
    nodes.remove(nodes["Principled BSDF"])
    coords = nodes.new("ShaderNodeTexCoord")
    sep = nodes.new("ShaderNodeSeparateXYZ")
    fade = nodes.new("ShaderNodeMapRange")
    fade.inputs["From Min"].default_value, fade.inputs["From Max"].default_value = 0.4 * MM, 7.2 * MM
    fade.inputs["To Min"].default_value, fade.inputs["To Max"].default_value = 1.0, 0.0
    emit = nodes.new("ShaderNodeEmission")
    emit.inputs["Color"].default_value = (0.86, 0.95, 0.92, 1)
    info = nodes.new("ShaderNodeObjectInfo")  # per-drop brightness (set from distance in rain())
    level = nodes.new("ShaderNodeMath")
    level.operation, level.inputs[1].default_value = "MULTIPLY", strength
    links.new(info.outputs["Color"], level.inputs[0])
    links.new(level.outputs[0], emit.inputs["Strength"])
    clear = nodes.new("ShaderNodeBsdfTransparent")
    mix = nodes.new("ShaderNodeMixShader")
    links.new(coords.outputs["Object"], sep.inputs[0])
    links.new(sep.outputs["Z"], fade.inputs["Value"])
    links.new(fade.outputs["Result"], mix.inputs["Fac"])
    links.new(clear.outputs[0], mix.inputs[1])
    links.new(emit.outputs[0], mix.inputs[2])
    links.new(mix.outputs[0], nodes["Material Output"].inputs["Surface"])
    return mat


def rain(col: bpy.types.Collection, mesh: bpy.types.Mesh, count: int, seed: int, animated: bool,
         nearest: float = 0.35) -> None:
    """Drops spread through the camera frustum, `nearest`-2.4 m away. Animated drops fall at a constant speed and
    wrap around with a Cycles modifier; the wrap happens below the water holdout so it is never seen."""
    rng = random.Random(seed)
    half_tan = CAMERA["sensor"] / 2 / CAMERA["lens"]
    cam_y, cam_z = CAMERA["location"][1], CAMERA["location"][2]
    for i in range(count):
        dist = nearest + (2.4 - nearest) * rng.random() ** 0.75  # more drops far away: the frustum is wider there
        half_w = half_tan * dist * 1.12
        half_h = half_w * 9 / 16
        top, bottom = cam_z + half_h * 1.15, -0.012
        obj = bpy.data.objects.new(f"Drop_{i:03d}", mesh)
        level = min(1.0, max(0.22, 0.5 / dist)) * rng.uniform(0.7, 1.0)  # far drops read fainter
        obj.color = (level, level, level, 1)
        scale = rng.uniform(0.6, 1.35)
        obj.scale = (scale, scale, scale * rng.uniform(0.8, 1.5))
        obj.rotation_euler = (0, rng.uniform(-0.04, 0.04), 0)
        x, y = rng.uniform(-half_w, half_w), cam_y + dist
        col.objects.link(obj)
        if not animated:
            obj.location = (x, y, rng.uniform(bottom + 0.01, top))
            continue
        speed = FALL_MM_PER_FRAME * MM * rng.uniform(0.85, 1.2)
        period = (top - bottom) / speed
        start = FRAMES[0] - rng.uniform(0, period)
        obj.location = (x, y, top)
        obj.keyframe_insert("location", index=2, frame=start)
        obj.location = (x, y, bottom)
        obj.keyframe_insert("location", index=2, frame=start + period)
        curve = obj.animation_data.action.fcurves.find("location", index=2)
        for key in curve.keyframe_points:
            key.interpolation = "LINEAR"
        curve.modifiers.new("CYCLES")


# ---------------------------------------------------------------- scenes

def build_keyframe(path: Path) -> None:
    scene = reset()
    scene.eevee.taa_render_samples = 64
    scene.eevee.use_raytracing = True
    scene.eevee.ray_tracing_options.resolution_scale = "1"
    scene.eevee.use_shadows = True
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.look = "AgX - Medium High Contrast"
    scene.view_settings.exposure = -0.6
    camera(scene, fstop=5.6)

    mats = {
        "glass": glass("Glass"),
        "liquid": amber_liquid(),
        "silver": principled("Collar_Silver", **{"Base Color": (0.85, 0.85, 0.83, 1), "Metallic": 1.0, "Roughness": 0.18}),
        "cap": principled("Cap_Bronze", **{"Base Color": (0.13, 0.105, 0.09, 1), "Metallic": 1.0, "Roughness": 0.26}),
        "water_drop": glass("Water_Drop", 0.0),
    }
    product = collection("SF_MASK")
    parts = bottle_parts(product, mats)
    drops = surface_droplets("Bottle_Droplets", [parts[0], parts[4]], 190, mats["water_drop"], 7)
    link(drops, product)

    set_col = collection("Set")
    water = principled("Wet_Floor", **{"Base Color": (0.006, 0.014, 0.010, 1), "Roughness": 0.035,
                                       "Specular IOR Level": 0.7})
    tree = water.node_tree
    noise = tree.nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value, noise.inputs["Detail"].default_value = 55.0, 4.0
    bump = tree.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value, bump.inputs["Distance"].default_value = 0.06, 0.0005
    tree.links.new(noise.outputs["Fac"], bump.inputs["Height"])
    tree.links.new(bump.outputs["Normal"], tree.nodes["Principled BSDF"].inputs["Normal"])
    bpy.ops.mesh.primitive_plane_add(size=6, location=(0, 1.2, 0.0005))
    link(assign(bpy.context.active_object, water), set_col).name = "Water_Floor"

    backdrop = bpy.data.materials.new("Green_Studio")
    backdrop.use_nodes = True
    t = backdrop.node_tree
    t.nodes.remove(t.nodes["Principled BSDF"])
    coords, mapping = t.nodes.new("ShaderNodeTexCoord"), t.nodes.new("ShaderNodeMapping")
    mapping.inputs["Location"].default_value = (-0.5, -0.72, 0)
    mapping.inputs["Scale"].default_value = (1.3, 2.2, 1)
    grad = t.nodes.new("ShaderNodeTexGradient")
    grad.gradient_type = "SPHERICAL"
    ramp = t.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = (0.002, 0.006, 0.004, 1)
    ramp.color_ramp.elements[1].position = 0.85
    ramp.color_ramp.elements[1].color = (0.035, 0.11, 0.055, 1)
    emit = t.nodes.new("ShaderNodeEmission")
    emit.inputs["Strength"].default_value = 0.6
    t.links.new(coords.outputs["UV"], mapping.inputs["Vector"])
    t.links.new(mapping.outputs["Vector"], grad.inputs["Vector"])
    t.links.new(grad.outputs["Fac"], ramp.inputs["Fac"])
    t.links.new(ramp.outputs["Color"], emit.inputs["Color"])
    t.links.new(emit.outputs[0], t.nodes["Material Output"].inputs["Surface"])
    bpy.ops.mesh.primitive_plane_add(size=1, location=(0, 1.4, 0.45), rotation=(math.radians(90), 0, 0))
    wall = bpy.context.active_object
    wall.scale = (3.2, 1.8, 1)
    link(assign(wall, backdrop), set_col).name = "Backdrop"

    lights = collection("Lights")

    def area(name, location, rotation, size, power, color=(1, 0.96, 0.9), shape="SQUARE", size_y=None, specular=1.0):
        data = bpy.data.lights.new(name, "AREA")
        data.shape, data.size, data.energy, data.color = shape, size, power, color
        data.specular_factor = specular  # 0 keeps a light out of the water reflection
        if size_y:
            data.size_y = size_y
        obj = bpy.data.objects.new(name, data)
        obj.location, obj.rotation_euler = location, [math.radians(a) for a in rotation]
        lights.objects.link(obj)

    area("Top_Soft", (0, 0.10, 0.36), (14, 0, 0), 0.22, 9)
    area("Strip_Left", (-0.17, 0.03, 0.06), (90, 0, -80), 0.02, 4, shape="RECTANGLE", size_y=0.3)
    area("Strip_Right", (0.17, 0.03, 0.06), (90, 0, 80), 0.02, 4, shape="RECTANGLE", size_y=0.3)
    area("Key_Front", (-0.32, -0.38, 0.22), (65, 0, -40), 0.32, 2.5, specular=0.3)

    rain_col = collection("Rain")
    rain(rain_col, drop_shape(drop_material(0.9)), 260, seed=11, animated=False, nearest=0.5)
    scene.render.film_transparent = False
    bpy.ops.wm.save_as_mainfile(filepath=str(path))


def build_rain_layer(path: Path) -> None:
    scene = reset()
    scene.eevee.taa_render_samples = 24
    scene.view_settings.view_transform = "Standard"
    scene.render.film_transparent = True
    camera(scene, fstop=None)
    hold = holdout("Holdout")
    proxies = collection("Holdout_Proxies")
    b = BOTTLE
    for obj in (rounded_box("Proxy_Body", (b["w"], b["d"], b["h"]), (b["x"], 0, b["h"] / 2), b["bevel"]),
                cylinder("Proxy_Neck", b["neck_r"], b["neck_h"] + 2 * MM, b["h"] - 2 * MM),
                cylinder("Proxy_Cap", b["cap_r"], b["cap_h"], b["h"] + b["neck_h"], 1.4 * MM)):
        link(assign(obj, hold), proxies)
    bpy.ops.mesh.primitive_plane_add(size=8, location=(0, 2.0, 0.0))
    link(assign(bpy.context.active_object, hold), proxies).name = "Proxy_Water"
    rain(collection("Rain"), drop_shape(drop_material(1.2)), 320, seed=23, animated=True, nearest=0.45)
    bpy.ops.wm.save_as_mainfile(filepath=str(path))


if __name__ == "__main__":
    out = Path(sys.argv[sys.argv.index("--") + 1]) if "--" in sys.argv else Path(__file__).parent
    out.mkdir(parents=True, exist_ok=True)
    build_keyframe(out / "perfume_keyframe.blend")
    build_rain_layer(out / "rain_layer_r05.blend")
    print("SF_BUILD_OK", out)
