"""GPU / RAM guard while a generation runs (thresholds carried over from the earlier pipeline)."""

from __future__ import annotations

import ctypes
import subprocess
import threading
from dataclasses import dataclass, field

TEMP_LIMIT_C = 83
VRAM_LIMIT_RATIO = 0.98
RAM_MIN_GIB = 4.0


class _MemoryStatus(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def available_ram_gib() -> float:
    status = _MemoryStatus()
    status.dwLength = ctypes.sizeof(_MemoryStatus)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
    return status.ullAvailPhys / 2**30


def sample() -> dict[str, float] | None:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu,memory.used,memory.total,utilization.gpu,name",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW,
        ).stdout.strip().splitlines()[0]
    except (OSError, subprocess.SubprocessError, IndexError):
        return None
    temp, used, total, util, name = [x.strip() for x in out.split(",", 4)]
    return {"temp_c": float(temp), "vram_used_mb": float(used), "vram_total_mb": float(total),
            "util_pct": float(util), "name": name, "ram_free_gib": round(available_ram_gib(), 2)}


@dataclass
class GpuGuard:
    interval_s: float = 1.0
    peak_temp_c: float = 0.0
    peak_vram_mb: float = 0.0
    min_ram_gib: float = 1e9
    tripped: str | None = None
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None

    def __enter__(self) -> GpuGuard:
        self._thread = threading.Thread(target=self._loop, name="gpu-guard", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_s):
            s = sample()
            if s is None:
                continue
            self.peak_temp_c = max(self.peak_temp_c, s["temp_c"])
            self.peak_vram_mb = max(self.peak_vram_mb, s["vram_used_mb"])
            self.min_ram_gib = min(self.min_ram_gib, s["ram_free_gib"])
            if s["temp_c"] >= TEMP_LIMIT_C:
                self.tripped = f"GPU 온도 {s['temp_c']:.0f}°C (기준 {TEMP_LIMIT_C}°C)"
            elif s["vram_used_mb"] > s["vram_total_mb"] * VRAM_LIMIT_RATIO:
                self.tripped = f"VRAM {s['vram_used_mb']:.0f}/{s['vram_total_mb']:.0f}MB (기준 98%)"
            elif s["ram_free_gib"] < RAM_MIN_GIB:
                self.tripped = f"가용 RAM {s['ram_free_gib']:.1f}GiB (기준 {RAM_MIN_GIB}GiB)"

    def abort_reason(self) -> str | None:
        return self.tripped
