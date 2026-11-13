"""Checks for still images (keyframes, img2img results, mask fixes)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np

from ...models import Severity

BLANK_STD = 2.0
CLIP_FRACTION = 0.2


def analyze_image(path: Path, project_size: tuple[int, int], protection: dict[str, int] | None) -> tuple[list, dict[str, Any]]:
    from . import Finding

    image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return [Finding("decode", Severity.ERROR, "이미지를 읽을 수 없습니다")], {}
    h, w = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    std = float(gray.std())
    clipped = float(((gray <= 2) | (gray >= 253)).mean())
    findings = []
    if (w, h) != project_size:
        findings.append(Finding("spec", Severity.INFO,
                                f"작업 해상도 {w}x{h} (납품 규격 {project_size[0]}x{project_size[1]} 아님)"))
    if std < BLANK_STD:
        findings.append(Finding("blank", Severity.ERROR, f"거의 단색 이미지 (밝기 표준편차 {std:.1f})",
                                value=std, threshold=BLANK_STD))
    if clipped > CLIP_FRACTION:
        findings.append(Finding("clipping", Severity.WARNING, f"완전 검정/흰색 픽셀 {clipped:.0%}",
                                value=clipped, threshold=CLIP_FRACTION))
    if protection is not None:
        changed = protection["outside_changed"]
        if changed == 0:
            findings.append(Finding("protect", Severity.INFO,
                                    f"마스크 밖 변경 0px (보호 영역 {protection['outside_pixels']:,}px 원본 그대로)",
                                    value=0, threshold=0))
        else:
            findings.append(Finding("protect", Severity.ERROR,
                                    f"마스크 밖 {changed:,}px 변경됨 (원본 보호 실패)" if changed > 0
                                    else "원본 프레임과 크기가 달라 보호 검사를 할 수 없음",
                                    value=changed, threshold=0))
    measurements = {"width": w, "height": h, "frames_decoded": 1, "luma_std": round(std, 2),
                    "clipped_fraction": round(clipped, 4), "protection": protection,
                    "mean_bgr": [round(float(c), 1) for c in image.reshape(-1, 3).mean(axis=0)]}
    return findings, measurements
