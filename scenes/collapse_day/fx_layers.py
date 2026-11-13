"""영상 1 효과 층: Blender에서 파편과 먼지를 만들어 컷 카메라로 렌더한다 (생성 영상 위에 합성할 층).

실행: blender -b collapse_<컷>.blend --python fx_layers.py -- <컷> <출력 폴더> <시작 프레임> <끝 프레임> [렌더할 프레임만 쉼표로] [layer|view|both]
출력: <출력 폴더>/layer/####.png  투명 배경에 효과만 (장면의 건물 · 바닥은 가리개로만 씀)
      <출력 폴더>/view/####.png   회색 장면 + 효과 (과정 기록용)
      scenes/collapse_day/fx/collapse_<컷>_fx.blend  효과가 들어간 장면
프레임 번호 = 생성 영상의 프레임 번호(0부터 셈). 파편은 물리 시뮬레이션이 아니라 스크립트로 계산한 포물선 · 튕김을 키프레임으로 구움.
"""
import math
import random
import sys
from pathlib import Path

import bmesh
import bpy
import numpy as np
from mathutils import Vector, noise

argv = sys.argv[sys.argv.index("--") + 1:]
CUT, OUT = argv[0], Path(argv[1])
F0, F1 = int(argv[2]), int(argv[3])
ONLY = [int(x) for x in argv[4].split(",")] if len(argv) > 4 and argv[4] not in ("", "all") else None
PASSES = argv[5] if len(argv) > 5 else "both"
FPS, G = 24, 9.81
scene = bpy.context.scene
cam = scene.objects[f"SF_Camera_{CUT}"]
scene.camera = cam
scene.frame_start, scene.frame_end = F0, F1
FRAMES = np.arange(F0 - 1, F1 + 2, dtype=np.float32)
rnd = random.Random({"C1": 101, "C2": 202, "C4": 404}[CUT])
C_FX = bpy.data.collections.new("FX")
scene.collection.children.link(C_FX)


# ---------- 화면 좌표 ↔ 세계 좌표 ----------
def ray(x, y):
    """생성 영상(1280×720)의 픽셀 (x, y)로 가는 광선."""
    fr = cam.data.view_frame(scene=scene)
    left, right = min(p.x for p in fr), max(p.x for p in fr)
    bottom, top = min(p.y for p in fr), max(p.y for p in fr)
    p = Vector((left + (right - left) * x / 1280, top + (bottom - top) * y / 720, fr[0].z))
    return cam.matrix_world.translation.copy(), (cam.matrix_world.to_3x3() @ p).normalized()


def on_plane(x, y, point, normal):
    o, d = ray(x, y)
    return o + d * ((point - o).dot(normal) / d.dot(normal))


def on_ground(x, y, z=0.0):
    return on_plane(x, y, Vector((0, 0, z)), Vector((0, 0, 1)))


# ---------- 파편 ----------
def rock_meshes(n=10):
    out = []
    for k in range(n):
        bm = bmesh.new()
        bmesh.ops.create_icosphere(bm, subdivisions=2, radius=0.5)
        sx, sy, sz = 1.0, rnd.uniform(0.55, 1.0), rnd.uniform(0.3, 0.7)       # 납작한 조각이 많게
        seed = Vector((k * 7.3, k * 3.1, k * 5.7))
        for v in bm.verts:                                                     # 울퉁불퉁하게: 방향마다 다른 만큼 밀고 당김
            bump = 1 + 0.75 * noise.noise(v.co * 2.6 + seed) + 0.25 * noise.noise(v.co * 7.0 + seed)
            v.co = Vector((v.co.x * sx, v.co.y * sy, v.co.z * sz)) * bump
        me = bpy.data.meshes.new(f"fx_rock_{k}")
        bm.to_mesh(me)
        bm.free()
        out.append(me)
    return out


def mat_rock(name, color):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*color, 1)
    b.inputs["Roughness"].default_value = 0.95
    return m


def bake(ob, locs=None, rots=None, scales=None, alpha=None):
    ob.rotation_mode = "XYZ"
    ob.animation_data_create()
    act = bpy.data.actions.new(ob.name)
    ob.animation_data.action = act
    n = len(FRAMES)

    def curve(path, index, values):
        fc = act.fcurves.new(path, index=index)
        fc.keyframe_points.add(n)
        co = np.empty(n * 2, np.float32)
        co[0::2], co[1::2] = FRAMES, values
        fc.keyframe_points.foreach_set("co", co)
        fc.keyframe_points.foreach_set("interpolation", [1] * n)      # 1 = LINEAR
        fc.update()

    for path, arr in (("location", locs), ("rotation_euler", rots), ("scale", scales)):
        if arr is not None:
            for i in range(3):
                curve(path, i, np.asarray(arr, np.float32)[:, i])
    if alpha is not None:
        curve("color", 3, np.asarray(alpha, np.float32))


ROCKS, IMPACTS = None, []


def chunk(p0, v0, t0, size, mat, ground=0.0, rest=0.28, fric=0.55, spin=None, life=None, bury=None):
    """t0 프레임에 p0에서 v0로 떨어져 나오는 조각. 바닥에서 튕기고, 처음 닿은 곳을 IMPACTS에 적는다.
    bury: 바닥에 처음 닿고 이 프레임 수가 지나면 없앰 (생성 영상의 먼지 속에 묻히는 조각)."""
    global ROCKS
    ROCKS = ROCKS or rock_meshes()
    ob = bpy.data.objects.new("fx_chunk", rnd.choice(ROCKS))
    C_FX.objects.link(ob)
    ob.active_material = mat
    pos, vel = Vector(p0), Vector(v0)
    w = Vector(spin) if spin else Vector([rnd.uniform(-7, 7) for _ in range(3)])
    ang = Vector([rnd.uniform(0, 6.28) for _ in range(3)])
    locs, rots, scales, first, hit_f = [], [], [], True, None
    for f in FRAMES:
        alive = f >= t0 and (life is None or f < t0 + life) and (bury is None or hit_f is None or f < hit_f + bury)
        if f > t0:
            for _ in range(4):
                dt = 1 / FPS / 4
                vel.z -= G * dt
                vel *= 1 - 0.03 * dt
                pos += vel * dt
                ang += w * dt
                if pos.z < ground + size * 0.3:
                    pos.z = ground + size * 0.3
                    if vel.z < 0:
                        hit_f = float(f) if hit_f is None else hit_f
                        if first and abs(vel.z) > 3:
                            IMPACTS.append((float(f), pos.copy(), size))
                            first = False
                        vel.z = -vel.z * rest
                        vel.x *= fric
                        vel.y *= fric
                        w *= 0.5
        locs.append(tuple(pos))
        rots.append(tuple(ang))
        scales.append((size, size, size) if alive else (0, 0, 0))
    bake(ob, locs, rots, scales)
    return ob


# ---------- 먼지 ----------
def mat_dust(name, color, density=4.0, aniso=0.35, glow=0.30):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    vol = nt.nodes.new("ShaderNodeVolumePrincipled")
    vol.inputs["Color"].default_value = (*color, 1)
    vol.inputs["Anisotropy"].default_value = aniso
    vol.inputs["Emission Color"].default_value = (*color, 1)      # 그늘 속 먼지도 하늘빛을 받은 것처럼 (EEVEE 볼륨은 하늘빛을 거의 못 받음)
    g = nt.nodes.new("ShaderNodeMath"); g.operation = "MULTIPLY"; g.inputs[1].default_value = glow      # 짙은 곳만 빛나게 (짙기 × glow)
    tc = nt.nodes.new("ShaderNodeTexCoord")
    info = nt.nodes.new("ShaderNodeObjectInfo")
    length = nt.nodes.new("ShaderNodeVectorMath"); length.operation = "LENGTH"
    fall = nt.nodes.new("ShaderNodeMapRange")                 # 가운데 1, 가장자리 0
    fall.inputs["From Min"].default_value, fall.inputs["From Max"].default_value = 0.15, 1.0
    fall.inputs["To Min"].default_value, fall.inputs["To Max"].default_value = 1.0, 0.0
    off = nt.nodes.new("ShaderNodeVectorMath"); off.operation = "ADD"      # 덩어리마다 다른 무늬
    rnd_scale = nt.nodes.new("ShaderNodeMath"); rnd_scale.operation = "MULTIPLY"; rnd_scale.inputs[1].default_value = 37.0
    ntex = nt.nodes.new("ShaderNodeTexNoise")
    ntex.inputs["Scale"].default_value, ntex.inputs["Detail"].default_value, ntex.inputs["Roughness"].default_value = 2.1, 7.0, 0.66
    shape = nt.nodes.new("ShaderNodeMapRange")
    shape.inputs["From Min"].default_value, shape.inputs["From Max"].default_value = 0.44, 0.74
    m1 = nt.nodes.new("ShaderNodeMath"); m1.operation = "MULTIPLY"
    m2 = nt.nodes.new("ShaderNodeMath"); m2.operation = "MULTIPLY"
    m3 = nt.nodes.new("ShaderNodeMath"); m3.operation = "MULTIPLY"; m3.inputs[1].default_value = density
    L = nt.links.new
    L(tc.outputs["Object"], length.inputs[0]); L(length.outputs["Value"], fall.inputs["Value"])
    L(info.outputs["Random"], rnd_scale.inputs[0]); L(tc.outputs["Object"], off.inputs[0]); L(rnd_scale.outputs["Value"], off.inputs[1])
    L(off.outputs["Vector"], ntex.inputs["Vector"]); L(ntex.outputs["Fac"], shape.inputs["Value"])
    L(fall.outputs["Result"], m1.inputs[0]); L(shape.outputs["Result"], m1.inputs[1])
    L(m1.outputs["Value"], m2.inputs[0]); L(info.outputs["Alpha"], m2.inputs[1])
    L(m2.outputs["Value"], m3.inputs[0]); L(m3.outputs["Value"], vol.inputs["Density"])
    L(m3.outputs["Value"], g.inputs[0]); L(g.outputs["Value"], vol.inputs["Emission Strength"])
    L(vol.outputs["Volume"], out.inputs["Volume"])
    return m


PUFF = None


def puff(mat, keys, stretch=(1, 1, 1)):
    """먼지 덩어리. keys = [(프레임, 위치, 반지름, 짙기 0~1), ...] 사이는 고르게 이어 줌."""
    global PUFF
    if PUFF is None:
        bm = bmesh.new()
        bmesh.ops.create_icosphere(bm, subdivisions=2, radius=1.0)
        PUFF = bpy.data.meshes.new("fx_puff")
        bm.to_mesh(PUFF)
        bm.free()
    ob = bpy.data.objects.new("fx_dust", PUFF)
    C_FX.objects.link(ob)
    ob.active_material = mat
    ob.rotation_euler = [rnd.uniform(0, 6.28) for _ in range(3)] if stretch == (1, 1, 1) else (0, 0, rnd.uniform(0, 6.28))
    kf = [k[0] for k in keys]
    locs = np.stack([np.interp(FRAMES, kf, [k[1][i] for k in keys]) for i in range(3)], axis=1)
    rad = np.interp(FRAMES, kf, [k[2] for k in keys])
    alpha = np.interp(FRAMES, kf, [k[3] for k in keys], left=0.0, right=0.0)
    bake(ob, locs, None, np.stack([rad * stretch[0], rad * stretch[1], rad * stretch[2]], axis=1), alpha)
    return ob


def v3(*a):
    return Vector(a)


def jitter(s):
    return Vector([rnd.uniform(-s, s) for _ in range(3)])


# ============ C1: 금을 따라 떨어지는 조각과 먼지 ============
def fx_c1():
    root = scene.objects["main_root"]
    nrm = (root.matrix_world.to_3x3() @ Vector((0, -1, 0))).normalized()          # 앞면이 보는 쪽
    face = root.matrix_world @ Vector((0, -6.0, 10))
    side = (root.matrix_world.to_3x3() @ Vector((1, 0, 0))).normalized()
    # 생성 영상에서 잰 금의 자리(픽셀)와, 금 끝이 그 점에 닿는 프레임
    CRACK = [(690, 125, 31), (700, 170, 32.5), (693, 228, 34.5), (668, 258, 36), (655, 300, 54), (655, 345, 72)]
    pts = [on_plane(x, y, face, nrm) for x, y, _ in CRACK]
    times = [t for _, _, t in CRACK]

    def at(f, lo=0.0):
        """프레임 f에 금 끝이 있는 곳까지 중에서 고른 한 점 (금 끝 쪽이 더 잘 뽑힘). lo: 금 위쪽 끝에서 이만큼은 건너뜀."""
        k = max(i for i, t in enumerate(times) if t <= f) if f >= times[0] else 0
        tip = k + (min(1.0, (f - times[k]) / (times[k + 1] - times[k])) if k + 1 < len(times) else 0.0)
        s = max(min(lo, tip), tip * (1 - rnd.random() ** 2.2))
        i = min(int(s), len(pts) - 2)
        return pts[i].lerp(pts[i + 1], s - i)

    m_light = mat_rock("fx_concrete", (0.60, 0.55, 0.48))
    m_dark = mat_rock("fx_concrete_dark", (0.36, 0.32, 0.28))
    dust = mat_dust("fx_dust", (0.74, 0.66, 0.56), density=1.9, glow=0.40)
    births = [rnd.uniform(31, 38) for _ in range(46)] + [31 + 53 * rnd.random() ** 1.5 for _ in range(44)]
    for k, t0 in enumerate(births):                                           # 눈에 보이는 조각
        size = min(1.5, 0.22 * math.exp(rnd.gauss(0.35, 0.6)))
        p = at(t0) + nrm * rnd.uniform(0.25, 0.5) + side * rnd.uniform(-0.35, 0.35)
        v = nrm * rnd.uniform(0.5, 2.8) + side * rnd.uniform(-1.3, 1.3) + v3(0, 0, rnd.uniform(-0.8, 1.6))
        chunk(p, v, t0, size, m_light if rnd.random() < 0.6 else m_dark)
    for _ in range(110):                                                      # 부스러기
        t0 = 31 + 55 * rnd.random() ** 1.3
        p = at(t0) + nrm * rnd.uniform(0.2, 0.45) + side * rnd.uniform(-0.3, 0.3)
        v = nrm * rnd.uniform(0.3, 3.5) + side * rnd.uniform(-1.8, 1.8) + v3(0, 0, rnd.uniform(-1.0, 2.2))
        chunk(p, v, t0, rnd.uniform(0.05, 0.11), m_dark)
    for k in range(18):                                                       # 금이 벌어질 때 터져 나오는 먼지
        t0 = rnd.uniform(31, 37) if k < 12 else rnd.uniform(40, 74)
        p = at(t0, 2.0) + nrm * 0.7      # 지붕 위 하늘로는 올라가지 않게 금 위쪽은 건너뜀
        drift = nrm * rnd.uniform(1.2, 2.6) + side * rnd.uniform(-0.2, 0.9) + v3(0, 0, rnd.uniform(-2.2, -0.6))
        r0 = rnd.uniform(0.5, 0.9)
        puff(dust, [(t0 - 1, p, r0, 0.0), (t0 + 2, p + drift * 0.12, r0 * 1.4, 1.0), (t0 + 14, p + drift * 0.75, r0 * 2.2, 0.62),
                    (t0 + 34, p + drift * 1.5, r0 * 3.0, 0.26), (t0 + 58, p + drift * 2.1, r0 * 3.6, 0.0)])
    for k in range(9):                                                        # 조각을 따라 흘러내리는 먼지 줄기
        t0 = rnd.uniform(33, 60)
        p = at(t0, 1.6) + nrm * 0.9
        fall = v3(0, 0, -rnd.uniform(5.5, 8.5)) + nrm * rnd.uniform(0.3, 1.0) + side * rnd.uniform(-0.3, 0.3)
        r0 = rnd.uniform(0.35, 0.6)
        puff(dust, [(t0 - 1, p, r0, 0.0), (t0 + 3, p + fall * 0.12, r0 * 1.3, 0.9), (t0 + 20, p + fall * 0.9, r0 * 2.0, 0.55),
                    (t0 + 44, p + fall * 1.9, r0 * 2.6, 0.0)], stretch=(1, 1, 2.6))
    for f, pos, size in sorted(IMPACTS, key=lambda a: -a[2])[:14]:            # 큰 조각이 바닥에 닿을 때
        r0 = 0.5 + size
        up = v3(rnd.uniform(-0.4, 0.4), -rnd.uniform(0.3, 1.0), rnd.uniform(1.2, 2.2))
        puff(dust, [(f - 1, pos, r0, 0.0), (f + 2, pos + up * 0.2, r0 * 1.6, 1.0), (f + 16, pos + up * 1.0, r0 * 3.0, 0.5), (f + 40, pos + up * 1.8, r0 * 4.2, 0.0)])


# ============ C2: 내려앉는 건물에서 튀는 파편과 먼지 기둥 ============
def fx_c2():
    to_cam = Vector((cam.location.x, cam.location.y - 8, 0)).normalized()        # 건물에서 카메라 쪽 (수평)
    side = Vector((-to_cam.y, to_cam.x, 0))                                       # 화면에서 오른쪽
    center = Vector((0, 8, 0))
    # 생성 영상에서 잰 지붕 선: (프레임, 왼쪽 끝 x, y, 오른쪽 끝 x, y). 건물 바닥은 y=545
    ROOF = [(40, 430, 140, 690, 165), (56, 392, 160, 672, 176), (66, 368, 200, 648, 184), (76, 336, 240, 640, 216),
            (84, 328, 280, 616, 264), (90, 336, 320, 632, 312), (96, 320, 368, 688, 384), (104, 300, 430, 700, 430), (120, 290, 450, 710, 450)]
    rf = [r[0] for r in ROOF]

    def roof(f, u, down=0.0):
        """프레임 f의 지붕 선에서 u(0 왼쪽 ~ 1 오른쪽) 위치. down: 지붕에서 바닥 쪽으로 내려간 비율."""
        lx, ly, rx, ry = [float(np.interp(f, rf, [r[i] for r in ROOF])) for i in (1, 2, 3, 4)]
        x, y = lx + (rx - lx) * u, ly + (ry - ly) * u
        return on_plane(x, y + (545 - y) * down, center, to_cam)

    def sink(f):                                                                  # 건물이 내려가는 속도 (m/s)
        return -float(np.interp(f, [40, 66, 84, 96, 110], [1.0, 4.0, 8.0, 10.0, 2.0]))

    m_light = mat_rock("fx_concrete", (0.46, 0.41, 0.35))
    m_dark = mat_rock("fx_concrete_dark", (0.22, 0.19, 0.16))
    dust = mat_dust("fx_dust", (0.70, 0.60, 0.50), density=0.55, glow=0.42)
    for k in range(70):                                                           # 지붕과 벽에서 떨어져 나가는 조각
        t0 = rnd.uniform(48, 98)
        u = rnd.random()
        out = (-1 if u < 0.5 else 1) * rnd.uniform(1.5, 7.0) if rnd.random() < 0.8 else rnd.uniform(-3, 3)
        p = roof(t0, u, rnd.uniform(0.0, 0.35)) + to_cam * rnd.uniform(0.5, 5.0)
        v = side * out + to_cam * rnd.uniform(0.0, 4.0) + v3(0, 0, sink(t0) * 0.5 + rnd.uniform(0.0, 5.0))
        chunk(p, v, t0, min(2.2, 0.38 * math.exp(rnd.gauss(0.3, 0.6))), m_light if rnd.random() < 0.55 else m_dark, bury=5)
    for k in range(60):                                                           # 내려앉으며 터져 나가는 조각
        t0 = rnd.uniform(82, 100)
        u = rnd.random()
        out = (u - 0.5) * rnd.uniform(10, 26)
        p = roof(t0, u, rnd.uniform(0.1, 0.8)) + to_cam * rnd.uniform(1.0, 6.0)
        v = side * out + to_cam * rnd.uniform(0.0, 6.0) + v3(0, 0, rnd.uniform(3.0, 13.0))
        chunk(p, v, t0, min(1.8, 0.3 * math.exp(rnd.gauss(0.3, 0.6))), m_light if rnd.random() < 0.45 else m_dark, bury=4)
    for _ in range(150):                                                          # 부스러기
        t0 = rnd.uniform(52, 102)
        u = rnd.random()
        p = roof(t0, u, rnd.uniform(0.0, 0.7)) + to_cam * rnd.uniform(0.5, 6.0)
        v = side * (u - 0.5) * rnd.uniform(6, 30) + to_cam * rnd.uniform(0, 5) + v3(0, 0, rnd.uniform(-2.0, 14.0))
        chunk(p, v, t0, rnd.uniform(0.08, 0.2), m_dark, bury=3)
    for k in range(14):                                                           # 지붕에서 솟는 먼지 기둥
        t0 = rnd.uniform(66, 102)
        p = roof(t0, rnd.uniform(0.3, 0.75), 0.05) + to_cam * rnd.uniform(-2.0, 3.0)
        up = v3(rnd.uniform(-0.6, 0.6), rnd.uniform(-0.6, 0.6), rnd.uniform(5.0, 9.0)) + side * rnd.uniform(-0.5, 1.2)
        r0 = rnd.uniform(1.6, 2.6)
        puff(dust, [(t0 - 1, p, r0, 0.0), (t0 + 3, p + up * 0.15, r0 * 1.3, 0.9), (t0 + 16, p + up * 0.9, r0 * 2.0, 0.6),
                    (t0 + 34, p + up * 1.7, r0 * 2.7, 0.3), (t0 + 56, p + up * 2.3, r0 * 3.2, 0.0)])
    for k in range(0):      # 바닥으로 퍼지는 먼지는 뺌: 생성 영상에 이미 짙게 있고, 옆 건물에 가려 네모나게 잘려 보임
        t0 = rnd.uniform(60, 100)
        u = rnd.choice((rnd.uniform(-0.15, 0.2), rnd.uniform(0.8, 1.15)))
        p = roof(t0, u, 0.96) + to_cam * rnd.uniform(2.0, 8.0)
        p.z = 1.2
        go = side * (-1 if u < 0.5 else 1) * rnd.uniform(3.0, 7.0) + to_cam * rnd.uniform(1.0, 3.5) + v3(0, 0, rnd.uniform(0.3, 1.2))
        r0 = rnd.uniform(1.8, 2.8)
        puff(dust, [(t0 - 1, p, r0, 0.0), (t0 + 4, p + go * 0.2, r0 * 1.4, 0.85), (t0 + 20, p + go * 1.0, r0 * 2.2, 0.55),
                    (t0 + 44, p + go * 1.8, r0 * 3.0, 0.22), (t0 + 64, p + go * 2.2, r0 * 3.4, 0.0)], stretch=(1.25, 1.25, 0.75))


# ============ C4: 굴러오는 파편과 앞쪽 먼지 ============
def fx_c4():
    def wall(f):                                                                  # 먼지 벽 앞머리까지의 거리 (m). 생성 영상에서 벽 아랫선의 높이로 잼
        return float(np.interp(f, [0, 40, 70, 90, 100], [75.0, 52.0, 30.0, 9.0, 0.5]))

    cy = cam.location.y
    m_dark = mat_rock("fx_concrete_dark", (0.03, 0.025, 0.02))                    # 해를 마주 보는 컷이라 조각은 검게 보임
    m_mid = mat_rock("fx_concrete", (0.048, 0.039, 0.032))
    dust = mat_dust("fx_dust", (0.55, 0.40, 0.28), density=0.4, glow=0.13)      # 먼지 벽이 해를 가려 어두워지는 컷이라 먼지도 어둡게
    for k in range(95):                                                           # 길을 따라 튀어 오는 조각
        t0 = 24 + 68 * rnd.random() ** 0.7                                        # 벽이 가까워질수록 많이
        x = rnd.uniform(-6.5, 6.5)
        if abs(x) < 1.2:
            x = math.copysign(1.2 + rnd.random(), x)                              # 카메라 바로 옆으로는 지나가지 않게
        p = v3(x, cy + wall(t0) + rnd.uniform(0.0, 3.0), rnd.uniform(0.3, 3.0))
        v = v3(rnd.uniform(-1.5, 1.5) + x * 0.12, -rnd.uniform(17, 27), rnd.uniform(0.5, 6.5))
        chunk(p, v, t0, min(0.55, 0.24 * math.exp(rnd.gauss(0.2, 0.45))), m_dark if rnd.random() < 0.6 else m_mid, rest=0.42, fric=0.86)
    for k in range(30):                                                           # 벽보다 훨씬 빨리, 낮게 튀며 카메라 옆을 지나가는 조각
        t0 = rnd.uniform(26, 80)
        x = rnd.uniform(-5.5, 5.5)
        if abs(x) < 1.2:
            x = math.copysign(1.2 + rnd.random(), x)
        p = v3(x, cy + wall(t0) + rnd.uniform(0.0, 2.0), rnd.uniform(0.2, 0.9))
        v = v3(rnd.uniform(-1.0, 1.0) + x * 0.1, -rnd.uniform(32, 46), rnd.uniform(0.0, 3.0))
        chunk(p, v, t0, rnd.uniform(0.12, 0.3), m_dark if rnd.random() < 0.7 else m_mid, rest=0.5, fric=0.9)
    for _ in range(220):                                                          # 부스러기
        t0 = rnd.uniform(20, 94)
        x = rnd.uniform(-7, 7)
        p = v3(x, cy + wall(t0) + rnd.uniform(0.0, 4.0), rnd.uniform(0.1, 2.2))
        v = v3(rnd.uniform(-2, 2) + x * 0.1, -rnd.uniform(16, 30), rnd.uniform(0.0, 7.0))
        chunk(p, v, t0, rnd.uniform(0.05, 0.13), m_dark, rest=0.45, fric=0.88)
    for k in range(18):                                                           # 벽보다 먼저 바닥을 타고 오는 먼지
        t0 = rnd.uniform(16, 62)
        x = rnd.uniform(-7, 7)
        p = v3(x, cy + min(wall(t0), 45.0) * rnd.uniform(0.55, 0.95), rnd.uniform(0.25, 0.7))
        go = v3(x * 0.08 + rnd.uniform(-0.4, 0.4), -rnd.uniform(13, 22), rnd.uniform(0.0, 0.25))
        r0 = rnd.uniform(0.5, 0.95)
        puff(dust, [(t0 - 1, p, r0, 0.0), (t0 + 4, p + go * 0.17, r0 * 1.3, 0.75), (t0 + 20, p + go * 0.83, r0 * 1.9, 0.5),
                    (t0 + 44, p + go * 1.8, r0 * 2.5, 0.0)], stretch=(1.5, 2.6, 0.5))


{"C1": fx_c1, "C2": fx_c2, "C4": fx_c4}[CUT]()
print("FX_OBJECTS", len(C_FX.objects), "impacts", len(IMPACTS))

# ---------- 렌더 ----------
r = scene.render
r.engine = "BLENDER_EEVEE_NEXT"
r.resolution_x, r.resolution_y, r.resolution_percentage = 1280, 720, 100
r.fps = FPS
r.use_motion_blur = True
r.motion_blur_shutter = 0.5
r.image_settings.file_format = "PNG"
r.image_settings.color_depth = "8"
ee = scene.eevee
ee.taa_render_samples = 24
ee.volumetric_start, ee.volumetric_end = 0.5, 260.0
ee.volumetric_tile_size = "2"
ee.volumetric_samples = 128
ee.use_volumetric_shadows = True
fx_dir = Path(bpy.data.filepath).parent / "fx"
fx_dir.mkdir(exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath=str(fx_dir / f"collapse_{CUT}_fx.blend"), copy=True)
others = [ob for ob in scene.objects if ob.type == "MESH" and ob.name not in C_FX.objects]
# 효과 층 전용 보조 빛 (그림자 없음): 그늘 속 조각에도 면마다 밝기 차이가 나게. 위쪽 하늘빛 + 아래쪽 바닥 반사빛
fills = []
FILL = {"C1": 1.0, "C2": 0.8, "C4": 0.15}[CUT]
for name, direction, energy, color in (("fx_sky", (0.25, 0.55, -0.8), 1.3 * FILL, (0.85, 0.92, 1.0)), ("fx_bounce", (-0.1, 0.35, 0.93), 0.45 * FILL, (1.0, 0.82, 0.62))):
    data = bpy.data.lights.new(name, "SUN")
    data.energy, data.color, data.use_shadow = energy, color, False
    ob = bpy.data.objects.new(name, data)
    ob.rotation_euler = Vector(direction).to_track_quat("-Z", "Y").to_euler()
    C_FX.objects.link(ob)
    fills.append(ob)
frames = ONLY or list(range(F0, F1 + 1))
for name in ("layer", "view"):
    if PASSES not in (name, "both"):
        continue
    layer = name == "layer"
    r.film_transparent = layer
    r.image_settings.color_mode = "RGBA" if layer else "RGB"
    ee.taa_render_samples = 24 if layer else 12
    for ob in others:
        ob.is_holdout = layer
        if CUT == "C2" and ob.name.startswith("main_"):      # C2에서는 주인공 건물이 무너지므로 가리개로 쓰지 않음
            ob.hide_render = layer or ob.name == "main_upper_cracked"
    for ob in fills:
        ob.hide_render = not layer
    (OUT / name).mkdir(parents=True, exist_ok=True)
    for f in frames:
        scene.frame_set(f)
        r.filepath = str(OUT / name / f"{f:04d}.png")
        bpy.ops.render.render(write_still=True)
print("FX_RENDER_OK", CUT, len(frames))
