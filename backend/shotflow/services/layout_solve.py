"""Camera and proxy placement from 2D marks on a storyboard drawing (decision D-015).

The artist marks, on one drawing:
  - the horizon line (where the camera's eye level is; 0.5 = a level camera),
  - a box around each object, the bottom edge where it touches the ground,
  - the lens.
With a flat ground and one object of known height, that is enough for a pinhole camera:
  pitch  = atan((cy - horizon) / f)
  ground point of a pixel = camera + h * ray / -ray.z          (ray through that pixel)
  object height = h * (1 + g.y * top.z / top.y)                 (g: ground point for h = 1, top: ray to the box top)
so a known height gives the camera height h, and h then places every other object.

All coordinates: metres, Blender axes (Z up), camera looking along +Y before the pitch. The image is the drawing
area only; marks are fractions of its width (x) and height (y), y downwards. Sensor width 36 mm, fitted horizontally.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import median
from typing import Any

SENSOR_MM = 36.0

# kind -> (name shown to the artist, height used when none is given (m), depth / width)
KINDS: dict[str, tuple[str, float | None, float]] = {
    "bottle": ("병", 0.25, 1.0),
    "person": ("사람", 1.7, 0.45),
    "box": ("상자", None, 1.0),
    "round": ("둥근 물체", None, 1.0),
    "building": ("건물", None, 0.8),
    "other": ("기타", None, 1.0),
}


class LayoutError(ValueError):
    """Marks that cannot describe a camera (shown to the artist as is)."""


@dataclass
class _Cam:
    f: float
    cx: float
    cy: float
    pitch: float  # radians, positive = looking down

    def ray(self, x: float, y: float) -> tuple[float, float, float]:
        """Direction through pixel (x, y): f * forward + (x - cx) * right - (y - cy) * up."""
        s, c = math.sin(self.pitch), math.cos(self.pitch)
        dx, dy = x - self.cx, y - self.cy
        return (dx, self.f * c - dy * s, -self.f * s - dy * c)

    def ground(self, x: float, y: float) -> tuple[float, float]:
        """Ground point (x, y) for a camera 1 m above the ground; scale by the real camera height."""
        rx, ry, rz = self.ray(x, y)
        if rz >= -1e-9:
            raise LayoutError("물체의 바닥이 수평선과 같거나 위에 있습니다. 수평선을 올리거나 상자 아래 끝을 바닥 닿는 곳에 맞추세요.")
        return rx / -rz, ry / -rz

    def height_factor(self, x: float, y_top: float, ground_y: float) -> float:
        """Object height / camera height for an object standing at ground_y (h = 1) whose top is at y_top."""
        _, ty, tz = self.ray(x, y_top)
        if ty <= 1e-9:
            raise LayoutError("상자 위 끝이 카메라 뒤쪽을 가리킵니다. 렌즈나 수평선을 확인하세요.")
        return 1.0 + ground_y * tz / ty


def _px(box: list[float], w: float, h: float) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = box
    x0, x1 = sorted((float(x0) * w, float(x1) * w))
    y0, y1 = sorted((float(y0) * h, float(y1) * h))
    if x1 - x0 < 1 or y1 - y0 < 1:
        raise LayoutError("너무 작은 상자가 있습니다. 물체를 감싸게 다시 그리세요.")
    return x0, y0, x1, y1


def solve_frame(marks: dict[str, Any], *, aspect: float, lens_mm: float, anchor_label: str | None = None) -> dict[str, Any]:
    """One drawing -> camera (height, pitch, location, rotation) and proxies. aspect = drawing width / height."""
    if not 8 <= lens_mm <= 300:
        raise LayoutError("렌즈는 8~300mm 사이로 정하세요")
    objects = marks.get("objects") or []
    if not objects:
        raise LayoutError("물체 상자를 하나 이상 그리세요")
    w, h = 1000.0 * aspect, 1000.0
    cam = _Cam(f=lens_mm / SENSOR_MM * w, cx=w / 2, cy=h / 2,
               pitch=math.atan2(h / 2 - float(marks.get("horizon", 0.5)) * h, lens_mm / SENSOR_MM * w))
    warnings: list[str] = []

    rows = []
    for i, ob in enumerate(objects):
        kind = ob.get("kind") if ob.get("kind") in KINDS else "other"
        x0, y0, x1, y1 = _px(ob["box"], w, h)
        row = {"id": ob.get("id", i), "label": (ob.get("label") or KINDS[kind][0]).strip()[:40], "kind": kind,
               "grounded": ob.get("grounded", True) is not False, "mask": bool(ob.get("mask")),
               "anchor": bool(ob.get("anchor")), "box": (x0, y0, x1, y1)}
        given = ob.get("height_m")
        row["height_given"] = float(given) if given not in (None, "") and float(given) > 0 else KINDS[kind][1]
        if row["grounded"]:
            gx, gy = cam.ground((x0 + x1) / 2, y1)
            row["g"] = (gx, gy)
            row["k"] = cam.height_factor((x0 + x1) / 2, y0, gy)
            if row["k"] <= 0:
                raise LayoutError(f"'{row['label']}' 상자의 위아래로는 높이를 잴 수 없습니다. 수평선 위치를 확인하세요.")
        rows.append(row)

    grounded = [r for r in rows if r["grounded"]]
    if not grounded:
        raise LayoutError("바닥에 닿은 물체가 하나는 있어야 합니다 (모두 '떠 있음'이면 거리를 알 수 없음)")
    anchor = next((r for r in rows if anchor_label and r["label"] == anchor_label and r["grounded"]), None) \
        or next((r for r in grounded if r["anchor"]), None) or grounded[0]

    # Camera height: the anchor's real height if known, otherwise every object of known height votes.
    if anchor["height_given"]:
        cam_h = anchor["height_given"] / anchor["k"]
    else:
        votes = [r["height_given"] / r["k"] for r in grounded if r["height_given"]]
        if votes:
            cam_h = median(votes)
        else:
            cam_h = 1.6
            warnings.append("높이를 아는 물체가 없어 카메라 높이를 1.6m로 두었습니다. 물체 하나에 실제 높이를 적으세요.")

    ax, ay = cam_h * anchor["g"][0], cam_h * anchor["g"][1]
    out = []
    for r in rows:
        x0, y0, x1, y1 = r["box"]
        depth_ratio = KINDS[r["kind"]][2]
        if r["grounded"]:
            gx, gy = cam_h * r["g"][0], cam_h * r["g"][1]
            left = cam.ground(x0, y1)
            right = cam.ground(x1, y1)
            width = abs(right[0] - left[0]) * cam_h
            height = cam_h * r["k"]
            if r["height_given"] and abs(height - r["height_given"]) > 0.25 * r["height_given"] and r is not anchor:
                warnings.append(f"'{r['label']}': 그림 비율로는 {height:.2f}m인데 {r['height_given']:.2f}m로 적었습니다. "
                                f"수평선이나 상자를 확인하세요 (그림 비율대로 둠).")
            z = 0.0
        else:
            # Floating (a drop, a falling slice): put it at the anchor's distance along the view.
            rx, ry, rz = cam.ray((x0 + x1) / 2, (y0 + y1) / 2)
            s = ay / ry if ry > 1e-9 else 1.0
            gx, gy, zc = s * rx, ay, cam_h + s * rz
            width = (x1 - x0) * s
            height = (y1 - y0) * s
            z = zc - height / 2
        out.append({"id": r["id"], "label": r["label"], "kind": r["kind"], "grounded": r["grounded"],
                    "mask": r["mask"] or r is anchor, "anchor": r is anchor,
                    "location": [round(gx - ax, 4), round(gy - ay, 4), round(z, 4)],
                    "size": [round(width, 4), round(width * depth_ratio, 4), round(height, 4)]})

    pitch_deg = math.degrees(cam.pitch)
    camera = {"height_m": round(cam_h, 4), "pitch_deg": round(pitch_deg, 3), "lens_mm": lens_mm,
              "distance_m": round(math.hypot(ax, ay), 4),
              "location": [round(-ax, 4), round(-ay, 4), round(cam_h, 4)],
              "rotation_deg": [round(90.0 - pitch_deg, 3), 0.0, 0.0]}
    if abs(pitch_deg) > 60:
        warnings.append(f"카메라가 {pitch_deg:.0f}° 기울어 있습니다. 수평선 위치가 맞는지 확인하세요.")
    return {"camera": camera, "objects": out, "anchor": anchor["label"], "warnings": warnings}


def solve_layout(marks: dict[str, Any], *, aspect: float) -> dict[str, Any]:
    """Start drawing (and an optional end drawing) of one cut -> cameras keyed at the cut's first and last frame.

    The proxies come from the start drawing; the end drawing only moves the camera, measured from the same anchor.
    """
    lens = float(marks.get("lens_mm") or 50)
    frames = {f.get("role"): f for f in marks.get("frames") or []}
    if "start" not in frames:
        raise LayoutError("컷 처음 그림에 표시가 있어야 합니다")
    start = solve_frame(frames["start"], aspect=aspect, lens_mm=lens)
    cameras = [{"role": "start", **start["camera"]}]
    warnings = list(start["warnings"])
    if frames.get("end") and (frames["end"].get("objects") or []):
        end = solve_frame(frames["end"], aspect=aspect, lens_mm=float(frames["end"].get("lens_mm") or lens),
                          anchor_label=start["anchor"])
        if end["anchor"] != start["anchor"]:
            warnings.append(f"끝 그림에 기준 물체 '{start['anchor']}'가 없어 '{end['anchor']}'를 기준으로 맞췄습니다.")
        cameras.append({"role": "end", **end["camera"]})
        warnings += [f"끝 그림: {w}" for w in end["warnings"]]
    return {"aspect": round(aspect, 5), "lens_mm": lens, "cameras": cameras, "objects": start["objects"],
            "anchor": start["anchor"], "warnings": warnings}
