"""ShotFlow <-> ComfyUI bridge helpers. Pure Python + numpy + Pillow (no torch), so they are testable outside ComfyUI."""

from __future__ import annotations

import io
import json
import os
import re
import urllib.error
import urllib.request
import uuid
from typing import Any

import numpy as np
from PIL import Image

SHOTFLOW_URL = os.environ.get("SHOTFLOW_URL", "http://127.0.0.1:8710").rstrip("/")
CATALOG_TIMEOUT_S = 1.5
REQUEST_TIMEOUT_S = 120
_ID = re.compile(r"#(\d+)\s*$")
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # local server: never go through a proxy


class ShotFlowError(RuntimeError):
    pass


def _get(path: str, timeout: float) -> bytes:
    try:
        with _opener.open(f"{SHOTFLOW_URL}{path}", timeout=timeout) as res:
            return res.read()
    except urllib.error.HTTPError as exc:
        raise ShotFlowError(f"ShotFlow {exc.code}: {exc.read().decode('utf-8', 'replace')[:300]}") from exc
    except OSError as exc:
        raise ShotFlowError(f"ShotFlow 서버({SHOTFLOW_URL})에 연결할 수 없습니다: {exc}") from exc


def catalog(kind: str) -> list[str]:
    """Labels for the node dropdowns ("RAIN ATLAS · R05 · v01 · #14"); empty when ShotFlow is not running."""
    try:
        items = json.loads(_get(f"/api/catalog/{kind}", CATALOG_TIMEOUT_S))
    except (ShotFlowError, ValueError):
        return []
    return [item["label"] for item in items]


def parse_id(value: str | int) -> int:
    """Accepts a dropdown label ending in "#<id>" or a bare number."""
    if isinstance(value, int):
        return value
    text = str(value).strip()
    match = _ID.search(text)
    if match:
        return int(match.group(1))
    if text.isdigit():
        return int(text)
    raise ShotFlowError(f"버전/샷 번호를 읽을 수 없습니다: {value!r}")


def fetch_frame(version_id: int, frame: int) -> np.ndarray:
    data = _get(f"/api/versions/{version_id}/frame/{frame}", REQUEST_TIMEOUT_S)
    return png_to_array(data)


def png_to_array(data: bytes) -> np.ndarray:
    """PNG bytes -> float32 HxWx3 in 0..1 (ComfyUI IMAGE layout without the batch axis)."""
    image = Image.open(io.BytesIO(data)).convert("RGB")
    return np.asarray(image, dtype=np.float32) / 255.0


def array_to_png(array: np.ndarray) -> bytes:
    pixels = np.clip(np.rint(np.asarray(array, dtype=np.float32) * 255.0), 0, 255).astype(np.uint8)
    buffer = io.BytesIO()
    Image.fromarray(pixels).save(buffer, format="PNG")
    return buffer.getvalue()


def _multipart(fields: dict[str, str], files: list[tuple[str, str, bytes]]) -> tuple[bytes, str]:
    boundary = f"----shotflow{uuid.uuid4().hex}"
    out = io.BytesIO()
    for name, value in fields.items():
        out.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n".encode())
        out.write(value.encode("utf-8"))
        out.write(b"\r\n")
    for field, filename, payload in files:
        out.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{filename}\"\r\n"
                  "Content-Type: image/png\r\n\r\n".encode())
        out.write(payload)
        out.write(b"\r\n")
    out.write(f"--{boundary}--\r\n".encode())
    return out.getvalue(), f"multipart/form-data; boundary={boundary}"


def publish(shot_id: int, images: list[np.ndarray], *, graph: dict[str, Any] | None, workflow: Any,
            parent_version_id: int | None, note: str, author: str = "ComfyUI") -> dict[str, Any]:
    if not images:
        raise ShotFlowError("등록할 이미지가 없습니다")
    fields = {"graph": json.dumps(graph or {}, ensure_ascii=False),
              "workflow": json.dumps(workflow, ensure_ascii=False) if workflow is not None else "",
              "note": note or "", "author": author or "ComfyUI",
              "parent_version_id": str(parent_version_id or 0)}
    files = [("files", f"publish_{i:03d}.png", array_to_png(img)) for i, img in enumerate(images)]
    body, content_type = _multipart(fields, files)
    request = urllib.request.Request(f"{SHOTFLOW_URL}/api/shots/{shot_id}/publish", data=body, method="POST",
                                     headers={"Content-Type": content_type})
    try:
        with _opener.open(request, timeout=REQUEST_TIMEOUT_S) as res:
            return json.loads(res.read())
    except urllib.error.HTTPError as exc:
        raise ShotFlowError(f"ShotFlow 등록 실패 {exc.code}: {exc.read().decode('utf-8', 'replace')[:300]}") from exc
    except OSError as exc:
        raise ShotFlowError(f"ShotFlow 서버({SHOTFLOW_URL})에 연결할 수 없습니다: {exc}") from exc
