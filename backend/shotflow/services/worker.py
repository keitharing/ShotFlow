"""Single background worker: one heavy job at a time (one 6GB GPU, one FFmpeg-heavy task)."""

from __future__ import annotations

import queue
import threading
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class Task:
    id: str
    kind: str
    target: str
    status: str = "queued"  # queued, running, done, failed
    result: Any = None
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "kind": self.kind, "target": self.target, "status": self.status,
            "result": self.result, "error": self.error,
            "created_at": self.created_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }


class Worker:
    def __init__(self) -> None:
        self._queue: queue.Queue[tuple[Task, Callable[[], Any]]] = queue.Queue()
        self._tasks: dict[str, Task] = {}
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._loop, name="shotflow-worker", daemon=True)
            self._thread.start()

    def submit(self, kind: str, target: str, fn: Callable[[], Any]) -> Task:
        task = Task(id=uuid.uuid4().hex[:12], kind=kind, target=target)
        with self._lock:
            self._tasks[task.id] = task
        self._queue.put((task, fn))
        self.start()
        return task

    def get(self, task_id: str) -> Task | None:
        with self._lock:
            return self._tasks.get(task_id)

    def recent(self, limit: int = 50) -> list[Task]:
        with self._lock:
            return sorted(self._tasks.values(), key=lambda t: t.created_at, reverse=True)[:limit]

    def wait_idle(self, timeout: float | None = None) -> None:
        self._queue.join() if timeout is None else _join_with_timeout(self._queue, timeout)

    def _loop(self) -> None:
        while True:
            task, fn = self._queue.get()
            task.status = "running"
            try:
                task.result = fn()
                task.status = "done"
            except Exception as exc:
                task.status = "failed"
                task.error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=3)}"
            finally:
                task.finished_at = datetime.now(timezone.utc)
                self._queue.task_done()


def _join_with_timeout(q: queue.Queue, timeout: float) -> None:
    import time

    deadline = time.monotonic() + timeout
    while q.unfinished_tasks and time.monotonic() < deadline:
        time.sleep(0.05)


worker = Worker()
