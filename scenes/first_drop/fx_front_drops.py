"""영상 2 「FIRST DROP」 효과 장면: 과일 조각이 음료에 닿아 튈 때 카메라 쪽으로 날아오는 방울 (초점 밖이라 크게 흐려져 보임).

가까운 화면(A2와 그 뒤를 이어 만든 영상, 카메라 고정) 위에 얹는 층이다. 방울만 있는 투명 배경 장면을 저장하고, 렌더와 합성은 ShotFlow가 한다.
  ShotFlow "Blender 레이어" 작업(purpose=layer) → RGBA 프레임 → ShotFlow "레이어 합성" 작업(mode=over) → 새 버전

사용: blender -b --factory-startup --python fx_front_drops.py -- <저장할 .blend> [시드=7] [미리보기 폴더] [미리보기 프레임들 a,b,c]

정한 방법
- 방울은 유체 시뮬레이션이 아니라 포물선 운동을 프레임마다 키프레임으로 넣은 것이다 (영상 1의 파편과 같은 방법).
- 영상이 느린 화면이라 시간을 9배 늘려 잡았다: 중력 9.81 / 81 = 0.121 m/s² (영상의 1초 기준). 왕관이 8cm쯤 솟는 데 1초쯤 걸리는 것에 맞춘 값.
- 갑자기 생기거나 사라지는 방울이 없게, 궤적을 미리 계산해 조건에 맞는 것만 쓴다:
  ① 화면 안에서 탁자에 닿지 않는다(닿는 자리에 물결을 만들 수 없으므로) ② 한 번 화면 밖으로 나가면 다시 들어오지 않는다
  ③ 화면을 덮을 만큼 커지기 전에 화면 밖으로 나간다 ④ 층이 끝나기 전에 모두 화면 밖으로 나간다
- 방울이 처음 보이는 곳은 튀는 물막의 가장자리이고, 3프레임에 걸쳐 커지며 나온다.
"""
import json
import math
import random
import sys
from pathlib import Path

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
OUT = Path(argv[0])
SEED = int(argv[1]) if len(argv) > 1 else 7
PREVIEW = Path(argv[2]) if len(argv) > 2 else None
PREVIEW_FRAMES = [int(x) for x in argv[3].split(",")] if len(argv) > 3 else []
FPS, N_FRAMES, W, H = 24, 72, 1280, 720
G = 9.81 / 81.0                                   # 느린 화면(9배)에서의 중력
LENS, SENSOR_W = 50.0, 36.0
SENSOR_H = SENSOR_W * H / W
CAM = Vector((-0.13, -0.42, 0.045))               # build_scenes.py의 A2 카메라 자리(수평으로 +y를 봄)
IMPACT_V = 0.667                                  # 생성 영상에서 조각이 닿는 자리의 세로 위치(위에서 0 ~ 1). 가로는 가운데
DIST = CAM.z * LENS / ((IMPACT_V - 0.5) * SENSOR_H)        # 카메라에서 닿는 자리까지의 거리
ORIGIN = Vector((CAM.x, CAM.y + DIST, 0.0))
FSTOP = 4.0

for block in (bpy.data.objects, bpy.data.meshes, bpy.data.materials, bpy.data.lights, bpy.data.cameras):
    for item in list(block):
        block.remove(item)
scene = bpy.context.scene
scene.render.engine = "CYCLES"
scene.cycles.samples = 192
scene.cycles.use_denoising = True
scene.cycles.max_bounces = 8
scene.cycles.transmission_bounces = 8
scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = W, H, 100
scene.render.fps = FPS
scene.frame_start, scene.frame_end = 1, N_FRAMES
scene.render.film_transparent = True
scene.render.use_motion_blur = True
scene.render.motion_blur_shutter = 0.5
scene.view_settings.view_transform = "Standard"   # 정한 주황이 그대로 나오게 (AgX는 채도를 낮춤)
scene.view_settings.look = "None"

cam_data = bpy.data.cameras.new("SF_Camera_A2_fx")
cam_data.lens, cam_data.sensor_width = LENS, SENSOR_W
cam_data.clip_start, cam_data.clip_end = 0.01, 20
cam_data.dof.use_dof, cam_data.dof.focus_distance, cam_data.dof.aperture_fstop = True, DIST, FSTOP
cam = bpy.data.objects.new("SF_Camera_A2_fx", cam_data)
cam.location, cam.rotation_euler = CAM, (math.pi / 2, 0, 0)
scene.collection.objects.link(cam)
scene.camera = cam

world = bpy.data.worlds.new("fx_world")
world.use_nodes = True
world.node_tree.nodes["Background"].inputs[0].default_value = (0.55, 0.55, 0.57, 1)      # 방울 가장자리에 비치는 어두운 둘레
world.node_tree.nodes["Background"].inputs[1].default_value = 1.0
scene.world = world


def emitter(name, size, loc, rot, color, strength):
    """방울에 비치기만 하고 카메라에는 보이지 않는 빛나는 판."""
    bpy.ops.mesh.primitive_plane_add(size=1, location=loc, rotation=rot)
    obj = bpy.context.object
    obj.name, obj.scale = name, (size[0], size[1], 1)
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    nodes.remove(nodes["Principled BSDF"])
    emit = nodes.new("ShaderNodeEmission")
    emit.inputs[0].default_value, emit.inputs[1].default_value = (*color, 1), strength
    mat.node_tree.links.new(emit.outputs[0], nodes["Material Output"].inputs["Surface"])
    obj.data.materials.append(mat)
    obj.visible_camera = False
    return obj


emitter("fx_backdrop", (5.0, 3.0), (ORIGIN.x, ORIGIN.y + 1.4, 1.0), (math.pi / 2, 0, 0), (1.0, 1.0, 1.0), 1.9)      # 흰 배경: 방울 가운데가 밝게 비침
emitter("fx_table", (6.0, 6.0), (ORIGIN.x, ORIGIN.y - 0.5, -0.002), (0, 0, 0), (0.95, 0.80, 0.66), 0.55)            # 탁자와 고인 음료: 아래쪽이 따뜻하게
emitter("fx_key", (0.5, 0.3), (CAM.x - 0.5, CAM.y + 0.1, 0.9), (math.radians(35), 0, math.radians(-30)), (1.0, 1.0, 1.0), 14.0)   # 반짝이는 점

mat = bpy.data.materials.new("fx_soda_drop")
mat.use_nodes = True
bsdf = mat.node_tree.nodes["Principled BSDF"]
bsdf.inputs["Base Color"].default_value = (0.95, 0.35, 0.05, 1)        # 생성 영상의 음료 색(평균 sRGB 220 · 129 · 42)에 맞춘 값
bsdf.inputs["Roughness"].default_value = 0.02
bsdf.inputs["IOR"].default_value = 1.33
bsdf.inputs["Transmission Weight"].default_value = 1.0

rng = random.Random(SEED)


def ndc_radius(radius, depth):
    """화면 가로를 1로 본 방울의 겉보기 반지름(초점 밖 번짐 포함)."""
    if depth <= 0.01:
        return 9.0
    f, sw = LENS / 1000, SENSOR_W / 1000
    sharp = radius / depth * f / sw
    coc = 0.5 * (f * f / (FSTOP * (DIST - f))) * abs(depth - DIST) / depth / sw
    return sharp + coc


def simulate(p0, v, radius, f0):
    """프레임마다의 자리와 보이는지 여부. 조건에 맞지 않으면 None."""
    path, seen, gone, last_seen, biggest = [], False, False, None, 0.0
    for f in range(f0, N_FRAMES + 3):
        t = (f - f0) / FPS
        p = p0 + v * t + Vector((0, 0, -0.5 * G * t * t))
        ndc = world_to_camera_view(scene, cam, p)
        depth = ndc.z
        r = ndc_radius(radius, depth)
        visible = depth > 0.02 and -r < ndc.x < 1 + r and -r * W / H < ndc.y < 1 + r * W / H
        if visible:
            if gone:
                return None                       # ② 다시 들어옴
            if p.z <= radius:
                return None                       # ① 화면 안에서 탁자에 닿음
            if r > 0.11:
                return None                       # ③ 너무 커짐
            seen, last_seen, biggest = True, f, max(biggest, r)
        elif seen:
            gone = True
        path.append((f, p.copy()))
        if gone and len(path) > (last_seen - f0) + 3:
            break
    if not seen or not gone or last_seen > N_FRAMES - 3:
        return None                               # ④ 끝나기 전에 나가지 못함
    if last_seen - f0 < 14:
        return None                               # 너무 금방 지나감
    return path, last_seen, biggest


def make_drops(count, radius_range, speed_range, frames_range):
    made, tries = [], 0
    while len(made) < count and tries < 20000:
        tries += 1
        radius = rng.uniform(*radius_range)
        f0 = rng.randint(*frames_range)
        p0 = ORIGIN + Vector((rng.uniform(-0.03, 0.03), rng.uniform(-0.03, 0.0), rng.uniform(0.025, 0.07)))
        speed = rng.uniform(*speed_range)
        az = math.radians(rng.uniform(-32, 32))
        vz = rng.uniform(0.07, 0.22)
        v = Vector((math.sin(az) * speed, -math.cos(az) * speed, vz))
        result = simulate(p0, v, radius, f0)
        if result is None:
            continue
        path, last_seen, biggest = result
        made.append(dict(radius=radius, f0=f0, path=path, last_seen=last_seen, biggest=biggest, speed=speed, az=math.degrees(az), vz=vz))
    return made, tries


big, tries_big = make_drops(8, (0.0028, 0.0042), (0.17, 0.30), (3, 14))      # 속도는 영상의 왕관이 솟는 빠르기(0.08 m/s쯤)의 2 ~ 4배
fine, tries_fine = make_drops(16, (0.0009, 0.0017), (0.16, 0.32), (2, 18))
report = []
for index, drop in enumerate(big + fine):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, radius=drop["radius"])
    obj = bpy.context.object
    obj.name = f"fx_drop_{index:02d}"
    bpy.ops.object.shade_smooth()
    obj.data.materials.append(mat)
    f0, last = drop["f0"], drop["path"][-1][0]
    for f, p in drop["path"]:
        obj.location = p
        obj.keyframe_insert("location", frame=f)
        grow = min(1.0, 0.35 + 0.65 * (f - f0) / 3.0)          # 물막 가장자리에서 3프레임에 걸쳐 커지며 나옴
        obj.scale = (grow, grow, grow)
        obj.keyframe_insert("scale", frame=f)
    for f, hidden in ((max(1, f0 - 1), True), (f0, False), (last, False), (last + 1, True)):
        if f0 == 1 and f == 1 and hidden:
            continue
        obj.hide_render = hidden
        obj.keyframe_insert("hide_render", frame=f)
    for fc in obj.animation_data.action.fcurves:
        for kp in fc.keyframe_points:
            kp.interpolation = "CONSTANT" if fc.data_path == "hide_render" else "LINEAR"
    report.append(dict(name=obj.name, radius_mm=round(drop["radius"] * 1000, 2), first_frame=f0, last_seen_frame=drop["last_seen"],
                       speed=round(drop["speed"], 3), azimuth_deg=round(drop["az"], 1), up=round(drop["vz"], 3), biggest_ndc=round(drop["biggest"], 3)))

OUT.parent.mkdir(parents=True, exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath=str(OUT))
(OUT.with_suffix(".json")).write_text(json.dumps(dict(seed=SEED, frames=N_FRAMES, gravity=G, camera=list(CAM), origin=list(ORIGIN), focus_m=DIST, fstop=FSTOP,
                                                    tries=dict(big=tries_big, fine=tries_fine), drops=report), ensure_ascii=False, indent=1), encoding="utf-8")
print("SF_FX saved", OUT, "| big", len(big), "of", tries_big, "tries | fine", len(fine), "of", tries_fine, "tries | origin", [round(c, 3) for c in ORIGIN], "focus", round(DIST, 3))
for d in report:
    print("  ", d)
if PREVIEW:
    PREVIEW.mkdir(parents=True, exist_ok=True)
    scene.cycles.samples = 64
    prefs = bpy.context.preferences.addons["cycles"].preferences
    for kind in ("OPTIX", "CUDA"):
        try:
            prefs.compute_device_type = kind
            prefs.get_devices()
            if any(dev.type == kind for dev in prefs.devices):
                for dev in prefs.devices:
                    dev.use = dev.type == kind
                scene.cycles.device = "GPU"
                break
        except Exception:
            pass
    scene.render.image_settings.file_format, scene.render.image_settings.color_mode = "PNG", "RGBA"
    for f in PREVIEW_FRAMES:
        scene.frame_set(f)
        scene.render.filepath = str(PREVIEW / f"fx_test_{f:04d}.png")
        bpy.ops.render.render(write_still=True)
