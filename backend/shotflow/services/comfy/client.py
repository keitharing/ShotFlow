"""Thin ComfyUI HTTP/WebSocket client (only the stock 0.39 API)."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import websocket


class ComfyError(RuntimeError):
    def __init__(self, message: str, detail: Any = None):
        super().__init__(message)
        self.detail = detail

    @property
    def out_of_memory(self) -> bool:
        text = f"{self} {json.dumps(self.detail, ensure_ascii=False, default=str)}".lower()
        return "out of memory" in text or "outofmemory" in text or "allocation on device" in text


class Interrupted(RuntimeError):
    pass


class ComfyClient:
    def __init__(self, base_url: str, timeout: float = 30):
        self.base = base_url.rstrip("/")
        self.http = httpx.Client(trust_env=False, timeout=timeout)
        self.client_id = uuid.uuid4().hex

    def close(self) -> None:
        self.http.close()

    def object_info(self) -> dict[str, Any]:
        return self.http.get(f"{self.base}/object_info", timeout=120).json()

    def system_stats(self) -> dict[str, Any]:
        return self.http.get(f"{self.base}/system_stats").json()

    def upload(self, path: Path, subfolder: str, kind: str = "input") -> str:
        """Upload into ComfyUI's input folder; returns the value LoadImage/LoadVideo expect."""
        with Path(path).open("rb") as handle:
            res = self.http.post(f"{self.base}/upload/image",
                                 files={"image": (Path(path).name, handle, "application/octet-stream")},
                                 data={"subfolder": subfolder, "type": kind, "overwrite": "true"})
        res.raise_for_status()
        body = res.json()
        return f"{body['subfolder']}/{body['name']}" if body.get("subfolder") else body["name"]

    def queue(self, graph: dict[str, Any]) -> str:
        res = self.http.post(f"{self.base}/prompt", json={"prompt": graph, "client_id": self.client_id})
        if res.status_code != 200:
            try:
                detail = res.json()
            except ValueError:
                detail = res.text
            raise ComfyError(f"ComfyUI rejected the workflow ({res.status_code})", detail)
        return res.json()["prompt_id"]

    def history(self, prompt_id: str) -> dict[str, Any] | None:
        return self.http.get(f"{self.base}/history/{prompt_id}").json().get(prompt_id)

    def view(self, filename: str, subfolder: str, kind: str = "output") -> bytes:
        res = self.http.get(f"{self.base}/view", params={"filename": filename, "subfolder": subfolder, "type": kind})
        res.raise_for_status()
        return res.content

    def interrupt(self) -> None:
        self.http.post(f"{self.base}/interrupt", json={})

    def free(self) -> None:
        self.http.post(f"{self.base}/free", json={"unload_models": True, "free_memory": True})

    def wait(self, prompt_id: str, on_progress: Callable[[float, str | None], None] | None = None,
             should_abort: Callable[[], str | None] | None = None, timeout: float = 1800) -> dict[str, Any]:
        """Follow progress over the WebSocket; fall back to polling history. Returns the history entry."""
        ws_url = self.base.replace("http://", "ws://") + f"/ws?clientId={self.client_id}"
        deadline = time.monotonic() + timeout
        ws = None
        try:
            ws = websocket.create_connection(ws_url, timeout=2, suppress_origin=True)
        except (OSError, websocket.WebSocketException):
            ws = None  # polling still works without progress detail
        node = None
        try:
            while time.monotonic() < deadline:
                reason = should_abort() if should_abort else None
                if reason:
                    self.interrupt()
                    raise Interrupted(reason)
                if ws is not None:
                    try:
                        raw = ws.recv()
                        if isinstance(raw, str):
                            msg = json.loads(raw)
                            data = msg.get("data", {})
                            if data.get("prompt_id") not in (None, prompt_id):
                                continue
                            if msg["type"] == "executing":
                                node = data.get("node")
                            elif msg["type"] == "progress" and on_progress and data.get("max"):
                                on_progress(data["value"] / data["max"], node)
                            elif msg["type"] == "execution_error":
                                raise ComfyError(data.get("exception_message", "execution error"), data)
                    except websocket.WebSocketTimeoutException:
                        pass
                else:
                    time.sleep(1)
                entry = self.history(prompt_id)
                if entry and entry.get("status", {}).get("completed") is not None:
                    status = entry["status"]
                    if status.get("status_str") == "error":
                        messages = [m for m in status.get("messages", []) if m[0] == "execution_error"]
                        detail = messages[-1][1] if messages else status
                        raise ComfyError(detail.get("exception_message", "execution error")
                                         if isinstance(detail, dict) else "execution error", detail)
                    if status.get("completed"):
                        return entry
            raise ComfyError(f"Timed out after {timeout:.0f}s")
        finally:
            if ws is not None:
                ws.close()
