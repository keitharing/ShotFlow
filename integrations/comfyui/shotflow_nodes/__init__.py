"""ShotFlow nodes for ComfyUI: pull a ShotFlow version frame into a graph, and publish results back as versions.

Loaded by ShotFlow's own ComfyUI launch (extra_model_paths custom_nodes + --whitelist-custom-nodes shotflow_nodes);
nothing is installed into the ComfyUI folder (design decision D-007).
"""

from __future__ import annotations

from . import core

CATEGORY = "ShotFlow"


def _choice(kind: str, placeholder: str) -> tuple:
    labels = core.catalog(kind)
    return (labels,) if labels else ("STRING", {"default": placeholder})


class ShotFlowLoadVersion:
    """Loads one frame (or the still) of a ShotFlow version as an IMAGE."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "version": _choice("versions", "버전 번호 (ShotFlow를 켜면 목록으로 바뀜)"),
            "frame": ("INT", {"default": 0, "min": 0, "max": 100000, "step": 1}),
        }}

    RETURN_TYPES = ("IMAGE", "INT")
    RETURN_NAMES = ("image", "version_id")
    FUNCTION = "load"
    CATEGORY = CATEGORY

    @classmethod
    def IS_CHANGED(cls, version, frame):
        return f"{version}:{frame}"

    def load(self, version, frame):
        import torch

        version_id = core.parse_id(version)
        array = core.fetch_frame(version_id, int(frame))
        return (torch.from_numpy(array)[None, ...], version_id)


class ShotFlowPublish:
    """Sends images back to ShotFlow as new versions, with the full node graph recorded."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                "shot": _choice("shots", "샷 번호"),
                "note": ("STRING", {"default": "", "multiline": True}),
            },
            "optional": {"parent_version_id": ("INT", {"default": 0, "min": 0, "max": 10**9})},
            "hidden": {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO"},
        }

    RETURN_TYPES = ()
    FUNCTION = "publish"
    OUTPUT_NODE = True
    CATEGORY = CATEGORY

    def publish(self, images, shot, note, parent_version_id=0, prompt=None, extra_pnginfo=None):
        arrays = [frame for frame in images.cpu().numpy()]
        workflow = (extra_pnginfo or {}).get("workflow") if isinstance(extra_pnginfo, dict) else None
        result = core.publish(core.parse_id(shot), arrays, graph=prompt, workflow=workflow,
                              parent_version_id=int(parent_version_id or 0) or None, note=note)
        codes = ", ".join(v["code"] for v in result.get("versions", []))
        return {"ui": {"text": [f"ShotFlow에 등록: {codes}"]}}


NODE_CLASS_MAPPINGS = {"ShotFlowLoadVersion": ShotFlowLoadVersion, "ShotFlowPublish": ShotFlowPublish}
NODE_DISPLAY_NAME_MAPPINGS = {"ShotFlowLoadVersion": "ShotFlow 버전 불러오기", "ShotFlowPublish": "ShotFlow에 등록"}
