"""영상 2 「FIRST DROP」 효과 장면: 끝 장면의 병 위로 한 번 스치는 빛.

액체는 건드리지 않고 빛만 더한다. 병 모양의 검은 물체에 움직이는 긴 조명이 비친 것만 렌더해서, ShotFlow가 '빛 더하기'(screen) 방식으로 생성 영상 위에 얹는다.
  ShotFlow "Blender 레이어" 작업(purpose=layer) → RGBA 프레임 → ShotFlow "레이어 합성" 작업(mode=screen) → 새 버전

사용: blender -b --factory-startup --python fx_glint.py -- <저장할 .blend> <추적 JSON> [미리보기 폴더] [미리보기 프레임들 a,b,c]

정한 방법
- 병: 생성 영상의 첫 프레임에서 잰 옆모습(줄마다의 반지름)을 돌려 만든 회전체. 1픽셀 = 0.001. 그래서 윤곽이 영상의 병과 맞는다.
- 카메라: 원근 없는 카메라(직교). 생성 영상에서 카메라가 천천히 다가가므로, 병 위의 점들을 추적해 구한 배율과 이동(glint_measure.py)을
  프레임마다 카메라의 배율과 자리로 넣었다. 추적값은 2차 곡선으로 고르게 해서 떨림을 없앴다.
- 빛: 병의 축을 중심으로 왼쪽에서 오른쪽으로 도는 긴 조명 판(카메라에는 보이지 않음). 둥근 유리에 비쳐 세로 줄무늬가 병을 가로질러 지나간다.
  들어올 때와 나갈 때는 세기를 0에서 올리고 0으로 내린다(갑자기 켜지거나 꺼지지 않게).
"""
import json
import math
import sys
from pathlib import Path

import bmesh
import bpy
import numpy as np

argv = sys.argv[sys.argv.index("--") + 1:]
OUT, TRACK = Path(argv[0]), json.loads(Path(argv[1]).read_text(encoding="utf-8"))
PREVIEW = Path(argv[2]) if len(argv) > 2 else None
PREVIEW_FRAMES = [int(x) for x in argv[3].split(",")] if len(argv) > 3 else []
W, H, N = TRACK["W"], TRACK["H"], TRACK["frames"]
PX = 0.001
AXIS_X = 652.0                                    # 병의 가운데 세로줄 (몸통의 왼쪽 566 · 오른쪽 739의 가운데)
# 첫 프레임에서 잰 병의 옆모습: (세로 픽셀, 반지름 픽셀). 투명한 목은 가장자리가 잘 안 보여 뚜껑 너비에서 어림함
PROFILE = [(51, 0), (53, 33), (57, 39), (110, 39.5), (114, 37), (188, 37.5), (197, 48), (207, 57), (225, 62), (243, 70.5), (255, 77.5),
           (267, 81.5), (279, 84.5), (291, 86), (600, 86.5), (609, 83), (615, 74), (617, 0)]
SWEEP = (34, 70)                                  # 빛이 지나가는 프레임(끝 장면의 1.4 ~ 2.9초)
ANGLE = 115.0                                     # 조명이 도는 각도: -115° → +115°
STRENGTH = 1.9

for block in (bpy.data.objects, bpy.data.meshes, bpy.data.materials, bpy.data.lights, bpy.data.cameras):
    for item in list(block):
        block.remove(item)
scene = bpy.context.scene
scene.render.engine = "CYCLES"
scene.cycles.samples = 96
scene.cycles.use_denoising = True
scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = W, H, 100
scene.render.fps = 24
scene.frame_start, scene.frame_end = 1, N
scene.render.film_transparent = True
scene.render.use_motion_blur = True
scene.render.motion_blur_shutter = 0.5
scene.view_settings.view_transform = "Standard"
scene.view_settings.look = "None"
world = bpy.data.worlds.new("glint_world")
world.use_nodes = True
world.node_tree.nodes["Background"].inputs[0].default_value = (0, 0, 0, 1)        # 조명 판 말고는 아무 빛도 없음
scene.world = world


def gloss(name, grey, roughness):
    """빛이 비친 자리만 밝게 나오는 검은 바탕의 반사면."""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    b = mat.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (grey, grey, grey, 1)
    b.inputs["Metallic"].default_value = 1.0
    b.inputs["Roughness"].default_value = roughness
    return mat


M_CAP, M_GLASS, M_LABEL = gloss("glint_cap", 0.35, 0.42), gloss("glint_glass", 0.55, 0.30), gloss("glint_label", 0.05, 0.62)
LABEL_ROWS = (285, 400)                           # 라벨이 있는 줄 (글자가 빛에 씻겨 흐려지지 않게 거의 비치지 않게 함)


def part_of(y_px):
    if y_px < 112:
        return 0
    return 2 if LABEL_ROWS[0] <= y_px <= LABEL_ROWS[1] else 1


# 옆모습을 잘게 나눠 회전체를 만든다
ys = np.arange(PROFILE[0][0], PROFILE[-1][0] + 0.01, 1.0)
rs = np.interp(ys, [p[0] for p in PROFILE], [p[1] for p in PROFILE])
SEG = 96
mesh = bpy.data.meshes.new("glint_bottle")
bm = bmesh.new()
rings = []
for y_px, r_px in zip(ys, rs):
    z = (H / 2 - y_px) * PX
    rings.append([bm.verts.new((max(r_px, 0.05) * PX * math.cos(2 * math.pi * k / SEG), max(r_px, 0.05) * PX * math.sin(2 * math.pi * k / SEG), z)) for k in range(SEG)])
for i in range(len(rings) - 1):
    idx = part_of((ys[i] + ys[i + 1]) / 2)
    for k in range(SEG):
        face = bm.faces.new((rings[i][k], rings[i][(k + 1) % SEG], rings[i + 1][(k + 1) % SEG], rings[i + 1][k]))
        face.material_index, face.smooth = idx, True
bm.normal_update()
bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
bm.to_mesh(mesh)
bm.free()
bottle = bpy.data.objects.new("glint_bottle", mesh)
bottle.location = ((AXIS_X - W / 2) * PX, 0, 0)
for m in (M_CAP, M_GLASS, M_LABEL):
    mesh.materials.append(m)
scene.collection.objects.link(bottle)

# 카메라: 프레임마다 배율 s, 이동 (tx, ty)  →  직교 카메라의 배율과 자리
fr = np.arange(N)
fit = {k: np.polyval(np.polyfit(fr, [t[k] for t in TRACK["track"]], 2), fr) for k in ("s", "tx", "ty")}
resid = {k: float(np.abs(fit[k] - np.array([t[k] for t in TRACK["track"]])).max()) for k in fit}
cam_data = bpy.data.cameras.new("SF_Camera_A3_glint")
cam_data.type = "ORTHO"
cam_data.clip_start, cam_data.clip_end = 0.1, 20
cam = bpy.data.objects.new("SF_Camera_A3_glint", cam_data)
cam.rotation_euler = (math.pi / 2, 0, 0)
scene.collection.objects.link(cam)
scene.camera = cam
for i in range(N):
    s, tx, ty = float(fit["s"][i]), float(fit["tx"][i]), float(fit["ty"][i])
    cam_data.ortho_scale = W * PX / s
    cam.location = ((W / 2 * (1 - s) - tx) * PX / s, -3.0, (ty - H / 2 * (1 - s)) * PX / s)
    cam_data.keyframe_insert("ortho_scale", frame=i + 1)
    cam.keyframe_insert("location", frame=i + 1)

# 조명 판: 병의 축을 중심으로 도는 빈 물체에 매달아 돌린다
pivot = bpy.data.objects.new("glint_pivot", None)
pivot.location = bottle.location
scene.collection.objects.link(pivot)
bpy.ops.mesh.primitive_plane_add(size=1)
strip = bpy.context.object
strip.name = "glint_strip"
strip.scale = (0.16, 1.3, 1)
strip.rotation_euler = (math.pi / 2, math.radians(6), 0)        # 세로로 세우고 살짝 기울임
strip.location = (0, -0.9, 0.03)
strip.parent = pivot
strip.visible_camera = False
mat = bpy.data.materials.new("glint_light")
mat.use_nodes = True
nodes = mat.node_tree.nodes
nodes.remove(nodes["Principled BSDF"])
emit = nodes.new("ShaderNodeEmission")
emit.inputs[0].default_value = (1.0, 0.96, 0.88, 1)
mat.node_tree.links.new(emit.outputs[0], nodes["Material Output"].inputs["Surface"])
strip.data.materials.append(mat)
a, b = SWEEP
for f in range(a - 2, b + 3):
    u = min(1.0, max(0.0, (f - a) / (b - a)))
    ease = u * u * (3 - 2 * u)                                  # 천천히 들어와 천천히 나감
    pivot.rotation_euler = (0, 0, math.radians(-ANGLE + 2 * ANGLE * ease))
    pivot.keyframe_insert("rotation_euler", frame=f)
    emit.inputs[1].default_value = STRENGTH * math.sin(math.pi * u) ** 0.8 if 0 < u < 1 else 0.0
    emit.inputs[1].keyframe_insert("default_value", frame=f)
for holder in (pivot.animation_data, mat.node_tree.animation_data, cam.animation_data, cam_data.animation_data):
    for fc in holder.action.fcurves:
        for kp in fc.keyframe_points:
            kp.interpolation = "LINEAR"

OUT.parent.mkdir(parents=True, exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath=str(OUT))
OUT.with_suffix(".json").write_text(json.dumps(dict(frames=N, sweep=SWEEP, angle=ANGLE, strength=STRENGTH, axis_x=AXIS_X, profile=PROFILE,
                                                   scale_first_last=[float(fit["s"][0]), float(fit["s"][-1])], fit_max_error=resid), ensure_ascii=False, indent=1), encoding="utf-8")
print("SF_FX saved", OUT, "| scale", round(float(fit["s"][0]), 4), "→", round(float(fit["s"][-1]), 4), "| fit error (s, tx, ty)", {k: round(v, 3) for k, v in resid.items()})
if PREVIEW:
    PREVIEW.mkdir(parents=True, exist_ok=True)
    scene.cycles.samples = 48
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
        scene.render.filepath = str(PREVIEW / f"glint_test_{f:04d}.png")
        bpy.ops.render.render(write_still=True)
