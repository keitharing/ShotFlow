"""영상 1 「무너지는 날」(COLLAPSE_DAY) Blender 장면을 만든다: 폐허 거리, 기울어진 7층 건물, 컷별 카메라 4대.

실행: blender -b --factory-startup --python build_scenes.py -- [저장할 폴더] [미리보기 폴더]
  저장할 폴더를 생략하면 이 스크립트 옆에 저장한다.
  컷마다 파일 하나: collapse_C1.blend ~ collapse_C4.blend (같은 세트, 카메라와 SF_MASK만 다름)
  미리보기 폴더를 주면 컷마다 PNG 한 장(960x540)을 렌더한다.

ShotFlow 3D 연동 규칙: 장면의 활성 카메라로 렌더하고, SF_MASK 컬렉션의 물체가 마스크가 된다.
  C1, C2, C4의 마스크는 주인공 건물, C3의 마스크는 인물.
단위는 미터. 거리는 -Y 쪽으로 뻗고, 주인공 건물은 거리 끝 (0, 8)에 있다.

v2 (2026-11-11): 룩 시험에서 건물이 멀쩡하게 그려져서 부서진 모습을 형태로 넣음.
  창문 자리를 파내고 모서리와 벽을 깎아 냄(불리언), 폐허 네 면에 창문, 잔해 더미, 주인공 건물 기울기 6도.
  C4는 무너진 뒤의 장면이라 위 4층을 빼고 잔해 더미를 보인다.
v3 (2026-11-11): 가편집에서 C1과 C2가 다른 장소처럼 보여서 C2를 고침.
  C2 카메라를 건물 서쪽 빈터에서 C1 · C4와 같은 거리로 옮기고, C2에서만 건물 앞면에 금을 형태로 넣음.
"""
import math
import random
import sys
from pathlib import Path

import bpy
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT_DIR = Path(argv[0]) if argv else Path(__file__).resolve().parent
PREVIEW = Path(argv[1]) if len(argv) > 1 else None

FLOOR_H = 3.4
rnd = random.Random(20261111)

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.context.preferences.filepaths.save_version = 0      # .blend1 백업 파일을 만들지 않음
scene = bpy.context.scene


def material(name, color, rough=0.9):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    bsdf = m.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*color, 1)
    bsdf.inputs["Roughness"].default_value = rough
    m.diffuse_color = (*color, 1)
    return m


M_CONC = material("concrete", (0.55, 0.53, 0.50))
M_CONC2 = material("concrete_dark", (0.40, 0.39, 0.37))
M_WIN = material("window", (0.05, 0.05, 0.06), 0.4)
M_ROAD = material("road", (0.22, 0.22, 0.22))
M_RUBBLE = material("rubble", (0.47, 0.44, 0.40))
M_SKIN = material("skin", (0.80, 0.62, 0.52), 0.6)
M_JACKET = material("jacket", (0.35, 0.35, 0.36))
M_SCARF = material("scarf", (0.65, 0.07, 0.06), 0.7)
M_HAIR = material("hair", (0.06, 0.05, 0.05), 0.6)


def collection(name):
    c = bpy.data.collections.new(name)
    scene.collection.children.link(c)
    return c


C_SET, C_MAIN, C_PERSON, C_CAM = collection("Set"), collection("Main_Building"), collection("Person"), collection("Cameras")
C_MASK = collection("SF_MASK")


def link(ob, coll):
    for c in list(ob.users_collection):
        c.objects.unlink(ob)
    coll.objects.link(ob)


def box(name, size, loc, mat, coll, rot=(0, 0, 0), parent=None):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc, rotation=rot)
    ob = bpy.context.object
    ob.name = name
    ob.scale = size
    ob.data.materials.append(mat)
    link(ob, coll)
    if parent is not None:
        ob.parent = parent
    return ob


def empty(name, loc, coll):
    ob = bpy.data.objects.new(name, None)
    ob.location = loc
    coll.objects.link(ob)
    return ob


def join(target, parts):
    """parts를 target 메시에 합친다."""
    bpy.ops.object.select_all(action="DESELECT")
    for ob in parts:
        ob.select_set(True)
    target.select_set(True)
    bpy.context.view_layer.objects.active = target
    bpy.ops.object.join()
    return target


def cut(target, cutter):
    """target에서 cutter 모양을 깎아 내고 cutter는 지운다. 깎인 면은 cutter의 재질(창문 색)을 받는다."""
    bpy.context.view_layer.update()
    mod = target.modifiers.new("carve", "BOOLEAN")
    mod.operation, mod.object, mod.solver = "DIFFERENCE", cutter, "EXACT"
    try:
        mod.material_mode = "TRANSFER"
    except (AttributeError, TypeError):
        pass
    bpy.ops.object.select_all(action="DESELECT")
    target.select_set(True)
    bpy.context.view_layer.objects.active = target
    bpy.ops.object.modifier_apply(modifier=mod.name)
    bpy.data.objects.remove(cutter, do_unlink=True)


def carve(target, loc, size, rot=(0, 0, 0), shape="cube"):
    """target에서 덩어리를 깎아 낸다. loc는 월드 좌표."""
    if shape == "cube":
        bpy.ops.mesh.primitive_cube_add(size=1, location=loc, rotation=rot)
    else:
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2, radius=0.5, location=loc, rotation=rot)
    cutter = bpy.context.object
    cutter.scale = size
    cutter.data.materials.append(M_WIN)
    cut(target, cutter)


def cut_windows(body, w, d, z0, floors, cols_x, cols_y, origin=(0.0, 0.0), parent=None, blown=0.0):
    """네 면에 창문 자리를 1.5m 깊이로 파낸다 (면마다 한 번씩 깎음). blown 비율만큼은 더 크게 뚫는다."""
    ox, oy = origin
    faces = {("x", -1): [], ("x", 1): [], ("y", -1): [], ("y", 1): []}
    for f in range(floors):
        zc = z0 + f * FLOOR_H + FLOOR_H * 0.55
        for sign in (-1, 1):
            for i in range(cols_x):
                xc = ox - w / 2 + (i + 0.5) * w / cols_x
                k = rnd.uniform(1.2, 1.5) if rnd.random() < blown else 1.0
                faces[("y", sign)].append(box("win", (w / cols_x * 0.6 * k, 1.8, FLOOR_H * 0.5 * k),
                                              (xc, oy + sign * (d / 2 - 0.6), zc), M_WIN, C_SET, parent=parent))
            for j in range(cols_y):
                yc = oy - d / 2 + (j + 0.5) * d / cols_y
                k = rnd.uniform(1.2, 1.5) if rnd.random() < blown else 1.0
                faces[("x", sign)].append(box("win", (1.8, d / cols_y * 0.6 * k, FLOOR_H * 0.5 * k),
                                              (ox + sign * (w / 2 - 0.6), yc, zc), M_WIN, C_SET, parent=parent))
    for parts in faces.values():
        bpy.context.view_layer.update()
        cut(body, join(parts[0], parts[1:]))


def building_block(name, w, d, floor0, floors, parent, coll, blown=0.15):
    """주인공 건물의 한 덩어리(층 묶음). 창문 자리를 파낸 한 메시. 원점은 parent 기준."""
    h = floors * FLOOR_H
    z0 = floor0 * FLOOR_H
    body = box(name, (w, d, h), (0, 0, z0 + h / 2), M_CONC, coll, parent=parent)
    body.data.materials.append(M_WIN)
    cut_windows(body, w, d, z0, floors, 5, 4, parent=parent, blown=blown)
    return body


def ruin(name, x, y, w, d, h, coll):
    """부서진 낮은 건물: 네 면의 창문, 깎여 나간 모서리와 벽, 삐죽한 윗부분, 발치의 잔해 더미."""
    body = box(name, (w, d, h), (x, y, h / 2), M_CONC2, coll)
    body.data.materials.append(M_WIN)
    floors = max(1, int(h // FLOOR_H))
    cut_windows(body, w, d, 0, floors, 3, 3, origin=(x, y), blown=0.45)
    # 모서리 하나를 크게 깎고, 벽에 구멍 한두 개
    cx, cy = rnd.choice((-1, 1)), rnd.choice((-1, 1))
    carve(body, (x + cx * w * 0.45, y + cy * d * 0.45, h * rnd.uniform(0.8, 1.0)),
          (w * rnd.uniform(0.5, 0.8), d * rnd.uniform(0.5, 0.8), h * rnd.uniform(0.5, 0.8)),
          rot=(rnd.uniform(-0.5, 0.5), rnd.uniform(-0.5, 0.5), rnd.uniform(0, 1.2)))
    for _ in range(rnd.randint(1, 2)):
        s = rnd.choice((-1, 1))
        r = rnd.uniform(2.4, 4.2)
        if rnd.random() < 0.5:
            loc = (x + s * w / 2, y + rnd.uniform(-0.3, 0.3) * d, h * rnd.uniform(0.3, 0.7))
        else:
            loc = (x + rnd.uniform(-0.3, 0.3) * w, y + s * d / 2, h * rnd.uniform(0.3, 0.7))
        carve(body, loc, (r, r, r * rnd.uniform(0.8, 1.3)), shape="ico")
    for k in range(rnd.randint(2, 4)):      # 남아 있는 윗부분 조각 (깎인 모서리 쪽은 피함)
        bw, bd, bh = w * rnd.uniform(0.12, 0.35), d * rnd.uniform(0.15, 0.5), rnd.uniform(0.8, 3.5)
        dx, dy = rnd.uniform(0.05, 0.36) * -cx, rnd.uniform(-0.3, 0.3)
        box(f"{name}_top", (bw, bd, bh), (x + dx * w, y + dy * d, h + bh / 2 - 0.3), M_CONC2, coll,
            rot=(rnd.uniform(-0.1, 0.1), rnd.uniform(-0.1, 0.1), rnd.uniform(-0.3, 0.3)))
    rubble(x, y, w * 0.65, d * 0.65, 26, coll, 0.4, 1.6, pile=1.6)
    return body


def rubble(cx, cy, sx, sy, n, coll, smin=0.25, smax=1.1, pile=0.0):
    for _ in range(n):
        s = rnd.uniform(smin, smax)
        box("rubble", (s * rnd.uniform(0.6, 1.8), s * rnd.uniform(0.6, 1.8), s * rnd.uniform(0.25, 0.9)),
            (cx + rnd.uniform(-sx, sx), cy + rnd.uniform(-sy, sy), s * 0.25 + rnd.uniform(0, pile)), M_RUBBLE, coll,
            rot=(rnd.uniform(-0.6, 0.6), rnd.uniform(-0.6, 0.6), rnd.uniform(0, 3.14)))


def slab(x, y, w, d, tilt, coll):
    """비스듬히 쓰러진 바닥판."""
    box("slab", (w, d, 0.35), (x, y, w * abs(math.sin(tilt)) / 2 + 0.2), M_CONC2, coll, rot=(rnd.uniform(-0.15, 0.15), tilt, rnd.uniform(0, 3.14)))


# ---------- 세트 ----------
box("ground", (260, 260, 0.2), (0, -30, -0.1), M_ROAD, C_SET)
box("sidewalk_L", (3, 96, 0.25), (-7.5, -46, 0.12), M_CONC2, C_SET)
box("sidewalk_R", (3, 96, 0.25), (7.5, -46, 0.12), M_CONC2, C_SET)

# 주인공 건물: 7층. 아래 3층과 위 4층을 따로 만들어 위쪽만 무너뜨릴 수 있게 함
MAIN_W, MAIN_D, MAIN_FLOORS, SPLIT = 16.0, 12.0, 7, 3
main_root = empty("main_root", (0, 8, 0), C_MAIN)
main_root.rotation_euler = (math.radians(-3.0), math.radians(6.0), 0)      # 눈에 띄게 기울어짐
lower = building_block("main_lower", MAIN_W, MAIN_D, 0, SPLIT, main_root, C_MAIN)
upper_root = empty("main_upper_root", (0, 0, SPLIT * FLOOR_H), C_MAIN)
upper_root.parent = main_root
upper = building_block("main_upper", MAIN_W, MAIN_D, 0, MAIN_FLOORS - SPLIT, upper_root, C_MAIN, blown=0.3)
bpy.context.view_layer.update()
top = main_root.matrix_world @ Vector((0, 0, MAIN_FLOORS * FLOOR_H))
mid = main_root.matrix_world @ Vector((0, 0, (SPLIT + 1.2) * FLOOR_H))
# 꼭대기 모서리가 떨어져 나가고, 4~5층 앞면에 금이 벌어진 자리
carve(upper, (top.x - MAIN_W * 0.42, top.y - MAIN_D * 0.4, top.z - 1.0), (8.5, 8.0, 7.0), rot=(0.5, 0.35, 0.6))
carve(upper, (top.x + MAIN_W * 0.46, top.y + MAIN_D * 0.3, top.z + 0.5), (5.0, 9.0, 4.5), rot=(-0.3, -0.5, 0.2))
for k, dx in enumerate((-5.5, -2.8, -0.2, 2.4, 5.2)):
    carve(upper, (mid.x + dx, mid.y - MAIN_D / 2 - 0.1, mid.z + (0.9 if k % 2 else -0.4)), (3.0, 2.4, 1.5 + 0.5 * (k % 2)),
          rot=(0, 0.3 * (-1) ** k, 0), shape="ico")
carve(lower, (main_root.location.x + MAIN_W * 0.5, main_root.location.y - MAIN_D * 0.45, 2.0), (4.5, 4.5, 5.5), shape="ico")

# C2용: 금이 간 위 4층 (C1에서 금이 다 내려온 뒤의 모습). 다른 컷에서는 금 없는 것을 쓴다
upper_cracked = upper.copy()
upper_cracked.data = upper.data.copy()
upper_cracked.name = "main_upper_cracked"
C_MAIN.objects.link(upper_cracked)
bpy.context.view_layer.update()
CRACK = [(-1.0, 24.4), (-0.2, 20.6), (-1.7, 18.0), (-0.4, 15.2), (-1.5, 12.6), (-0.6, 10.0)]   # 앞면(남쪽)의 (x, z), 지붕에서 4층 바닥까지
for (cx0, cz0), (cx1, cz1) in zip(CRACK, CRACK[1:]):
    cc = main_root.matrix_world @ Vector(((cx0 + cx1) / 2, -MAIN_D / 2, (cz0 + cz1) / 2))
    carve(upper_cracked, (cc.x, cc.y, cc.z), (0.6, 3.2, math.hypot(cx1 - cx0, cz1 - cz0) + 0.5),
          rot=(math.radians(-3.0), math.radians(6.0) + math.atan2(cx1 - cx0, cz1 - cz0), 0))

# 거리 양쪽의 폐허 (거리는 -Y 쪽으로 뻗음)
for k, y in enumerate([-10, -28, -46, -64, -82]):
    ruin(f"ruin_L{k}", -16.5, y, 13, 13, rnd.choice([6.8, 10.2, 13.6]), C_SET)
    ruin(f"ruin_R{k}", 16.5, y, 13, 13, rnd.choice([6.8, 10.2, 13.6]), C_SET)
# 주인공 건물 옆과 뒤
ruin("ruin_side_R", 21, 9, 12, 14, 10.2, C_SET)
ruin("ruin_back_L", -6, 30, 18, 12, 13.6, C_SET)
ruin("ruin_back_R", 16, 34, 14, 12, 6.8, C_SET)

rubble(0, -3, 9, 5, 70, C_SET, 0.3, 1.5, pile=1.2)             # 주인공 건물 앞
rubble(0, -40, 5.5, 36, 110, C_SET, 0.15, 0.7)                 # 거리 바닥
rubble(-9, -30, 2.2, 42, 50, C_SET, 0.3, 1.3, pile=0.6)
rubble(9, -30, 2.2, 42, 50, C_SET, 0.3, 1.3, pile=0.6)
for sx, sy in [(-7, -6), (9, -9), (-6, -36), (7, -52), (-8, -70)]:
    slab(sx, sy, rnd.uniform(3, 5), rnd.uniform(2.5, 4), rnd.uniform(0.25, 0.6), C_SET)

# C4용: 무너진 뒤의 잔해 더미. C4에서는 주인공 건물의 위 4층을 렌더하지 않고 이 더미를 보인다 (C1~C3에서는 숨김)
C_AFTER = collection("After_Collapse")
rubble(0, 5, 9, 7, 90, C_AFTER, 0.8, 3.2, pile=5.0)
for _ in range(6):
    slab(rnd.uniform(-8, 8), rnd.uniform(-2, 9), rnd.uniform(5, 9), rnd.uniform(4, 6), rnd.uniform(0.3, 0.9), C_AFTER)

# ---------- 인물 대리 (C3용) ----------
PX, PY = 2.4, -24.0
person = empty("person_root", (PX, PY, 0), C_PERSON)
person.rotation_euler = (0, 0, math.radians(8))


def sphere(name, r, loc, mat, scale=(1, 1, 1)):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=r, location=loc, segments=32, ring_count=16)
    ob = bpy.context.object
    ob.name = name
    ob.scale = scale
    ob.data.materials.append(mat)
    bpy.ops.object.shade_smooth()
    link(ob, C_PERSON)
    ob.parent = person
    return ob


sphere("head", 0.105, (0, 0, 1.62), M_SKIN, (0.92, 1.0, 1.18))
# 머리카락 대리 물체는 두지 않음: 이마를 비스듬히 가로지르는 경계가 있으면 AI가 그 자리에 붉은 줄을 그림 (머리카락은 프롬프트로 그림)
sphere("neck", 0.06, (0, 0, 1.47), M_SKIN, (1.0, 1.0, 1.4))
sphere("shoulders", 0.2, (0, 0, 1.30), M_JACKET, (1.25, 0.62, 0.62))     # 둥근 어깨
sphere("chest", 0.2, (0, 0, 1.10), M_JACKET, (1.05, 0.66, 1.25))
box("legs", (0.36, 0.22, 0.82), (0, 0, 0.41), M_JACKET, C_PERSON, parent=person)
bpy.ops.mesh.primitive_torus_add(major_radius=0.105, minor_radius=0.04, location=(0, 0, 1.44))
scarf = bpy.context.object
scarf.name = "scarf"
scarf.data.materials.append(M_SCARF)
bpy.ops.object.shade_smooth()
link(scarf, C_PERSON)
scarf.parent = person
box("scarf_tail", (0.2, 0.04, 0.08), (0.17, 0.03, 1.41), M_SCARF, C_PERSON, rot=(0, 0.25, 0.1), parent=person)   # 화면 왼쪽으로 날림

# ---------- 빛: 해 질 녘 역광 (룩 B, 2026-11-11 선택). 해가 거리 끝(북쪽) 낮은 곳에 있음 ----------
world = bpy.data.worlds.new("dusk")
world.use_nodes = True
world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.72, 0.62, 0.52, 1)
world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.8
scene.world = world
bpy.ops.object.light_add(type="SUN", location=(0, 60, 30))
sun = bpy.context.object
sun.name = "sun"
sun.rotation_euler = (Vector((0, 0, 8)) - Vector((-20, 120, 28))).to_track_quat("-Z", "Y").to_euler()   # 북쪽(거리 끝)의 낮은 해, 약간 왼쪽. 고른 첫 장면 4장의 해 위치와 맞춤: C1 · C2는 왼쪽 뒤, C4는 거리 끝, C3는 얼굴 정면
sun.data.energy = 3.0
sun.data.color = (1.0, 0.78, 0.55)
sun.data.angle = math.radians(6)
link(sun, C_SET)


# ---------- 카메라 ----------
def camera(name, loc, target, lens, note):
    data = bpy.data.cameras.new(name)
    data.lens = lens
    data.sensor_width = 36
    data.clip_start, data.clip_end = 0.05, 600
    ob = bpy.data.objects.new(name, data)
    ob.location = loc
    ob.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
    ob["note"] = note
    C_CAM.objects.link(ob)
    return ob


# (컷, 카메라 위치, 보는 곳, 렌즈 mm, 설명, 마스크로 쓸 컬렉션)
CUTS = [
    ("C1", (5.0, -26.0, 1.3), (0.5, 6.0, 13.0), 21, "아래에서 올려다봄, 약하게 흔들림", C_MAIN),
    # C2 v1은 건물 서쪽 빈터에서 봄: (-72.0, -8.0, 5.0) → (0.0, 6.0, 11.5), 35mm. C1과 다른 장소처럼 보여서 C1과 같은 거리로 옮김
    ("C2", (-6.0, -58.0, 7.0), (1.0, 6.0, 11.0), 30, "거리에서 비스듬히 넓게, 고정", C_MAIN),
    ("C3", (PX + 0.35, PY + 1.55, 1.56), (PX - 0.22, PY, 1.52), 50, "얼굴 가까이, 고정", C_PERSON),
    ("C4", (0.0, -84.0, 0.35), (0.0, 0.0, 4.5), 24, "땅바닥 높이, 약하게 흔들림", C_MAIN),
]
CAMS = {cut: camera(f"SF_Camera_{cut}", loc, target, lens, note) for cut, loc, target, lens, note, _ in CUTS}
scene.camera = CAMS["C1"]

# ---------- 렌더 설정 ----------
scene.render.engine = "BLENDER_EEVEE_NEXT"
scene.render.resolution_x, scene.render.resolution_y = 1920, 1080
scene.render.resolution_percentage = 100
scene.render.fps = 24
scene.frame_start, scene.frame_end = 1, 120
scene.eevee.taa_render_samples = 32
scene.view_settings.view_transform = "Standard"
scene.render.film_transparent = False
scene.render.image_settings.file_format = "PNG"
OUT_DIR.mkdir(parents=True, exist_ok=True)
if PREVIEW:
    PREVIEW.mkdir(parents=True, exist_ok=True)


def set_mask(source):
    """SF_MASK 컬렉션을 비우고 source 컬렉션의 메시를 넣는다 (물체는 원래 컬렉션에도 그대로 있음)."""
    for ob in list(C_MASK.objects):
        C_MASK.objects.unlink(ob)
    for ob in source.objects:
        if ob.type == "MESH" and not ob.hide_render:
            C_MASK.objects.link(ob)


for cut, _loc, _target, _lens, _note, mask_source in CUTS:
    scene.camera = CAMS[cut]
    after = cut == "C4"                      # C4는 무너진 뒤
    cracked = cut == "C2"                    # C2는 금이 간 뒤
    upper.hide_render = upper.hide_viewport = after or cracked
    upper_cracked.hide_render = upper_cracked.hide_viewport = not cracked
    for ob in C_PERSON.objects:              # C2는 넓은 그림이라 인물 대리를 빼 둠 (작게 찍힌 사람은 AI가 망가뜨림)
        ob.hide_render = cracked
    set_mask(mask_source)
    for ob in C_AFTER.objects:
        ob.hide_render = not after
    scene.frame_set(1)
    path = OUT_DIR / f"collapse_{cut}.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(path), copy=True)
    print("SAVED", path.name, "camera", scene.camera.name, "mask", len(C_MASK.objects))
    if PREVIEW:
        scene.render.resolution_percentage = 50
        scene.render.filepath = str(PREVIEW / f"collapse_{cut}.png")
        bpy.ops.render.render(write_still=True)
        scene.render.resolution_percentage = 100

print("OBJECTS", len(bpy.data.objects))
print("SF_BUILD_OK")
