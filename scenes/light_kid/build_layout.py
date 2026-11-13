"""영상 3 「빛을 쥔 아이」(LIGHT_KID) Blender 구도: 24컷을 파일 하나에, 카메라 하나, 빛의 자리 하나.

실행: blender -b --factory-startup --python build_layout.py -- [미리보기 폴더] [미리보기를 렌더할 컷, 예: S04,S11]
  결과: 이 스크립트 옆에 light_kid_layout.blend 와 layout_check.json. 미리보기 폴더를 주면 컷마다 PNG 한 장(640x360).

규칙
  - 프레임 번호가 컷 번호다: 1번 프레임 = S01 ... 24번 프레임 = S24. 컷의 물체는 그 프레임에서만 보인다(보이기를 키로 잡음).
  - 카메라는 하나이고 움직이지 않는다: (0, -10, 0)에서 +Y를 봄, 35mm. 렌즈를 위아래로 밀어(shift) 원점이 화면의 가로 50%, 세로 45%에 온다.
  - 빛의 자리 = 원점. 모든 컷에서 빛을 내는 물체의 중심이 원점을 지나는 시선 위에 있다.
  - 스토리보드 좌표를 그대로 쓴다: W(px, py, d) = 스토리보드 그림(480x270)의 (px, py)에 보이면서 빛보다 d미터 뒤에 있는 점. 크기도 스토리보드 px로 적는다.
  - ShotFlow 3D 연동: SF_MASK 컬렉션 = 빛을 내는 물체(마스크). SF_BEAUTY_ONLY 컬렉션 = 빛 번짐 · 빛줄기처럼 렌더에만 보이고 마스크에는 넣지 않는 것.
  - 스토리보드와 다른 곳: 불꽃 · 가로등 등의 중심을 빛의 자리에 정확히 맞춤(스토리보드는 몇 px 어긋남). 땅 · 바다는 실제 원근으로 놓아 수평선의 높이가 그림과 다를 수 있음.
모양은 구도와 깊이를 잡기 위한 덩어리다. 그림체는 뒤 단계에서 AI가 그린다.
  - 구름은 구도에 넣지 않는다 (2026-11-12): 납작한 타원으로 넣었더니 AI가 그 모양을 윤곽선까지 그대로 따라 그렸다. 구름은 프롬프트로만 그리게 한다.
    같은 까닭으로 S22의 손도 뺐다. 비례가 중요한 것(손 · 사람)을 덩어리로 넣으면 덩어리처럼 나온다.
"""
import json
import math
import random
import sys
from pathlib import Path

import bmesh
import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Matrix, Vector

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
HERE = Path(__file__).resolve().parent
PREVIEW = Path(argv[0]) if argv else None
ONLY = argv[1].split(",") if len(argv) > 1 else None
CAM_DIST, LENS, SENSOR = 10.0, 35.0, 36.0
K = CAM_DIST * SENSOR / LENS / 480          # 빛이 있는 깊이에서 스토리보드 1px의 길이(m)
LX, LY = 240, 122                           # 스토리보드에서 빛의 자리
INK, SKIN, YELLOW, GOLD = "#222222", "#d9c3a8", "#f2c94c", "#ffcf5a"
NIGHT, SPACE, DAWN, DUSK = ("#060a18", "#13203f"), ("#020308", "#070a16"), ("#2a3a6a", "#f3b27a"), ("#3b2a66", "#f09a4a")

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.context.preferences.filepaths.save_version = 0
scene = bpy.context.scene
MASK = bpy.data.collections.new("SF_MASK")
BEAUTY = bpy.data.collections.new("SF_BEAUTY_ONLY")
scene.collection.children.link(MASK)
scene.collection.children.link(BEAUTY)
SHOT, COL = "", None


def W(px, py, d=0.0):
    s = (CAM_DIST + d) / CAM_DIST
    return Vector(((px - LX) * K * s, d, (LY - py) * K * s))


def S(px, d=0.0):
    return px * K * (CAM_DIST + d) / CAM_DIST


def RX(deg):
    return Matrix.Rotation(math.radians(deg), 4, "X")


def RY(deg):
    return Matrix.Rotation(math.radians(deg), 4, "Y")


def lin(hexcolor):
    out = []
    for i in (1, 3, 5):
        c = int(hexcolor[i:i + 2], 16) / 255
        out.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    return out


_mats = {}


def mat(color, emit=0.0):
    key = (color, emit)
    if key not in _mats:
        m = bpy.data.materials.new(f"M_{color[1:]}_{emit:g}")
        m.use_nodes = True
        b = m.node_tree.nodes["Principled BSDF"]
        b.inputs["Base Color"].default_value = (*lin(color), 1)
        b.inputs["Roughness"].default_value = 0.85
        if emit:
            b.inputs["Emission Color"].default_value = (*lin(color), 1)
            b.inputs["Emission Strength"].default_value = emit
        _mats[key] = m
    return _mats[key]


def veil(color, alpha, strength=1.5, falloff=False):
    """반투명하게 빛나는 재질 (빛 번짐 · 빛줄기). falloff=True면 가운데에서 가장자리로 옅어진다 (반지름 1인 원판에 씀)."""
    key = ("veil", color, alpha, strength, falloff)
    if key not in _mats:
        m = bpy.data.materials.new(f"V_{color[1:]}_{alpha:g}")
        m.use_nodes = True
        m.surface_render_method = "BLENDED"
        m.use_backface_culling = False
        n, links = m.node_tree.nodes, m.node_tree.links
        n.remove(n["Principled BSDF"])
        emit, clear, mix = n.new("ShaderNodeEmission"), n.new("ShaderNodeBsdfTransparent"), n.new("ShaderNodeMixShader")
        emit.inputs["Color"].default_value = (*lin(color), 1)
        emit.inputs["Strength"].default_value = strength
        links.new(clear.outputs[0], mix.inputs[1])
        links.new(emit.outputs[0], mix.inputs[2])
        links.new(mix.outputs[0], n["Material Output"].inputs["Surface"])
        if falloff:
            coord, length, inv, power, scale = (n.new("ShaderNodeTexCoord"), n.new("ShaderNodeVectorMath"), n.new("ShaderNodeMath"),
                                                n.new("ShaderNodeMath"), n.new("ShaderNodeMath"))
            length.operation, inv.operation, power.operation, scale.operation = "LENGTH", "SUBTRACT", "POWER", "MULTIPLY"
            inv.use_clamp = True
            inv.inputs[0].default_value, power.inputs[1].default_value, scale.inputs[1].default_value = 1.0, 2.0, alpha
            links.new(coord.outputs["Object"], length.inputs[0])
            links.new(length.outputs["Value"], inv.inputs[1])
            links.new(inv.outputs[0], power.inputs[0])
            links.new(power.outputs[0], scale.inputs[0])
            links.new(scale.outputs[0], mix.inputs[0])
        else:
            mix.inputs[0].default_value = alpha
        _mats[key] = m
    return _mats[key]


def add(name, bm, material, loc=None, rot=None, scale=None, mask=False, beauty=False, smooth=False):
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    me = bpy.data.meshes.new(f"{SHOT}_{name}")
    bm.to_mesh(me)
    bm.free()
    if smooth:
        me.polygons.foreach_set("use_smooth", [True] * len(me.polygons))
    me.materials.append(material)
    ob = bpy.data.objects.new(f"{SHOT}_{name}", me)
    m = Matrix.Translation(loc) if loc is not None else Matrix.Identity(4)
    if rot is not None:
        m = m @ rot
    if scale is not None:
        m = m @ Matrix.Diagonal((*scale, 1.0))
    ob.matrix_world = m
    COL.objects.link(ob)
    if mask:
        MASK.objects.link(ob)
    if beauty:
        BEAUTY.objects.link(ob)
    return ob


def sph(name, loc, scale, color, rot=None, emit=0.0, mask=False):
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=28, v_segments=14, radius=1.0)
    return add(name, bm, mat(color, emit), loc=loc, rot=rot, scale=scale, mask=mask, smooth=True)


def ell(name, px, py, rx, ry, d, color, rot=0.0, thick=None, emit=0.0, mask=False):
    """화면에서 가로 rx, 세로 ry(px)로 보이는 타원체. rot = 화면에서 시계 방향 각도. thick = 앞뒤 반두께(m)."""
    return sph(name, W(px, py, d), (S(rx, d), thick if thick is not None else S(min(rx, ry), d), S(ry, d)), color, RY(rot), emit, mask)


def cyl(name, px, py, r, h, d, color, r2=None, emit=0.0, mask=False):
    """서 있는 원기둥 · 원뿔대. (px, py) = 가운데, r = 아래 반지름, r2 = 위 반지름."""
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=28, radius1=1.0, radius2=(r2 if r2 is not None else r) / r, depth=1.0)
    return add(name, bm, mat(color, emit), loc=W(px, py, d), scale=(S(r, d), S(r, d), S(h, d)), mask=mask, smooth=True)


def disc(name, px, py, r, t, d, color, emit=0.0, mask=False):
    """카메라를 마주 보는 원판 · 원통. t = 앞뒤 두께(m), d = 가운데의 깊이."""
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=48, radius1=1.0, radius2=1.0, depth=1.0)
    return add(name, bm, mat(color, emit), loc=W(px, py, d), rot=RX(90), scale=(S(r, d), S(r, d), t), mask=mask)


def rods(name, pts, r, color, emit=0.0, mask=False, beauty=False, material=None, pairs=False):
    """점들을 잇는 가는 막대. pts = [(px, py, d), ...]. pairs=True면 두 점씩 따로 잇는다. r = 반지름(px)."""
    bm = bmesh.new()
    segs = zip(pts[0::2], pts[1::2]) if pairs else zip(pts, pts[1:])
    for a, b in segs:
        pa, pb = W(*a), W(*b)
        v = pb - pa
        if v.length < 1e-6:
            continue
        m = Matrix.Translation((pa + pb) / 2) @ Vector((0, 0, 1)).rotation_difference(v.normalized()).to_matrix().to_4x4()
        rad = S(r, (a[2] + b[2]) / 2)
        bmesh.ops.create_cone(bm, cap_ends=True, segments=8, radius1=rad, radius2=rad, depth=v.length, matrix=m)
    return add(name, bm, material or mat(color, emit), mask=mask, beauty=beauty)


def extrude(name, pts, t, d, color, emit=0.0, mask=False, beauty=False, material=None):
    """스토리보드의 다각형을 앞면의 깊이 d에서 뒤로 t미터 밀어 낸 덩어리. 화면에서 보이는 윤곽은 다각형 그대로다."""
    bm = bmesh.new()
    front = [bm.verts.new(W(x, y, d)) for x, y in pts]
    back = [bm.verts.new(W(x, y, d + t)) for x, y in pts]
    bm.faces.new(front)
    bm.faces.new(back[::-1])
    for i in range(len(pts)):
        j = (i + 1) % len(pts)
        bm.faces.new((front[i], front[j], back[j], back[i]))
    return add(name, bm, material or mat(color, emit), mask=mask, beauty=beauty)


def torus(name, px, py, R, r, d, color, tilt=0.0, roll=0.0, emit=0.0, mask=False):
    """고리. tilt = 정면에서 기울인 각도(0이면 원으로 보임), roll = 화면에서 시계 방향 각도."""
    bm = bmesh.new()
    RM, rm, nu, nv = S(R, d), S(r, d), 72, 8
    vs = [[bm.verts.new(((RM + rm * math.cos(2 * math.pi * j / nv)) * math.cos(2 * math.pi * i / nu),
                         (RM + rm * math.cos(2 * math.pi * j / nv)) * math.sin(2 * math.pi * i / nu), rm * math.sin(2 * math.pi * j / nv)))
           for j in range(nv)] for i in range(nu)]
    for i in range(nu):
        for j in range(nv):
            bm.faces.new((vs[i][j], vs[(i + 1) % nu][j], vs[(i + 1) % nu][(j + 1) % nv], vs[i][(j + 1) % nv]))
    return add(name, bm, mat(color, emit), loc=W(px, py, d), rot=RY(roll) @ RX(90 + tilt), mask=mask, smooth=True)


def plane(name, z, color, near=-8.5, far=40.0, half=90.0, emit=0.0):
    bm = bmesh.new()
    bm.faces.new([bm.verts.new(p) for p in ((-half, near, z), (half, near, z), (half, far, z), (-half, far, z))])
    return add(name, bm, mat(color, emit))


def floor(name, py, color, far=40.0):
    """빛과 같은 깊이에 선 물체의 발이 py에 오는 바닥."""
    return plane(name, (LY - py) * K, color, far=far)


def horizon(name, py, color, far=60.0):
    """먼 가장자리(수평선)가 py에 보이는 바다 · 땅."""
    return plane(name, (LY - py) * K * (CAM_DIST + far) / CAM_DIST, color, far=far)


def backdrop(colors):
    top, bottom = colors
    key = ("sky", top, bottom)
    if key not in _mats:
        m = bpy.data.materials.new(f"SKY_{top[1:]}_{bottom[1:]}")
        m.use_nodes = True
        n, links = m.node_tree.nodes, m.node_tree.links
        n.remove(n["Principled BSDF"])
        coord, sep, rng, ramp, emit = (n.new("ShaderNodeTexCoord"), n.new("ShaderNodeSeparateXYZ"), n.new("ShaderNodeMapRange"),
                                       n.new("ShaderNodeValToRGB"), n.new("ShaderNodeEmission"))
        rng.inputs["From Min"].default_value, rng.inputs["From Max"].default_value = -1.0, 1.0
        ramp.color_ramp.elements[0].color, ramp.color_ramp.elements[1].color = (*lin(bottom), 1), (*lin(top), 1)
        links.new(coord.outputs["Object"], sep.inputs[0])
        links.new(sep.outputs["Y"], rng.inputs["Value"])
        links.new(rng.outputs["Result"], ramp.inputs["Fac"])
        links.new(ramp.outputs["Color"], emit.inputs["Color"])
        links.new(emit.outputs[0], n["Material Output"].inputs["Surface"])
        _mats[key] = m
    bm = bmesh.new()
    bm.faces.new([bm.verts.new(p) for p in ((-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0))])
    return add("sky", bm, _mats[key], loc=W(240, 135, 90), rot=RX(90), scale=(S(320, 90), S(190, 90), 1))


def glow(r, d=-0.3):
    """빛 번짐: 빛의 자리에 놓는, 가장자리로 옅어지는 원판. 반지름 r(px)이 컷 목록의 '빛의 크기'다."""
    bm = bmesh.new()
    bmesh.ops.create_circle(bm, cap_ends=True, segments=72, radius=1.0)
    ob = add("glow", bm, veil(GOLD, 0.55, 1.6, True), loc=W(LX, LY, d), rot=RX(90), scale=(S(r, d),) * 3, beauty=True)
    ob["sf_role"] = "glow"
    return ob


def core(r, d=0.0, color="#fff3c4"):
    return ell("light", LX, LY, r, r, d, color, emit=5.0, mask=True)


def flame(name, px, py, k, d=0.0, up=True, mask=False):
    """불꽃: 위아래 37k px, 가운데가 (px, py). up=False면 아래로 뿜는다."""
    sg = 1 if up else -1
    bm = bmesh.new()
    r, h = S(12 * k, d), S(25 * k, d)
    c = W(px, py + sg * 6.5 * k, d)
    bmesh.ops.create_uvsphere(bm, u_segments=20, v_segments=10, radius=r, matrix=Matrix.Translation(c))
    m = Matrix.Translation(c + Vector((0, 0, sg * h / 2))) @ (Matrix.Identity(4) if up else RX(180))
    bmesh.ops.create_cone(bm, cap_ends=True, segments=20, radius1=r * 0.98, radius2=r * 0.06, depth=h, matrix=m)
    add(name, bm, mat("#f0a030", 3.0), mask=mask, smooth=True)
    sph(name + "_in", W(px, py + sg * 8 * k, d) - Vector((0, r * 1.02, 0)), (S(4.5 * k, d), 0.02, S(8 * k, d)), "#fff3c4", emit=5.0, mask=mask)


def specks(name, n, seed, cx, cy, sx, sy, dmin, dmax, rmin, rmax, color, emit=3.0, mask=False):
    rnd = random.Random(seed)
    bm = bmesh.new()
    for _ in range(n):
        d = rnd.uniform(dmin, dmax)
        bmesh.ops.create_icosphere(bm, subdivisions=1, radius=S(rnd.uniform(rmin, rmax), d),
                                   matrix=Matrix.Translation(W(cx + rnd.uniform(-sx, sx), cy + rnd.uniform(-sy, sy), d)))
    return add(name, bm, mat(color, emit), mask=mask)


def quad(p0, p1, p2, n=16):
    return [((1 - t) ** 2 * p0[0] + 2 * t * (1 - t) * p1[0] + t * t * p2[0], (1 - t) ** 2 * p0[1] + 2 * t * (1 - t) * p1[1] + t * t * p2[1])
            for t in (i / n for i in range(n + 1))]


def kid(x, y, k, arm, d=0.0):
    """아이: 발이 (x, y), 크기 k. 스토리보드의 kid()와 같은 비율. 손의 자리를 돌려준다."""
    leg, body, hr = 18 * k, 26 * k, 9 * k
    top = y - leg - body
    rods("legs", [(x - 4 * k, y, d), (x - 3 * k, y - leg, d), (x + 4 * k, y, d), (x + 3 * k, y - leg, d)], 1.7 * k, SKIN, pairs=True)
    cyl("shorts", x, y - leg + 2 * k, 7.6 * k, 9 * k, d, "#2a3558")
    cyl("jacket", x, y - leg - body / 2, 8 * k, body, d, YELLOW, r2=6.5 * k)
    ell("hood", x, top - 1 * k, 8.5 * k, 6 * k, d + S(3 * k), YELLOW)
    ell("head", x + 1 * k, top - hr + 2 * k, hr, hr, d, SKIN)
    # 뒷모습: 머리카락이 카메라 쪽에서 머리를 덮는다 (얼굴 쪽 살색이 보이면 AI가 앞모습으로 그릴 수 있음)
    ell("hair", x + 1 * k, top - hr + 2 * k - 0.12 * hr, hr * 1.07, hr * 1.02, d - S(0.25 * hr), "#4a4741")
    sh = (x + 5 * k, top + 5 * k)
    hand = {"up": (x + 15 * k, top - 26 * k), "down": (x + 8 * k, y - leg - 3 * k)}[arm]
    rods("arm", [(*sh, d), (*hand, d)], 1.6 * k ** 0.6 + 1.2, YELLOW)
    ell("hand", *hand, 2.6 * k ** 0.8, 2.6 * k ** 0.8, d, SKIN)
    return hand


def rooftop(fy, feet, seed):
    """옥상: 난간의 위쪽이 fy - 12, 발이 feet(px). 뒤에 먼 건물과 낮은 해, 구름."""
    rnd = random.Random(seed)
    ell("lowsun", 404, fy - 30, 17, 17, 70, "#f6dcab", emit=2.0)
    x = -40.0
    while x < 520:
        w, h = rnd.uniform(14, 34), rnd.uniform(8, 34)
        extrude("far_building", [(x, fy - 12 - h), (x + w, fy - 12 - h), (x + w, fy + 4), (x, fy + 4)], 4.0, 25, "#6d6875")
        x += w + rnd.uniform(0, 6)
    edge = max(0.3, CAM_DIST * ((feet - LY) / (fy - LY) - 1))
    floor("roof", feet, "#8e8a86", far=edge)
    extrude("parapet", [(-60, fy - 12), (540, fy - 12), (540, fy + 2), (-60, fy + 2)], 0.3, edge, "#a09c96")


# ---------------- 24컷 ----------------
def s01():
    backdrop(NIGHT)
    floor("ground", 250, "#1c1c20", far=30)
    ell("coal", 240, 127, 10, 4.5, 0.05, "#2b2b2e")
    core(2.6)
    glow(7)


def s02():
    backdrop(NIGHT)
    # 손은 넣지 않는다 (2026-11-12): 성냥개비가 화면 오른쪽 아래 밖에서 올라온다
    rods("stick", [(433, 339, -2.0), (246, 136, 0.0)], 3, "#b08a5a")
    ell("match_head", 243, 131, 6, 8, 0.0, "#7a3b2e", rot=-35)
    flame("flame", LX, LY, 0.9, mask=True)
    glow(30)


def s03():
    backdrop(NIGHT)
    floor("ground", 262, "#1f2a1c", far=20)
    rnd = random.Random(31)
    bm = bmesh.new()
    for _ in range(60):
        d, x, h = rnd.uniform(-4, 7), rnd.uniform(-40, 520), rnd.uniform(40, 120)
        base = Vector((W(x, 262, d).x, d, (LY - 262) * K))
        m = Matrix.Translation(base + Vector((0, 0, S(h) / 2))) @ RY(rnd.uniform(-14, 14))
        bmesh.ops.create_cone(bm, cap_ends=True, segments=5, radius1=S(2.6), radius2=0.004, depth=S(h), matrix=m)
    add("grass", bm, mat("#4f5a3c"))
    specks("fireflies", 9, 32, 240, 110, 220, 90, 2, 18, 1.4, 2.6, GOLD)
    ell("body", LX - 9, LY - 4, 9, 4, 0.05, "#4a4741", rot=20)
    ell("wing1", LX - 2, LY - 11, 9, 3.5, 0.0, "#efeadc", rot=-40, thick=0.01)
    ell("wing2", LX + 5, LY - 10, 9, 3.5, 0.0, "#efeadc", rot=-10, thick=0.01)
    core(4.5, d=-0.05)
    glow(26)


def s04():
    backdrop(NIGHT)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    z = (LY - 249) * K
    add("table", bm, mat("#3a2f28"), loc=Vector((0, -0.75, z - 0.2)), scale=(18, 6.5, 0.4))
    cyl("dish", 240, 247, 54, 6, 0.0, "#cfcabf")
    cyl("candle", 240, 198, 16, 92, 0.0, "#efe9dc")
    rods("wick", [(240, 152, 0), (240, 141, 0)], 1, INK)
    flame("flame", LX, LY, 1.0, mask=True)
    glow(40)


def s05():
    backdrop(NIGHT)
    # v2 (2026-11-12, 장작에도 불이 붙어 있어야 함): 전에는 장작이 바닥에 누워 있고 불꽃이 그 위에 떠 있었다.
    # 장작을 불꽃 바로 아래로 모아 세우고(원뿔 모양), 불꽃이 장작 사이에서 올라오게 한다. 밑에는 달아오른 숯불
    floor("ground", 206, "#2a2622", far=30)
    for i, (base, tip) in enumerate((((186, 204, -0.5), (246, 139, 0.05)), ((294, 204, -0.4), (234, 139, -0.05)), ((213, 208, -1.0), (243, 143, 0.0)),
                                     ((268, 200, 0.9), (238, 141, 0.1)), ((240, 197, 1.2), (240, 145, 0.2)))):
        rods(f"log{i}", [base, tip], 6.5, ("#9a7b55", "#86694a")[i % 2])
    ell("embers", 240, 193, 27, 9, 0.2, "#f0802a", thick=S(22), emit=2.5)
    for i in range(8):
        a = 2 * math.pi * i / 8 + 0.3
        loc = Vector((88 * K * math.cos(a), 1.5 * math.sin(a), (LY - 206) * K + S(7)))
        sph("stone", loc, (S(19), S(15), S(10)), "#bdb8ad")
    flame("flame_l", 217, 160, 1.0, d=-0.3)
    flame("flame_r", 263, 160, 1.0, d=0.3)
    flame("flame", LX, LY, 1.5, mask=True)
    specks("sparks", 12, 51, 240, 60, 60, 44, -1, 2, 1, 2.2, "#f0a030")
    glow(52)


def s06():
    backdrop(NIGHT)
    cyl("cap", 240, 80, 34, 12, 0.0, "#8d8a84", r2=26)
    cage = []
    for i in range(6):
        a = math.radians(60 * i)
        dd = S(30) * math.sin(a)
        cage += [(240 + 30 * math.cos(a), 86, dd), (240 + 30 * math.cos(a), 164, dd)]
    rods("cage", cage, 1.2, INK, pairs=True)
    cyl("base", 240, 171, 30, 14, 0.0, "#8d8a84", r2=36)
    rods("handle", [(x, y, 0.0) for x, y in quad((212, 86), (240, 30), (268, 86), 12)], 1.4, INK)
    # 손은 넣지 않는다 (2026-11-12): 랜턴은 들보의 고리에 걸려 있다
    rods("beam", [(-40, 20, 0.3), (520, 14, 0.3)], 8, "#5a4632")
    rods("hook", [(240, 22, 0.15), (240, 57, 0.0)], 1.3, INK)
    flame("flame", LX, LY, 0.8, mask=True)
    glow(58)


def s07():
    backdrop(NIGHT)
    floor("ground", 240, "#20242c")
    extrude("house", [(140, 70), (340, 70), (340, 240), (140, 240)], 5.0, 0.0, "#8f8b84")
    extrude("roof", [(124, 72), (240, 8), (356, 72)], 5.4, -0.2, "#5d5a55")
    extrude("window", [(212, 94), (268, 94), (268, 150), (212, 150)], 0.05, -0.06, "#fbe7a6", emit=3.0, mask=True)
    rods("window_bars", [(240, 94, -0.12), (240, 150, -0.12), (212, 122, -0.12), (268, 122, -0.12)], 1, INK, pairs=True)
    extrude("door", [(292, 176), (322, 176), (322, 240), (292, 240)], 0.05, -0.06, "#4b4741")
    specks("stars", 8, 71, 240, 50, 230, 45, 60, 80, 1.5, 2.6, "#fff3c4")
    glow(66)


def s08():
    backdrop(NIGHT)
    disc("head", LX, LY, 38, 1.4, 0.7, "#d9d4c9")
    disc("reflector", LX, LY, 28, 0.05, -0.03, "#efe9dc")
    disc("lens", LX, LY, 14, 0.05, -0.07, "#fff3c4", emit=4.0, mask=True)
    rods("body", [(262, 138, 1.2), (440, 268, -2.5)], 19, "#8d8a84")
    glow(78)


def s09():
    backdrop(NIGHT)
    floor("ground", 262, "#1d2026")
    rods("pole", [(352, 262, 0), (352, 106, 0), (262, 100, 0)], 2.6, INK)
    extrude("lamp_head", [(214, 116), (266, 116), (258, 102), (222, 102)], 0.6, -0.3, "#8d8a84")
    ell("lamp", LX, LY, 20, 6, 0.0, "#fff3c4", thick=S(12), emit=4.0, mask=True)
    rnd = random.Random(91)
    rain = []
    for _ in range(46):
        x, y, d = rnd.uniform(0, 480), rnd.uniform(0, 250), rnd.uniform(-5, 12)
        rain += [(x, y, d), (x - 6, y + 16, d)]
    rods("rain", rain, 0.5, "#8a867d", pairs=True)
    glow(86)


def s10():
    backdrop(NIGHT)
    floor("ground", 222, "#2a2c30", far=60)
    arch = [(240 + 144 * math.cos(math.radians(a)), 130 - 126 * math.sin(math.radians(a))) for a in range(0, 181, 10)]
    extrude("tunnel", [(-300, 330), (-300, -200), (780, -200), (780, 330), (384, 330)] + arch + [(96, 330)], 40.0, 4.0, "#6e6a62")
    extrude("train", [(178, 70), (302, 70), (302, 220), (178, 220)], 14.0, 0.0, "#a8a39a")
    extrude("glass_l", [(192, 78), (236, 78), (236, 100), (192, 100)], 0.05, -0.06, "#2b2e36")
    extrude("glass_r", [(244, 78), (288, 78), (288, 100), (244, 100)], 0.05, -0.06, "#2b2e36")
    disc("headlight", LX, LY, 17, 0.08, -0.08, "#fff3c4", emit=4.0, mask=True)
    z = (LY - 222) * K
    for side in (-1, 1):
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=1.0)
        add("rail", bm, mat("#55585e"), loc=Vector((side * 34 * K, 10.0, z + 0.04)), scale=(0.1, 38.0, 0.08))
    glow(96)


def s11():
    backdrop(NIGHT)
    floor("sea", 214, "#101b33", far=70)
    extrude("rock", [(150, 214), (196, 178), (290, 184), (336, 214), (300, 222), (180, 222)], 3.0, -1.5, "#6b6862")
    cyl("tower", 240, 167, 20, 58, 0.0, "#efe9dc", r2=14)
    cyl("lamp", LX, LY, 16, 32, 0.0, "#fbe7a6", emit=3.5, mask=True)
    cyl("lamp_roof", 240, 97, 20, 18, 0.0, "#8d8a84", r2=0.5)
    beam = veil(GOLD, 0.22, 1.4)
    extrude("beam_l", [(LX, LY), (-40, 64), (-40, 124)], 0.02, 0.05, GOLD, beauty=True, material=beam)
    extrude("beam_r", [(LX, LY), (520, 82), (520, 142)], 0.02, 0.05, GOLD, beauty=True, material=beam)
    specks("stars", 14, 111, 240, 50, 230, 48, 60, 80, 1.5, 2.6, "#fff3c4")
    glow(104)


def s12():
    backdrop(NIGHT)
    floor("ground", 238, "#17191f", far=60)
    rnd = random.Random(12)
    x = -40.0
    while x < 520:
        w, h = rnd.uniform(18, 40), rnd.uniform(14, 56)
        extrude("building", [(x, 234 - h), (x + w, 234 - h), (x + w, 240), (x, 240)], 4.0, 25, "#3d4048")
        x += w
    rnd = random.Random(121)
    rays, tips, half = [], bmesh.new(), []
    for i in range(28):
        if i < 14:
            half.append((rnd.uniform(-0.05, 0.05), rnd.uniform(20, 30), rnd.uniform(64, 96), rnd.uniform(-1.5, 1.5)))
        jit, r1, r2, dd = half[i % 14]          # 마주 보는 줄기는 길이가 같다: 불꽃의 가운데가 빛의 자리에 오게
        a = i * 2 * math.pi / 28 + jit
        rays += [(LX + r1 * math.cos(a), LY + r1 * math.sin(a), 0.0), (LX + r2 * math.cos(a), LY + r2 * math.sin(a), dd)]
        bmesh.ops.create_icosphere(tips, subdivisions=1, radius=S(2.4, dd), matrix=Matrix.Translation(W(*rays[-1])))
    rods("burst", rays, 0.9, GOLD, emit=3.0, mask=True, pairs=True)
    add("burst_tips", tips, mat("#fff3c4", 4.0), mask=True)
    core(8)
    glow(112)


def s13():
    backdrop(NIGHT)
    floor("ground", 250, "#22242a", far=50)
    rnd = random.Random(131)
    for _ in range(14):
        r = rnd.uniform(14, 30)
        ell("smoke", 240 + rnd.uniform(-78, 78), 226 + rnd.uniform(-18, 18), r, r, rnd.uniform(-1.5, 2.0), "#b9b4aa")
    # 몸통의 아래 끝을 스토리보드보다 3px 올림: 몸통이 불꽃의 위쪽을 가려 빛의 가운데가 2픽셀 내려가 보였음
    cyl("body", 240, 62, 16, 62, 0.0, "#efe9dc")
    cyl("nose", 240, 17, 16, 28, 0.0, "#efe9dc", r2=0.5)
    extrude("fin_l", [(224, 67), (204, 101), (224, 93)], 0.12, -0.06, "#8d8a84")
    extrude("fin_r", [(256, 67), (276, 101), (256, 93)], 0.12, -0.06, "#8d8a84")
    ell("port", 240, 45, 6, 6, -S(16), "#bdd3e6", thick=0.05)
    flame("flame", LX, LY, 1.5, up=False, mask=True)
    glow(118)


def s14():
    backdrop(NIGHT)
    horizon("sea", 190, "#0f1a30", far=60)
    ell("moon", LX, LY, 46, 46, 50, "#f3ecd2", emit=2.5, mask=True)
    front = 50 - S(46, 50) * 0.96
    for cx, cy, r in ((-14, -10, 8), (16, 12, 11), (8, -22, 5)):
        ell("crater", LX + cx, LY + cy, r, r, front, "#d5cba6", thick=0.4, emit=1.8, mask=True)      # 달의 무늬도 빛에 넣음: 마스크에 구멍이 나지 않게
    for i, w in enumerate((70, 54, 40, 26)):
        row = 228 + 9 * i
        d = CAM_DIST * (7 * 68 / (row - LY)) - CAM_DIST
        extrude("moon_path", [(LX - w, row - 1.2), (LX + w, row - 1.2), (LX + w, row + 1.2), (LX - w, row + 1.2)], 0.05, d, GOLD, emit=1.5)
    glow(128)


def s15():
    backdrop(DAWN)
    ell("sun", LX, LY, 54, 54, 60, "#f6dcab", emit=3.0, mask=True)
    extrude("ridge", [(-60, 290), (-60, 196), (0, 196), (90, 150), (170, 206), (250, 168), (330, 214), (410, 160), (480, 200), (540, 180), (540, 290)],
            3.0, 12, "#5a5f6e")
    extrude("hill", [(-60, 300), (-60, 244), (120, 228), (300, 250), (540, 232), (540, 300)], 2.0, 3, "#3a3d48")
    glow(140)


def s16():
    backdrop(SPACE)
    ell("sun", LX, LY, 62, 62, 60, "#fff3c4", emit=4.0, mask=True)
    ell("moon", LX, LY, 58, 58, 35, "#0a0a0c")
    specks("stars", 30, 161, 240, 135, 235, 130, 80, 86, 1.2, 2.4, "#fff3c4")
    glow(170)


def s17():
    backdrop(SPACE)
    ell("sun", LX, LY, 88, 88, 50, "#f0a040", emit=3.0, mask=True)
    torus("flare1", LX - 88 * math.cos(math.radians(35)), LY - 88 * math.sin(math.radians(35)), 30, 3, 50, "#ffd27a", emit=3.0)
    torus("flare2", LX + 88 * math.cos(math.radians(20)), LY + 88 * math.sin(math.radians(20)), 34, 3, 50, "#ffd27a", emit=3.0)
    specks("stars", 20, 171, 240, 135, 235, 130, 80, 86, 1.2, 2.4, "#fff3c4")
    glow(210)


def s18():
    backdrop(SPACE)
    for n, (cx, cy, r, seed) in enumerate(((70, 60, 34, 181), (410, 210, 38, 182), (420, 50, 26, 183), (60, 220, 30, 184))):
        rnd = random.Random(seed)
        for _ in range(7):
            q = r * rnd.uniform(0.5, 0.9)
            ell(f"nebula{n}", cx + rnd.uniform(-r, r), cy + rnd.uniform(-0.6 * r, 0.6 * r), q, q, rnd.uniform(-2, 8), ("#3b3f5c", "#4a3f66")[n % 2])
    rods("spikes", [(LX - 220, LY, 0), (LX + 220, LY, 0), (LX, LY - 120, 0), (LX, LY + 140, 0), (LX - 70, LY - 70, 0), (LX + 70, LY + 70, 0),
                    (LX - 70, LY + 70, 0), (LX + 70, LY - 70, 0)], 1.1, GOLD, emit=3.0, pairs=True)
    specks("sparks", 26, 185, 240, 130, 220, 120, -3, 20, 1, 2.4, "#fff3c4")
    ell("star", LX, LY, 30, 30, 0.0, "#fff3c4", emit=5.0, mask=True)
    glow(250)


def s19():
    backdrop(SPACE)
    tilt, roll = math.degrees(math.acos(74 / 226)), -18
    for R in (226, 184, 140, 96):
        torus("ring", LX, LY, R, 1.6, 0.0, GOLD, tilt=tilt, roll=roll, emit=2.5)
    rnd = random.Random(191)
    bm = bmesh.new()
    m = RY(roll) @ RX(90 + tilt)
    for _ in range(70):
        rho, phi = S(rnd.uniform(20, 235)), rnd.uniform(0, 2 * math.pi)
        p = m @ Vector((rho * math.cos(phi), rho * math.sin(phi), rnd.uniform(-0.15, 0.15)))
        bmesh.ops.create_icosphere(bm, subdivisions=1, radius=S(rnd.uniform(0.8, 2.2)), matrix=Matrix.Translation(p))
    add("disc_stars", bm, mat("#fff3c4", 3.0))
    ell("bulge", LX, LY, 46, 26, 0.0, "#fff3c4", rot=roll, thick=S(26), emit=4.0, mask=True)
    glow(280)


def s20():
    backdrop(NIGHT)
    extrude("flood", [(-80, -60), (560, -60), (560, 330), (-80, 330)], 0.1, 6.0, "#fff3c4", emit=1.2)
    disc("light", LX, LY, 150, 0.1, 3.0, "#fffaf0", emit=2.5, mask=True)


def s21():
    backdrop(NIGHT)
    sph("eyeball", W(240, 128, 9.4), (9.0, 9.0, 9.0), "#f7f3ea")
    disc("iris", 240, 128, 88, 0.1, 0.25, "#b9a27a")
    disc("pupil", 240, 128, 40, 0.1, 0.15, "#111111")
    upper = [(-120, -120), (600, -120), (600, 132)] + quad((450, 132), (240, -14), (30, 132)) + [(-120, 132)]
    lower = [(-120, 132)] + quad((30, 132), (240, 268), (450, 132)) + [(600, 132), (600, 400), (-120, 400)]
    extrude("lid_upper", upper, 0.6, -0.7, SKIN)
    extrude("lid_lower", lower, 0.6, -0.7, SKIN)
    lashes = []
    for i in range(9):
        x = 80 + i * 40
        t = (x - 30) / 420
        y = 132 - 292 * t * (1 - t)
        lashes += [(x, y + 1, -0.75), (x - 8 + i * 2, y - 16, -0.95)]
    rods("lashes", lashes, 1.2, INK, pairs=True)
    ell("light", LX, LY, 9, 9, 0.05, "#fff3c4", thick=0.05, emit=5.0, mask=True)
    glow(46)


def s22():
    backdrop(DUSK)
    # 손은 넣지 않는다 (2026-11-12, 손이 있으면 어색함). 빛과 고리, 입자만 하늘에 떠 있다
    tilt = math.degrees(math.acos(17 / 58))
    torus("orbit1", LX, LY, 58, 1.0, 0.0, GOLD, tilt=tilt, roll=22, emit=3.0)
    torus("orbit2", LX, LY, 58, 1.0, 0.0, GOLD, tilt=tilt, roll=-26, emit=3.0)
    specks("sparks", 26, 8, LX, LY, 80, 56, -1, 1.5, 1, 2.4, "#fff3c4")
    core(11)
    glow(44)


def s23():
    backdrop(DUSK)
    rooftop(210, 234, 5)
    kid(216, 234, 1.6, "up")
    n = 20
    path = []
    for i in range(n + 1):
        t = i / n
        p = [(1 - t) ** 3 * a + 3 * t * (1 - t) ** 2 * b + 3 * t * t * (1 - t) * c + t ** 3 * e
             for a, b, c, e in zip((240, 122), (246, 82), (260, 50), (276, -30))]
        path.append((p[0], p[1], 3.0 * t))
    rods("ribbon", path, 2.2, GOLD, emit=3.0)
    specks("sparks", 22, 231, 256, 60, 50, 50, 0, 4, 1, 2.4, "#fff3c4")
    core(5.5, d=-0.05)
    glow(22)


def s24():
    backdrop(DUSK)
    rooftop(240, 239, 7)
    kid(240, 239, 0.75, "down")
    rods("column", [(240, 214, 0.5), (240, -30, 6.0)], 3, GOLD, beauty=True, material=veil(GOLD, 0.3, 1.5))
    specks("light", 110, 13, 240, 104, 232, 96, 4, 45, 1, 3.2, "#ffe9a8", mask=True)


SHOTS = [s01, s02, s03, s04, s05, s06, s07, s08, s09, s10, s11, s12, s13, s14, s15, s16, s17, s18, s19, s20, s21, s22, s23, s24]

# ---------------- 카메라 · 조명 · 설정 ----------------
cam_data = bpy.data.cameras.new("SF_Camera")
cam_data.lens, cam_data.sensor_width, cam_data.clip_start, cam_data.clip_end = LENS, SENSOR, 0.1, 600.0
cam_data.shift_y = -(135 - LY) / 480          # 원점이 화면 가운데보다 13px(스토리보드) 위에 오게
cam = bpy.data.objects.new("SF_Camera", cam_data)
cam.location, cam.rotation_euler = (0, -CAM_DIST, 0), (math.pi / 2, 0, 0)
scene.collection.objects.link(cam)
scene.camera = cam
key = bpy.data.objects.new("SF_KeyLight", bpy.data.lights.new("SF_KeyLight", "POINT"))
key.data.energy, key.data.shadow_soft_size, key.location = 450, 0.5, W(LX, LY, -2.5)
fill = bpy.data.objects.new("SF_Fill", bpy.data.lights.new("SF_Fill", "SUN"))
fill.data.energy, fill.rotation_euler = 1.2, (math.radians(62), 0, math.radians(-24))
scene.collection.objects.link(key)
scene.collection.objects.link(fill)
world = bpy.data.worlds.new("SF_World")
world.use_nodes = True
world.node_tree.nodes["Background"].inputs[0].default_value = (0.03, 0.035, 0.05, 1)
scene.world = world
scene.render.engine = "BLENDER_EEVEE_NEXT"
scene.eevee.taa_render_samples = 32
scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = 1280, 720, 100
scene.render.fps, scene.frame_start, scene.frame_end = 24, 1, len(SHOTS)
scene.view_settings.view_transform = "Standard"

# ---------------- 만들기: 컷마다 컬렉션, 그 프레임에서만 보이게 ----------------
for frame, build in enumerate(SHOTS, 1):
    SHOT = f"S{frame:02d}"
    COL = bpy.data.collections.new(SHOT)
    scene.collection.children.link(COL)
    build()
    for ob in COL.objects:
        for attr in ("hide_render", "hide_viewport"):
            for f, hidden in {1: True, frame: False, frame + 1: True}.items():
                setattr(ob, attr, hidden)
                ob.keyframe_insert(attr, frame=f)
        for curve in ob.animation_data.action.fcurves:
            for point in curve.keyframe_points:
                point.interpolation = "CONSTANT"

# ---------------- 검사: 컷마다 보이는 물체, 빛의 자리, 빛의 크기 ----------------
expected = {}
shots_file = HERE.parents[3] / "02_포트폴리오" / "02_영상_제작과정" / "영상3_빛을쥔아이" / "컷_목록.json"
if shots_file.exists():
    expected = {s["no"]: s for s in json.loads(shots_file.read_text(encoding="utf-8"))["shots"]}
report, bad = [], 0
for frame in range(1, len(SHOTS) + 1):
    no = f"S{frame:02d}"
    scene.frame_set(frame)
    bpy.context.view_layer.update()
    shown = [o for o in scene.objects if o.type == "MESH" and not o.hide_render]
    wrong = [o.name for o in shown if not o.name.startswith(no)]
    row = {"no": no, "frame": frame, "name": expected.get(no, {}).get("name"), "objects": len(shown), "wrong_shot_objects": wrong}
    lights = [o for o in shown if o.name in MASK.objects]
    if lights and no != "S24":
        uv = [world_to_camera_view(scene, cam, o.matrix_world @ Vector(c)) for o in lights for c in o.bound_box]
        cx, cy = (min(p.x for p in uv) + max(p.x for p in uv)) / 2, 1 - (min(p.y for p in uv) + max(p.y for p in uv)) / 2
        row["light_at"] = [round(cx, 4), round(cy, 4)]
        row["light_off_px"] = [round((cx - 0.5) * 1280, 1), round((cy - LY / 270) * 720, 1)]
        if max(abs(v) for v in row["light_off_px"]) > 2.0:
            bad += 1
    halo = [o for o in shown if o.get("sf_role") == "glow"]
    if halo:
        c = halo[0].matrix_world.translation
        a, b = world_to_camera_view(scene, cam, c), world_to_camera_view(scene, cam, c + Vector((halo[0].matrix_world.to_scale().x, 0, 0)))
        row["light_radius_px"] = round((b.x - a.x) * 1280, 1)
        want = expected.get(no, {}).get("light_radius_px")
        row["light_radius_expected_px"] = want
        if want is not None and abs(row["light_radius_px"] - want) > 2.0:
            bad += 1
    if wrong:
        bad += 1
    report.append(row)
    print("SF_LAYOUT", no, row.get("objects"), row.get("light_off_px"), row.get("light_radius_px"), row.get("light_radius_expected_px"), wrong[:2])
origin = world_to_camera_view(scene, cam, Vector((0, 0, 0)))
(HERE / "layout_check.json").write_text(json.dumps({"camera": {"location": [0, -CAM_DIST, 0], "lens_mm": LENS, "shift_y": cam_data.shift_y},
                                                    "origin_at": [round(origin.x, 4), round(1 - origin.y, 4)], "problems": bad, "shots": report},
                                                   ensure_ascii=False, indent=1), encoding="utf-8")
print("SF_LAYOUT origin", round(origin.x, 4), round(1 - origin.y, 4), "| problems", bad)

scene.frame_set(1)
bpy.ops.wm.save_as_mainfile(filepath=str(HERE / "light_kid_layout.blend"))
print("SF_LAYOUT saved", HERE / "light_kid_layout.blend")

if PREVIEW:
    PREVIEW.mkdir(parents=True, exist_ok=True)
    scene.render.resolution_percentage = 50
    scene.eevee.taa_render_samples = 16
    scene.render.image_settings.file_format = "PNG"
    for frame in range(1, len(SHOTS) + 1):
        no = f"S{frame:02d}"
        if ONLY and no not in ONLY:
            continue
        scene.frame_set(frame)
        scene.render.filepath = str(PREVIEW / f"{no}.png")
        bpy.ops.render.render(write_still=True)
    print("SF_LAYOUT previews", PREVIEW)
