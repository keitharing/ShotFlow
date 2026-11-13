"""ShotFlow's own ComfyUI process (decision D-003).

Same portable install, models and safety flags as E:\\AI_제작환경\\manage_comfy.py, but input/output/user/temp
folders live under ShotFlow/data/comfy so generated files never land in the user's record folders.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx

from ...config import Settings, load_settings, writable_path

COMFY_ROOT = Path(os.environ.get("SHOTFLOW_COMFY_ROOT", r"E:\AI_제작환경\ComfyUI_windows_portable"))
PYTHON = COMFY_ROOT / "python_embeded" / "python.exe"
APP = COMFY_ROOT / "ComfyUI"
PORTS = range(8190, 8200)
READY_TIMEOUT_S = 180


def comfy_dir(settings: Settings, name: str) -> Path:
    path = writable_path(settings.data_dir / "comfy" / name / ".keep", settings).parent
    return path


def _state_file(settings: Settings) -> Path:
    return settings.data_dir / "comfy" / "state.json"


ALLOWED_EXTRA_ARGS = {"--fp32-unet", "--fp16-unet", "--fp32-vae", "--fp16-vae", "--force-fp32", "--lowvram",
                      "--normalvram", "--fp32-text-enc"}


NODES_DIR = Path(__file__).resolve().parents[4] / "integrations" / "comfyui"
NODE_PACKAGE = "shotflow_nodes"


MODEL_FOLDERS = ("checkpoints", "controlnet", "vae")  # models approved for ShotFlow (D-010)
MODELS_DIR = Path(os.environ.get("SHOTFLOW_MODELS", Path(__file__).resolve().parents[4] / "data" / "models"))


def models_dir(settings: Settings | None = None) -> Path:
    """One model folder per ShotFlow install (not per data folder): the files are gigabytes."""
    return MODELS_DIR


def model_path(folder: str, file: str, settings: Settings | None = None) -> Path:
    """ShotFlow's own model folder first, then the portable ComfyUI one (which ShotFlow never writes to)."""
    own = models_dir(settings) / folder / file
    return own if own.is_file() else APP / "models" / folder / file


def _node_args(settings: Settings) -> list[str]:
    """Load only ShotFlow's own node package from the ShotFlow folder (D-007); nothing goes into ComfyUI."""
    if not (NODES_DIR / NODE_PACKAGE / "__init__.py").is_file():
        return []
    config = writable_path(settings.data_dir / "comfy" / "extra_paths.yaml", settings)
    models = "".join(f"  {name}: {name}\n" for name in MODEL_FOLDERS)
    config.write_text(f'shotflow:\n  custom_nodes: "{NODES_DIR.as_posix()}"\n'
                      f'shotflow_models:\n  base_path: "{models_dir(settings).as_posix()}"\n{models}', encoding="utf-8")
    return ["--extra-model-paths-config", str(config), "--whitelist-custom-nodes", NODE_PACKAGE]


def launch_options(settings: Settings) -> dict[str, Any]:
    """Precision/VRAM flags for ShotFlow's ComfyUI (data/comfy/launch_options.json); only known flags pass."""
    path = settings.data_dir / "comfy" / "launch_options.json"
    options = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    extra = [a for a in options.get("extra_args", []) if a in ALLOWED_EXTRA_ARGS]
    return {"extra_args": extra, "note": options.get("note")}


def _process_identity(pid: int) -> dict[str, Any] | None:
    """Executable path + creation time, so a reused PID is never mistaken for our ComfyUI."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    kernel.OpenProcess.restype = wt.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
    kernel.GetProcessTimes.argtypes = [wt.HANDLE] + [ctypes.POINTER(wt.FILETIME)] * 4
    kernel.CloseHandle.argtypes = [wt.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        buf, size = ctypes.create_unicode_buffer(32768), wt.DWORD(32768)
        times = [wt.FILETIME() for _ in range(4)]
        if not kernel.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return None
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return None
        return {"exe": str(Path(buf.value).resolve()),
                "creation": (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime}
    finally:
        kernel.CloseHandle(handle)


def _owned_state(settings: Settings) -> dict[str, Any] | None:
    path = _state_file(settings)
    if not path.is_file():
        return None
    state = json.loads(path.read_text(encoding="utf-8"))
    identity = _process_identity(state["pid"])
    if identity and identity == state["identity"] and Path(identity["exe"]) == PYTHON.resolve():
        return state
    return None


def _ping(url: str) -> dict[str, Any] | None:
    try:
        with httpx.Client(trust_env=False, timeout=3) as client:
            return client.get(f"{url}/system_stats").json()
    except (httpx.HTTPError, ValueError):
        return None


def status(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or load_settings()
    state = _owned_state(settings)
    if not state:
        return {"running": False, "installed": PYTHON.is_file() and (APP / "main.py").is_file()}
    stats = _ping(state["url"])
    flags = [a for a in state["command"] if a in ALLOWED_EXTRA_ARGS]
    return {"running": stats is not None, "starting": stats is None, "url": state["url"], "pid": state["pid"],
            "started_at": state["created_at"], "log": state["log"], "extra_args": flags,
            "version": (stats or {}).get("system", {}).get("comfyui_version")}


def start(settings: Settings | None = None, wait: bool = True) -> dict[str, Any]:
    settings = settings or load_settings()
    current = _owned_state(settings)
    if current:
        if wait:
            _wait_ready(current["url"], None)
        return status(settings)
    if not PYTHON.is_file() or not (APP / "main.py").is_file():
        raise RuntimeError(f"ComfyUI portable not found at {COMFY_ROOT}")
    port = next((p for p in PORTS if _port_free(p)), None)
    if port is None:
        raise RuntimeError("No free local port 8190-8199")

    temp = comfy_dir(settings, "temp")
    env = os.environ.copy()
    for key, sub in {"TEMP": "", "TMP": "", "HF_HOME": "huggingface", "TORCH_HOME": "torch",
                     "XDG_CACHE_HOME": "cache", "CUDA_CACHE_PATH": "cuda"}.items():
        target = temp / sub if sub else temp
        target.mkdir(parents=True, exist_ok=True)
        env[key] = str(target)
    env.update({"HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
                "SHOTFLOW_URL": f"http://{settings.host}:{settings.port}"})
    command = [str(PYTHON), "-s", str(APP / "main.py"), "--windows-standalone-build", "--listen", "127.0.0.1",
               "--port", str(port), "--disable-auto-launch", "--offline", "--disable-all-custom-nodes",
               *_node_args(settings), *launch_options(settings)["extra_args"]]
    for name in ("input", "output", "user", "temp"):
        command += [f"--{name}-directory", str(comfy_dir(settings, name))]
    stamp = time.strftime("%Y%m%d_%H%M%S")
    log_path = writable_path(settings.data_dir / "comfy" / "logs" / f"comfy_{stamp}.log", settings)
    with log_path.open("wb") as log:
        process = subprocess.Popen(command, cwd=APP, env=env, stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
    state = {"pid": process.pid, "identity": _process_identity(process.pid), "port": port,
             "url": f"http://127.0.0.1:{port}", "log": str(log_path), "command": command,
             "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    writable_path(_state_file(settings), settings).write_text(json.dumps(state, ensure_ascii=False, indent=2),
                                                              encoding="utf-8")
    if wait:
        _wait_ready(state["url"], process)
    return status(settings)


def stop(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or load_settings()
    state = _owned_state(settings)
    if not state:
        return {"stopped": False, "reason": "ShotFlow가 띄운 ComfyUI가 없습니다"}
    if _process_identity(state["pid"]) != state["identity"]:
        raise RuntimeError("Process identity changed; refusing to stop it")
    subprocess.run([str(Path(os.environ["SystemRoot"]) / "System32" / "taskkill.exe"), "/PID", str(state["pid"]), "/F"],
                   check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    return {"stopped": True, "pid": state["pid"]}


def url(settings: Settings | None = None) -> str:
    state = _owned_state(settings or load_settings())
    if not state:
        raise RuntimeError("ComfyUI is not running (start it from ShotFlow first)")
    return state["url"]


def _port_free(port: int) -> bool:
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _wait_ready(base: str, process: subprocess.Popen | None) -> None:
    deadline = time.monotonic() + READY_TIMEOUT_S
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            raise RuntimeError(f"ComfyUI exited with code {process.returncode}; see the log in data/comfy/logs")
        if _ping(base):
            return
        time.sleep(1)
    raise RuntimeError(f"ComfyUI not ready within {READY_TIMEOUT_S}s")
