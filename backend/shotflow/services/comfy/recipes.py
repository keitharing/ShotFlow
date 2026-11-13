"""Recipes: ComfyUI workflows registered in ShotFlow. Nodes titled "SF:..." become form inputs."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ...config import ROOT, Settings, load_settings
from ...models import Recipe, RecipeVersion, log_activity
from .server import model_path

RECIPES_DIR = ROOT / "recipes"
SF_PREFIX = "SF:"
MODEL_INPUTS = {  # loader input name -> ComfyUI models sub-folder
    "ckpt_name": "checkpoints", "unet_name": "diffusion_models", "clip_name": "text_encoders",
    "vae_name": "vae", "lora_name": "loras", "control_net_name": "controlnet",
}
SNAPSHOT = "object_info_snapshot.json"
RESERVED_INPUTS = {"filename_prefix"}  # always set by ShotFlow so outputs land in the job folder


@dataclass
class Exposed:
    key: str  # "<node_id>.<input>"
    node_id: str
    node_title: str
    label: str
    input: str
    type: str
    default: Any
    min: float | None = None
    max: float | None = None
    step: float | None = None
    options: list[Any] | None = None
    multiline: bool = False


def load_object_info(settings: Settings | None = None, live: dict | None = None) -> dict[str, Any]:
    """Live schema when ComfyUI is up (and cached), otherwise the cached snapshot."""
    settings = settings or load_settings()
    path = settings.data_dir / "comfy" / SNAPSHOT
    if live:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(live, ensure_ascii=False), encoding="utf-8")
        return live
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def title(node: dict[str, Any]) -> str:
    return (node.get("_meta") or {}).get("title", "")


def find_node(api: dict[str, Any], node_title: str) -> str:
    for node_id, node in api.items():
        if title(node) == node_title:
            return node_id
    raise KeyError(f"No node titled {node_title!r}")


def _is_link(value: Any) -> bool:
    return isinstance(value, list) and len(value) == 2 and isinstance(value[0], str)


def exposed_inputs(api: dict[str, Any], object_info: dict[str, Any], expose: dict[str, list[str]]) -> list[Exposed]:
    result: list[Exposed] = []
    for node_id, node in api.items():
        node_title = title(node)
        if not node_title.startswith(SF_PREFIX):
            continue
        schema = object_info.get(node["class_type"], {}).get("input", {})
        spec_all = {**schema.get("required", {}), **schema.get("optional", {})}
        wanted = expose.get(node_title)
        names = [n for n, v in node["inputs"].items() if not _is_link(v)] if wanted is None else wanted
        for name in (n for n in names if n not in RESERVED_INPUTS):
            value = node["inputs"].get(name)
            spec = spec_all.get(name, [type(value).__name__.upper()])
            kind, meta = spec[0], (spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {})
            options = kind if isinstance(kind, list) else None
            result.append(Exposed(
                key=f"{node_id}.{name}", node_id=node_id, node_title=node_title,
                label=node_title[len(SF_PREFIX):].strip(), input=name,
                type="COMBO" if options is not None else str(kind), default=value,
                min=meta.get("min"), max=meta.get("max"), step=meta.get("step"),
                options=options, multiline=bool(meta.get("multiline")) or (kind == "STRING" and name == "text"),
            ))
    return result


def required_models(api: dict[str, Any]) -> list[dict[str, Any]]:
    models = []
    for node in api.values():
        for name, value in node["inputs"].items():
            folder = MODEL_INPUTS.get(name)
            if folder and isinstance(value, str):
                path = model_path(folder, value)
                models.append({"node": node["class_type"], "input": name, "file": value, "folder": folder,
                               "present": path.is_file(), "bytes": path.stat().st_size if path.is_file() else None})
    return models


def graph_for_display(api: dict[str, Any]) -> dict[str, Any]:
    """Nodes (with literal inputs) and links, for drawing "the node graph that made this result"."""
    nodes, links = [], []
    for node_id, node in api.items():
        literals = {k: v for k, v in node["inputs"].items() if not _is_link(v)}
        nodes.append({"id": node_id, "class_type": node["class_type"], "title": title(node), "inputs": literals})
        for name, value in node["inputs"].items():
            if _is_link(value):
                links.append({"from": value[0], "slot": value[1], "to": node_id, "input": name})
    return {"nodes": nodes, "links": links}


def apply_values(api: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    graph = copy.deepcopy(api)
    for key, value in values.items():
        node_id, name = key.split(".", 1)
        if node_id not in graph or name not in graph[node_id]["inputs"]:
            raise KeyError(f"Unknown input {key}")
        if _is_link(graph[node_id]["inputs"][name]):
            raise ValueError(f"{key} is a link, not a value")
        graph[node_id]["inputs"][name] = value
    return graph


def validate_values(exposed: list[Exposed], values: dict[str, Any]) -> dict[str, Any]:
    """Coerce form values to the node's types and clamp-check numeric ranges."""
    by_key = {e.key: e for e in exposed}
    clean: dict[str, Any] = {}
    for key, raw in values.items():
        e = by_key.get(key)
        if e is None:
            raise ValueError(f"{key} is not an exposed input")
        if e.type == "INT":
            value: Any = int(raw)
        elif e.type == "FLOAT":
            value = float(raw)
        elif e.type == "BOOLEAN":
            value = bool(raw)
        elif e.type == "COMBO":
            if raw not in (e.options or []):
                raise ValueError(f"{e.label} {e.input}: {raw!r} is not an option")
            value = raw
        else:
            value = str(raw)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if e.min is not None and value < e.min or e.max is not None and value > e.max:
                raise ValueError(f"{e.label} {e.input}: {value} outside {e.min}..{e.max}")
        clean[key] = value
    return clean


def _digest(data: Any) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def load_dir(folder: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    meta = json.loads((folder / "recipe.json").read_text(encoding="utf-8"))
    api = json.loads((folder / "api.json").read_text(encoding="utf-8"))
    ui_path = folder / "ui.json"
    ui = json.loads(ui_path.read_text(encoding="utf-8")) if ui_path.is_file() else None
    return meta, api, ui


def sync(session: Session, object_info: dict[str, Any], folder: Path = RECIPES_DIR,
         actor: str = "system") -> list[dict[str, Any]]:
    """Register every recipe folder; a changed api.json or recipe.json becomes a new recipe version."""
    report = []
    for recipe_dir in sorted(p for p in folder.iterdir() if (p / "recipe.json").is_file()):
        meta, api, ui = load_dir(recipe_dir)
        recipe = session.query(Recipe).filter_by(name=meta["id"]).one_or_none()
        if recipe is None:
            recipe = Recipe(name=meta["id"], kind=meta["kind"], description=meta.get("description"))
            session.add(recipe)
            session.flush()
        recipe.kind, recipe.description = meta["kind"], meta.get("description")
        exposed = [asdict(e) for e in exposed_inputs(api, object_info, meta.get("expose", {}))]
        digest = _digest({"api": api, "meta": meta})
        latest = recipe.versions[-1] if recipe.versions else None
        if latest is None or (latest.ui_json or {}).get("digest") != digest:
            number = (latest.number + 1) if latest else 1
            session.add(RecipeVersion(recipe=recipe, number=number, api_json=api,
                                      ui_json={"digest": digest, "meta": meta, "ui": ui},
                                      exposed_inputs=exposed, required_models=required_models(api)))
            session.flush()
            log_activity(session, actor, "recipe_registered", recipe, version=number)
            report.append({"recipe": meta["id"], "version": number, "status": "registered"})
        else:
            latest.exposed_inputs = exposed  # refresh types if the ComfyUI schema became available
            latest.required_models = required_models(api)
            report.append({"recipe": meta["id"], "version": latest.number, "status": "unchanged"})
    return report
