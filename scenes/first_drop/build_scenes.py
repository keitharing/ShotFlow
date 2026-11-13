"""영상 2 「FIRST DROP」(FIRST_DROP) Blender 장면을 만든다: 흰 스튜디오, 병 하나, 고인 음료, 카메라 세 대.

실행: blender -b --factory-startup --python build_scenes.py -- [저장할 폴더] [미리보기 폴더]
  저장할 폴더를 생략하면 이 스크립트 옆에 저장한다. 미리보기 폴더를 주면 파일마다 PNG 한 장(960x540)을 렌더한다.

컷을 잇는 방법 (2026-11-12: 컷이 잘 이어지고 하나의 이야기가 되게)
  세트 · 병 · 조명은 하나이고, 음료가 떨어지는 자리 P도 하나다. 카메라만 세 대(A1 가까이, A2 표면 높이, A3 정면)이고 모두 앞쪽에 있어서
  병은 어느 컷에서나 P의 오른쪽에 있다.
  장면의 상태는 네 순간뿐이다: T0 줄기가 쏟아짐, T1 줄기가 끝나며 물결이 남음, T2 과일이 떨어져 왕관이 가장 높이 솟음, T3 음료가 가라앉고 병에 맺힌 한 방울이 흘러내림.
  (v4, 2026-11-12: 병 뒤에서 V 모양으로 솟는 날개가 뜬금없음 → 날개를 없앰. 끝은 튄 음료가 병에 방울로 맺히고 그중 한 방울이 흘러내리는 것으로 바꿈.
   A3 카메라를 더 가까이 잡음: 병이 화면 높이의 4분의 3)
  컷의 시작과 끝은 이 순간들을 카메라로 본 것이다:
    A1 시작 = T0, A1 끝 = T1 | A2 시작 = T1, A2 끝 = T2 | A3 시작 = T2, A3 끝 = T3
  그래서 A1의 끝 그림과 A2의 시작 그림, A2의 끝 그림과 A3의 시작 그림은 같은 순간을 다른 카메라로 본 것이 된다.
  파일은 컷의 시작 · 끝마다 하나: first_drop_A1_start.blend ~ first_drop_A3_end.blend

ShotFlow 3D 연동 규칙: 장면의 활성 카메라로 렌더하고, SF_MASK 컬렉션의 물체(병)가 마스크가 된다.
v2 (2026-11-12): 병에 라벨 그림(가상의 상표 DROP, tex/label_drop.png)을 입히고, 유리와 음료가 제대로 보이도록 Cycles로 렌더한다.
  환경 변수 SF_ENGINE=EEVEE 를 주면 전처럼 EEVEE로 저장한다.
단위는 미터. 병은 원점에 서 있고 높이 0.245. 카메라는 -Y 쪽(앞)에 있다. 음료의 모양(줄기 · 왕관 · 날개)은 구도를 잡기 위한 형태이고 시뮬레이션이 아니다.
"""
import math
import os
import random
import sys
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Vector

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT_DIR = Path(argv[0]) if argv else Path(__file__).resolve().parent
PREVIEW = Path(argv[1]) if len(argv) > 1 else None
PREVIEW_PCT = int(argv[2]) if len(argv) > 2 else 50      # 미리보기 크기 (1920x1080의 %)
PREVIEW_ONLY = argv[3].split(",") if len(argv) > 3 else None      # 미리보기를 렌더할 것만 (예: A1_start,A3_end)
rnd = random.Random(20261112)
ENGINE = os.environ.get("SF_ENGINE", "CYCLES").upper()
HERE = Path(__file__).resolve().parent
P = Vector((-0.11, -0.02, 0.0))          # 음료가 떨어지는 자리

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.context.preferences.filepaths.save_version = 0      # .blend1 백업 파일을 만들지 않음
scene = bpy.context.scene


def material(name, color, rough=0.5, metallic=0.0, transmission=0.0, ior=1.45, emission=0.0):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*color, 1)
    b.inputs["Roughness"].default_value = rough
    b.inputs["Metallic"].default_value = metallic
    b.inputs["IOR"].default_value = ior
    if transmission:
        b.inputs["Transmission Weight"].default_value = transmission
        for attr in ("use_raytrace_refraction", "use_screen_refraction"):
            if hasattr(m, attr):
                setattr(m, attr, True)
    if emission:
        b.inputs["Emission Color"].default_value = (*color, 1)
        b.inputs["Emission Strength"].default_value = emission
    return m


M_FLOOR = material("floor_white", (0.92, 0.92, 0.91), 0.45)
M_GLASS = material("bottle_glass", (1, 1, 1), 0.03, transmission=1.0, ior=1.5)
M_CAP = material("cap", (0.10, 0.10, 0.11), 0.35, metallic=0.9)
M_LABEL = material("label", (0.96, 0.95, 0.92), 0.6)
label_img = HERE / "tex" / "label_drop.png"
if label_img.exists():                                   # 라벨 그림 (make_label.py로 만듦)
    tex = M_LABEL.node_tree.nodes.new("ShaderNodeTexImage")
    tex.image = bpy.data.images.load(str(label_img))
    M_LABEL.node_tree.links.new(tex.outputs["Color"], M_LABEL.node_tree.nodes["Principled BSDF"].inputs["Base Color"])
M_STRIPE = material("label_stripe", (0.95, 0.42, 0.06), 0.5)
M_DRINK = material("drink", (0.95, 0.30, 0.02), 0.12)       # 음료: 주황. 유리 너머로도 보이게 불투명하게 둠
M_FLESH = material("fruit_flesh", (0.98, 0.62, 0.16), 0.35)
M_RIND = material("fruit_rind", (0.93, 0.47, 0.05), 0.55)
M_BEAD = material("drink_bead", (1.0, 0.72, 0.42), 0.0, transmission=1.0, ior=1.33)      # 병에 맺힌 음료 방울


def collection(name):
    c = bpy.data.collections.new(name)
    scene.collection.children.link(c)
    return c


C_SET, C_BOTTLE, C_CAM, C_MASK = collection("Set"), collection("Bottle"), collection("Cameras"), collection("SF_MASK")
C_T = {t: collection(f"State_{t}") for t in ("T0", "T1", "T2", "T3")}


def to_object(name, bm, mat, coll, smooth=True):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    for poly in me.polygons:
        poly.use_smooth = smooth
    ob = bpy.data.objects.new(name, me)
    ob.data.materials.append(mat)
    coll.objects.link(ob)
    return ob


def lathe(name, profile, mat, coll, steps=72, wrap_uv=False):
    """(반지름, 높이) 점들을 Z축으로 돌려 만든 모양. wrap_uv: 그림을 한 바퀴 감는 UV (가로 50%가 앞쪽 -Y)."""
    bm = bmesh.new()
    verts = [bm.verts.new((r, 0, z)) for r, z in profile]
    edges = [bm.edges.new((a, b)) for a, b in zip(verts, verts[1:])]
    bmesh.ops.spin(bm, geom=verts + edges, cent=(0, 0, 0), axis=(0, 0, 1), angle=2 * math.pi, steps=steps, use_duplicate=False)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-6)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    if wrap_uv:
        z0, z1 = min(z for _, z in profile), max(z for _, z in profile)
        layer = bm.loops.layers.uv.new("label")
        for face in bm.faces:
            us = [math.atan2(loop.vert.co.x, -loop.vert.co.y) / (2 * math.pi) + 0.5 for loop in face.loops]
            if max(us) - min(us) > 0.5:                  # 뒤쪽 이음매에 걸친 면
                us = [u + 1 if u < 0.5 else u for u in us]
            for loop, u in zip(face.loops, us):
                loop[layer].uv = (u, (loop.vert.co.z - z0) / (z1 - z0))
    return to_object(name, bm, mat, coll)


# ---------- 세트 ----------
# 바닥과 뒷벽이 둥글게 이어진 흰 배경 (바닥과 벽 사이에 선이 보이지 않게)
prof = [(-6.0, 0.0), (1.6, 0.0)] + [(1.6 + 0.9 * math.sin(math.radians(a)), 0.9 - 0.9 * math.cos(math.radians(a))) for a in range(6, 91, 6)] + [(2.5, 6.0)]
bm = bmesh.new()
rows = [(bm.verts.new((-7.0, y, z)), bm.verts.new((7.0, y, z))) for y, z in prof]
for a, b in zip(rows, rows[1:]):
    bm.faces.new((a[0], a[1], b[1], b[0]))
bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
floor = to_object("floor", bm, M_FLOOR, C_SET)
if floor.data.polygons[0].normal.z < 0:
    floor.data.flip_normals()

# 고인 음료: P를 가운데로 한 얕은 웅덩이 (모든 순간에 있음)
bm = bmesh.new()
bmesh.ops.create_cone(bm, cap_ends=True, segments=64, radius1=1.0, radius2=1.0, depth=1.0)
pool = to_object("pool", bm, M_DRINK, C_SET)
pool.location, pool.scale = (P.x, P.y, 0.0015), (0.150, 0.105, 0.003)

# ---------- 병 (모든 컷에서 같은 물체) ----------
glass = lathe("bottle_glass", [(0, 0), (0.034, 0), (0.036, 0.004), (0.036, 0.135), (0.034, 0.156), (0.022, 0.180), (0.0135, 0.192),
                               (0.0135, 0.224), (0.0145, 0.226), (0.0145, 0.234), (0, 0.234)], M_GLASS, C_BOTTLE)
lathe("bottle_drink", [(0, 0.005), (0.0328, 0.005), (0.0328, 0.135), (0.0308, 0.154), (0.0205, 0.174), (0, 0.174)], M_DRINK, C_BOTTLE)
lathe("bottle_cap", [(0, 0.220), (0.0168, 0.220), (0.0168, 0.243), (0.0152, 0.245), (0, 0.245)], M_CAP, C_BOTTLE)
lathe("bottle_label", [(0.0364, 0.052), (0.0364, 0.118)], M_LABEL, C_BOTTLE, wrap_uv=True)
for ob in C_BOTTLE.objects:
    C_MASK.objects.link(ob)


# ---------- 음료의 모양 ----------
def stream(name, coll, radius_top, radius_end):
    """위 왼쪽에서 내려와 P에 닿는 줄기."""
    cu = bpy.data.curves.new(name, "CURVE")
    cu.dimensions = "3D"
    cu.bevel_depth, cu.bevel_resolution, cu.use_fill_caps = 1.0, 8, True
    sp = cu.splines.new("BEZIER")
    pts = [(-0.30, 0.0, 0.66), (-0.215, -0.008, 0.40), (-0.135, -0.018, 0.14), (P.x, P.y, 0.004)]
    sp.bezier_points.add(len(pts) - 1)
    for k, (bp, co) in enumerate(zip(sp.bezier_points, pts)):
        bp.co = co
        bp.handle_left_type = bp.handle_right_type = "AUTO"
        bp.radius = radius_top + (radius_end - radius_top) * k / (len(pts) - 1)
    ob = bpy.data.objects.new(name, cu)
    ob.data.materials.append(M_DRINK)
    coll.objects.link(ob)
    return ob


def crown(name, coll, radius, height, spikes, seed):
    """P에서 솟은 왕관 모양: 둥글게 선 뾰족한 기둥들과 그 끝의 방울."""
    r = random.Random(seed)
    bm = bmesh.new()
    for k in range(spikes):
        a = 2 * math.pi * k / spikes + r.uniform(-0.08, 0.08)
        h = height * r.uniform(0.7, 1.0)
        base = Vector((P.x + radius * math.cos(a), P.y + radius * math.sin(a), 0.0))
        lean = Matrix.Rotation(math.radians(r.uniform(14, 24)), 4, Vector((-math.sin(a), math.cos(a), 0)))      # 바깥쪽으로 기울임
        m = Matrix.Translation(base) @ lean @ Matrix.Translation((0, 0, h / 2))
        bmesh.ops.create_cone(bm, cap_ends=True, segments=12, radius1=radius * 0.36, radius2=radius * 0.03, depth=h, matrix=m)
        tip = base + lean.to_3x3() @ Vector((0, 0, h * 1.12))
        bmesh.ops.create_icosphere(bm, subdivisions=2, radius=radius * r.uniform(0.09, 0.15), matrix=Matrix.Translation(tip))
    ob = to_object(name, bm, M_DRINK, coll)
    ring = lathe(name + "_base", [(radius * 0.72, 0.003), (radius * 0.80, radius * 0.34), (radius * 1.22, radius * 0.40), (radius * 1.36, 0.003)], M_DRINK, coll, 48)
    ring.location = (P.x, P.y, 0)
    return ob


def ripples(name, coll, radii):
    obs = []
    for k, rr in enumerate(radii):
        ob = lathe(f"{name}_{k}", [(rr - 0.004, 0.0032), (rr, 0.0062), (rr + 0.004, 0.0032)], M_DRINK, coll, 64)
        ob.location, ob.scale = (P.x, P.y, 0), (1.0, 0.72, 1.0)
        obs.append(ob)
    return obs


def drops(name, coll, center, spread, count, seed, rmin=0.003, rmax=0.008):
    r = random.Random(seed)
    bm = bmesh.new()
    for _ in range(count):
        p = Vector(center) + Vector((r.uniform(-spread[0], spread[0]), r.uniform(-spread[1], spread[1]), r.uniform(-spread[2], spread[2])))
        p.z = max(p.z, 0.012)
        bmesh.ops.create_icosphere(bm, subdivisions=2, radius=r.uniform(rmin, rmax), matrix=Matrix.Translation(p))
    return to_object(name, bm, M_DRINK, coll)


def fruit_slice(name, coll, location, tilt):
    """둥글게 자른 과일 조각."""
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=48, radius1=0.031, radius2=0.031, depth=0.008)
    flesh = to_object(name, bm, M_FLESH, coll, smooth=False)
    rind = lathe(name + "_rind", [(0.031, -0.0042), (0.035, -0.0042), (0.035, 0.0042), (0.031, 0.0042), (0.031, -0.0042)], M_RIND, coll, 48)
    for ob in (flesh, rind):
        ob.location = location
        ob.rotation_euler = tilt
    return flesh


def wing(name, coll, side, grow):
    """병 뒤에서 솟아 옆으로 펼쳐지는 음료의 날개. side: -1 왼쪽, 1 오른쪽. grow: 0~1 (얼마나 펼쳐졌나)."""
    root = Vector((side * 0.012, 0.055, 0.004))
    ctrl = Vector((side * (0.05 + 0.10 * grow), 0.075, 0.10 + 0.16 * grow))
    tip = Vector((side * (0.07 + 0.27 * grow), 0.085, 0.10 + 0.28 * grow))
    bm = bmesh.new()
    rows, n = [], 22
    for k in range(n + 1):
        u = k / n
        c = (1 - u) ** 2 * root + 2 * u * (1 - u) * ctrl + u ** 2 * tip
        tan = (2 * (1 - u) * (ctrl - root) + 2 * u * (tip - ctrl)).normalized()
        across = tan.cross(Vector((0, 1, 0))).normalized()
        w = (0.060 * (1 - u) ** 0.55 + 0.004) * (0.45 + 0.55 * grow) * (1 + 0.12 * math.sin(u * 9 + side))
        rows.append((bm.verts.new(c - across * w), bm.verts.new(c + Vector((0, 0.012 * math.sin(u * 3.14), 0))), bm.verts.new(c + across * w)))
    for a, b in zip(rows, rows[1:]):
        bm.faces.new((a[0], a[1], b[1], b[0]))
        bm.faces.new((a[1], a[2], b[2], b[1]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    ob = to_object(name, bm, M_DRINK, coll)
    mod = ob.modifiers.new("thick", "SOLIDIFY")
    mod.thickness, mod.offset = 0.007, 0
    ob.modifiers.new("smooth", "SUBSURF").levels = 2
    ob.modifiers["smooth"].render_levels = 2
    return ob


# T0: 줄기가 쏟아진다
stream("stream_full", C_T["T0"], 0.016, 0.011)
# T1: 줄기가 가늘어지며 끝난다. P에 작은 왕관과 물결이 남는다. 과일은 아직 두 카메라의 화면 위쪽 밖에 있다
stream("stream_thin", C_T["T1"], 0.007, 0.003)
crown("splash_small", C_T["T1"], 0.022, 0.030, 9, 11)
ripples("ripple_T1", C_T["T1"], (0.045, 0.078))
drops("drops_T1", C_T["T1"], (P.x, P.y, 0.07), (0.05, 0.03, 0.05), 14, 12)
fruit_slice("slice_high", C_T["T1"], (P.x, P.y, 0.42), (math.radians(64), math.radians(8), 0))
# T2: 과일이 표면에 닿고 왕관이 가장 높이 솟는다. (날개는 아직 없음: A2 화면에 큰 덩어리로 잡혀서 뺌. 날개는 A3 안에서 솟는다)
crown("crown_big", C_T["T2"], 0.044, 0.074, 13, 21)
ripples("ripple_T2", C_T["T2"], (0.082, 0.120))
drops("drops_T2", C_T["T2"], (P.x, P.y, 0.135), (0.085, 0.05, 0.05), 26, 22)
fruit_slice("slice_in", C_T["T2"], (P.x, P.y, 0.016), (math.radians(72), math.radians(10), 0))
# T3: 왕관이 가라앉아 물결만 남고 과일 조각이 떠 있다. 튄 음료가 병에 방울로 맺혀 있고, 그중 큰 한 방울이 라벨 위로 흘러내린다
ripples("ripple_T3", C_T["T3"], (0.060, 0.105, 0.138))
drops("drops_T3", C_T["T3"], (P.x + 0.01, P.y, 0.05), (0.05, 0.03, 0.035), 7, 33, 0.002, 0.005)
fruit_slice("slice_float", C_T["T3"], (P.x, P.y, 0.006), (math.radians(8), 0, 0))


def bottle_radius(z):
    if z < 0.052 or 0.118 < z <= 0.135:
        return 0.0361
    if z <= 0.118:
        return 0.0366                       # 라벨 위
    if z <= 0.156:
        return 0.036 + (0.034 - 0.036) * (z - 0.135) / 0.021
    return 0.034 + (0.022 - 0.034) * (z - 0.156) / 0.024


def bead(bm, phi, z, r, tall=1.25, flat=0.5):
    """병 겉면에 붙은 방울 하나. phi: 앞(-Y)에서 왼쪽(-X, P 쪽)으로 돈 각도."""
    rad = bottle_radius(z)
    n = Vector((-math.sin(phi), -math.cos(phi), 0))
    up = Vector((0, 0, 1))
    side = up.cross(n).normalized()
    m = Matrix((
        (side.x * r, up.x * r * tall, n.x * r * flat, n.x * (rad + r * flat * 0.35)),
        (side.y * r, up.y * r * tall, n.y * r * flat, n.y * (rad + r * flat * 0.35)),
        (side.z * r, up.z * r * tall, n.z * r * flat, z),
        (0, 0, 0, 1)))
    bmesh.ops.create_icosphere(bm, subdivisions=3, radius=1.0, matrix=m)


bm = bmesh.new()
rb = random.Random(41)
for _ in range(70):                                       # 튄 음료가 맺힌 잔 방울들 (P 쪽과 앞쪽 면)
    bead(bm, math.radians(rb.uniform(-35, 105)), rb.uniform(0.012, 0.176), rb.uniform(0.0008, 0.0024))
bead(bm, math.radians(14), 0.128, 0.0042, tall=1.5, flat=0.55)                  # 흘러내리는 큰 한 방울 (라벨 바로 위)
bead(bm, math.radians(14), 0.150, 0.0013, tall=14.0, flat=0.35)                 # 그 방울이 지나온 젖은 자국
beads = to_object("bottle_beads", bm, M_BEAD, C_T["T3"])
C_MASK.objects.link(beads)                                # 병의 일부로 다룸 (마스크에 포함)

# ---------- 빛: 흰 스튜디오. 위 왼쪽 앞의 큰 부드러운 빛, 오른쪽의 약한 빛, 뒤에서 음료를 비추는 빛 ----------
world = bpy.data.worlds.new("studio_white")
world.use_nodes = True
world.node_tree.nodes["Background"].inputs["Color"].default_value = (1, 1, 1, 1)
world.node_tree.nodes["Background"].inputs["Strength"].default_value = 1.0 if ENGINE == "CYCLES" else 0.85
scene.world = world


def area(name, loc, target, energy, size):
    data = bpy.data.lights.new(name, "AREA")
    data.energy, data.size = energy, size
    ob = bpy.data.objects.new(name, data)
    ob.location = loc
    ob.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
    C_SET.objects.link(ob)


area("key", (-0.75, -0.65, 1.05), (0, 0, 0.12), 16, 1.0)
area("fill", (0.9, -0.7, 0.45), (0, 0, 0.12), 4, 1.2)
area("back", (0.1, 0.9, 0.55), (0, 0, 0.16), 9, 0.9)


# ---------- 카메라 (모두 앞쪽) ----------
def camera(name, loc, target, lens, note):
    data = bpy.data.cameras.new(name)
    data.lens, data.sensor_width = lens, 36
    data.clip_start, data.clip_end = 0.01, 50
    ob = bpy.data.objects.new(name, data)
    ob.location = loc
    ob.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
    ob["note"] = note
    C_CAM.objects.link(ob)
    return ob


# (컷, 시작 · 끝): (카메라 위치, 보는 곳, 렌즈 mm, 장면의 순간, 설명)
SHOTS = {
    ("A1", "start"): ((-0.07, -0.70, 0.125), (-0.07, 0, 0.125), 50, "T0", "아주 가까이. 병은 오른쪽, 줄기와 P는 왼쪽"),
    ("A1", "end"): ((-0.07, -0.66, 0.125), (-0.07, 0, 0.125), 50, "T1", "아주 가까이, 조금 더 다가감"),
    ("A2", "start"): ((P.x - 0.02, -0.42, 0.045), (P.x - 0.02, 0, 0.045), 50, "T1", "앞에서 표면 높이로. 가운데가 P, 오른쪽 끝에 병"),
    ("A2", "end"): ((P.x - 0.02, -0.42, 0.045), (P.x - 0.02, 0, 0.045), 50, "T2", "앞에서 표면 높이로, 고정"),
    ("A3", "start"): ((0.04, -0.95, 0.13), (-0.02, 0, 0.125), 60, "T2", "정면 가까이. 병이 가운데, P는 왼쪽 아래"),
    ("A3", "end"): ((-0.03, -0.88, 0.13), (-0.01, 0, 0.125), 60, "T3", "정면 가까이, 아주 조금 돌며 다가감"),
}
CAMS = {key: camera(f"SF_Camera_{key[0]}_{key[1]}", loc, target, lens, note) for key, (loc, target, lens, _t, note) in SHOTS.items()}

# ---------- 렌더 설정 ----------
scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = 1920, 1080, 100
scene.render.fps = 24
scene.frame_start, scene.frame_end = 1, 120
if ENGINE == "CYCLES":
    scene.render.engine = "CYCLES"
    scene.cycles.samples, scene.cycles.use_denoising = 96, True
    scene.cycles.max_bounces, scene.cycles.transmission_bounces, scene.cycles.glossy_bounces = 10, 10, 6
    try:                                                 # 그래픽 카드가 있으면 씀. 없거나 설정이 안 되면 CPU로 렌더됨
        prefs = bpy.context.preferences.addons["cycles"].preferences
        for kind in ("OPTIX", "CUDA"):
            try:
                prefs.compute_device_type = kind
                prefs.get_devices()
                if any(dev.type == kind for dev in prefs.devices):
                    for dev in prefs.devices:
                        dev.use = dev.type == kind
                    scene.cycles.device = "GPU"
                    print("CYCLES_DEVICE", kind)
                    break
            except Exception:
                continue
    except Exception as exc:
        print("CYCLES_DEVICE cpu", exc)
else:
    scene.render.engine = "BLENDER_EEVEE_NEXT"
    scene.eevee.taa_render_samples = 48
    if hasattr(scene.eevee, "use_raytracing"):
        scene.eevee.use_raytracing = True
scene.view_settings.view_transform = "Standard"
scene.render.film_transparent = False
scene.render.image_settings.file_format = "PNG"
OUT_DIR.mkdir(parents=True, exist_ok=True)
if PREVIEW:
    PREVIEW.mkdir(parents=True, exist_ok=True)

for (cut, when), (_loc, _target, _lens, state, _note) in SHOTS.items():
    scene.camera = CAMS[(cut, when)]
    for t, coll in C_T.items():
        for ob in coll.objects:
            ob.hide_render = ob.hide_viewport = t != state
    scene.frame_set(1)
    path = OUT_DIR / f"first_drop_{cut}_{when}.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(path), copy=True)
    print("SAVED", path.name, "camera", scene.camera.name, "state", state, "mask", len(C_MASK.objects))
    if PREVIEW and (PREVIEW_ONLY is None or f"{cut}_{when}" in PREVIEW_ONLY):
        scene.render.resolution_percentage = PREVIEW_PCT
        scene.render.filepath = str(PREVIEW / f"first_drop_{cut}_{when}.png")
        bpy.ops.render.render(write_still=True)
        scene.render.resolution_percentage = 100

print("OBJECTS", len(bpy.data.objects))
print("SF_BUILD_OK")
