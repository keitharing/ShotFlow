"""C1 결과 화면을 C1 카메라에서 장면 형태에 투영해, C2 카메라로 보았을 때도 같은 건물로 보이게 한다 (카메라 투영).

실행: blender -b collapse_C2.blend --python project_c1_to_c2.py -- <C1 화면 PNG (C1 카메라 구도에 맞춘 것)> [미리보기 PNG]
  C1 카메라에서 보이는 물체(주인공 건물, 앞쪽 잔해, 양옆 폐허, 바닥)의 재질을 투영 재질로 바꾸고 파일을 저장한다.
  투영이 닿지 않는 면(카메라를 등진 면, 화면 밖)은 원래 색으로 남는다. 남은 부분은 ShotFlow 3D 가이드가 새로 그린다.
"""
import sys

import bpy
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
IMAGE = argv[0]
PREVIEW = argv[1] if len(argv) > 1 else None

scene = bpy.context.scene
cam = bpy.data.objects["SF_Camera_C1"]
cam_pos = cam.matrix_world.translation.copy()
K = cam.data.lens / cam.data.sensor_width
ASPECT = scene.render.resolution_x / scene.render.resolution_y
img = bpy.data.images.load(IMAGE)
img.pack()

# ---------- C1 카메라에서 보이는 물체 고르기 ----------
for ob in scene.objects:
    ob.hide_viewport = ob.hide_render
bpy.context.view_layer.update()
dg = bpy.context.evaluated_depsgraph_get()
ALWAYS = ("main_lower", "main_upper_cracked", "ground", "sidewalk")


def in_frame(p):
    q = cam.matrix_world.inverted() @ p
    if q.z > -0.1:
        return False
    u, v = q.x / -q.z * K, q.y / -q.z * K * ASPECT
    return abs(u) < 0.5 and abs(v) < 0.5


def visible(ob):
    corners = [ob.matrix_world @ Vector(c) for c in ob.bound_box]
    center = sum(corners, Vector()) / 8
    for p in [center] + [center + (c - center) * 0.8 for c in corners]:
        if not in_frame(p):
            continue
        d = p - cam_pos
        hit, loc, _n, _i, hit_ob, _m = scene.ray_cast(dg, cam_pos, d.normalized(), distance=d.length + 0.5)
        if hit and (hit_ob.name == ob.name or (loc - p).length < 0.6):
            return True
    return False


targets = [ob for ob in scene.objects if ob.type == "MESH" and not ob.hide_render
           and (ob.name.startswith(ALWAYS) or visible(ob))]
print("PROJECT_TARGETS", len(targets), sorted({ob.name.split(".")[0] for ob in targets}))

# ---------- 투영 재질 ----------
cache = {}


def projected(mat):
    if mat.name in cache:
        return cache[mat.name]
    base = tuple(mat.diffuse_color)
    m = bpy.data.materials.new("proj_" + mat.name)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    N, L = nt.nodes.new, nt.links.new

    def math(op, a, b=None, clamp=False):
        n = N("ShaderNodeMath")
        n.operation, n.use_clamp = op, clamp
        for i, x in enumerate((a, b)):
            if x is None:
                continue
            if isinstance(x, (int, float)):
                n.inputs[i].default_value = x
            else:
                L(x, n.inputs[i])
        return n.outputs[0]

    tc = N("ShaderNodeTexCoord")
    tc.object = cam
    sep = N("ShaderNodeSeparateXYZ")
    L(tc.outputs["Object"], sep.inputs[0])
    negz = math("MULTIPLY", sep.outputs["Z"], -1.0)
    u = math("ADD", math("MULTIPLY", math("DIVIDE", sep.outputs["X"], negz), K), 0.5)
    v = math("ADD", math("MULTIPLY", math("DIVIDE", sep.outputs["Y"], negz), K * ASPECT), 0.5)
    uv = N("ShaderNodeCombineXYZ")
    L(u, uv.inputs[0]); L(v, uv.inputs[1])
    tex = N("ShaderNodeTexImage")
    tex.image, tex.extension, tex.interpolation = img, "CLIP", "Linear"
    L(uv.outputs[0], tex.inputs["Vector"])
    # 쓸 수 있는 곳: 화면 안, 카메라 앞, 카메라를 향한 면
    geo = N("ShaderNodeNewGeometry")
    to_cam = N("ShaderNodeVectorMath"); to_cam.operation = "SUBTRACT"
    to_cam.inputs[0].default_value = cam_pos
    L(geo.outputs["Position"], to_cam.inputs[1])
    norm = N("ShaderNodeVectorMath"); norm.operation = "NORMALIZE"
    L(to_cam.outputs[0], norm.inputs[0])
    dot = N("ShaderNodeVectorMath"); dot.operation = "DOT_PRODUCT"
    L(norm.outputs[0], dot.inputs[0]); L(geo.outputs["True Normal"], dot.inputs[1])
    mask = math("GREATER_THAN", dot.outputs["Value"], 0.06)
    for term in (math("GREATER_THAN", u, 0.004), math("LESS_THAN", u, 0.996), math("GREATER_THAN", v, 0.004), math("LESS_THAN", v, 0.996),
                 math("GREATER_THAN", negz, 0.2)):
        mask = math("MULTIPLY", mask, term)
    emit = N("ShaderNodeEmission")
    L(tex.outputs["Color"], emit.inputs["Color"])
    bsdf = N("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Base Color"].default_value = base
    bsdf.inputs["Roughness"].default_value = 0.9
    mix = N("ShaderNodeMixShader")
    L(mask, mix.inputs[0]); L(bsdf.outputs[0], mix.inputs[1]); L(emit.outputs[0], mix.inputs[2])
    out = N("ShaderNodeOutputMaterial")
    L(mix.outputs[0], out.inputs["Surface"])
    m.diffuse_color = base
    cache[mat.name] = m
    return m


for ob in targets:
    if ob.data.users > 1:
        ob.data = ob.data.copy()
    for slot in ob.material_slots:
        if slot.material and not slot.material.name.startswith("proj_"):
            slot.material = projected(slot.material)

# 하늘: C1 화면 윗부분의 색 (sRGB → 선형)
px = img.pixels[:]
w, h = img.size
rows = range(int(h * 0.92), int(h * 0.98), 4)
acc = [0.0, 0.0, 0.0]
cnt = 0
for y in rows:
    for x in range(int(w * 0.15), int(w * 0.75), 8):
        i = (y * w + x) * 4
        for c in range(3):
            acc[c] += px[i + c]
        cnt += 1
sky = [a / cnt for a in acc]      # Blender가 읽은 값은 이미 선형
bg = scene.world.node_tree.nodes["Background"]
bg.inputs["Color"].default_value = (*sky, 1)
bg.inputs["Strength"].default_value = 1.0
print("SKY_LINEAR", [round(s, 3) for s in sky])

scene.camera = bpy.data.objects["SF_Camera_C2"]
bpy.context.preferences.filepaths.save_version = 0
bpy.ops.wm.save_mainfile()
if PREVIEW:
    scene.render.resolution_percentage = 50
    scene.render.filepath = PREVIEW
    bpy.ops.render.render(write_still=True)
    scene.render.resolution_percentage = 100
print("SF_PROJECT_OK")
