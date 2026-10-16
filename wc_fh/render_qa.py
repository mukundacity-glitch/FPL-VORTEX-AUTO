from __future__ import annotations

import atexit
import json
import math
from pathlib import Path
from typing import Any, Callable

from PIL import Image


def measured_timeline(scene: dict[str, Any]) -> list[dict[str, Any]]:
    timeline = scene.get("narration_timeline")
    timing = scene.get("timing", {})
    if not timeline or timing.get("player_alignment") != "measured_segments" or timing.get("status") != "measured":
        raise ValueError(f"{scene.get('scene_id')}: measure narration before visual verification")
    duration = scene.get("duration_seconds")
    if not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
        raise ValueError(f"{scene.get('scene_id')}: invalid measured scene duration")
    previous_end = 0.0
    for cue in timeline:
        start, end = cue.get("start"), cue.get("end")
        if (not isinstance(start, (int, float)) or not isinstance(end, (int, float))
                or not math.isfinite(start) or not math.isfinite(end)
                or start < previous_end - 0.001 or end <= start or end > duration + 0.001):
            raise ValueError(f"{scene.get('scene_id')}: invalid measured narration cue {cue.get('segment_id')}")
        if cue.get("alignment", "measured_segments") != "measured_segments":
            raise ValueError(f"{scene.get('scene_id')}: estimated narration cues cannot verify Final visuals")
        previous_end = end
    return timeline


def verify_layout(layout: list[dict[str, Any]]) -> None:
    for item in layout:
        if item["kind"] == "photo" and not abs(item["source_aspect"] - item["rendered_aspect"]) < 0.025:
            raise ValueError("Player image distorted")
        if item["kind"] == "pitch" and not abs(item["aspect"] - item["reference_aspect"]) < 0.003:
            raise ValueError("Pitch aspect wrong")
    texts = [item for item in layout if item["kind"] == "text"]
    for index, first in enumerate(texts):
        for second in texts[index + 1:]:
            left, top, right, bottom = first["box"]
            other_left, other_top, other_right, other_bottom = second["box"]
            if min(right, other_right) - max(left, other_left) > 2 and min(bottom, other_bottom) - max(top, other_top) > 2:
                raise ValueError(f"Overlapping text: {first['text']} / {second['text']}")


def verified_visual(factory: Callable[..., Any], scene: dict[str, Any], contract: dict[str, Any],
                    width: int, height: int) -> Any:
    measured_timeline(scene)
    visual = factory(scene, contract, width, height)
    draw = getattr(visual, "draw", None)
    if callable(draw):
        duration = float(scene["duration_seconds"])
        for seconds in (0.0, duration / 2, max(0.0, duration - 0.05)):
            frame = draw(seconds, duration)
            if not isinstance(frame, Image.Image) or frame.size != (width, height):
                raise ValueError(f"{scene['scene_id']}: visual frame is not {width}×{height}")
            verify_layout(getattr(visual, "layout", []))
    elif not callable(getattr(visual, "render_video", None)):
        raise TypeError(f"{scene['scene_id']}: renderer has neither draw nor native render_video")
    # Native Remotion rendering performs its own still preflight; validate its exported PNG below.
    return visual


def verify_exported_visuals(root: Path, manifest: dict[str, Any]) -> None:
    root = root.resolve()
    for key, delivery in manifest.items():
        scenes = json.loads((root / "analysis" / f"{key}_scene_manifest.json").read_text(encoding="utf-8"))
        images = delivery.get("slide_pngs_4k", [])
        if len(scenes) != 4 or len(images) != len(scenes) or len({scene["scene_id"] for scene in scenes}) != 4:
            raise ValueError(f"{key}: four measured scenes and matching 4K PNGs are required")
        report = []
        for scene, image in zip(scenes, images):
            timeline = measured_timeline(scene)
            path = Path(image).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise ValueError(f"{key}: missing or non-isolated visual export: {path}")
            with Image.open(path) as frame:
                frame.load()
                if frame.size != (3840, 2160) or frame.format != "PNG":
                    raise ValueError(f"{key}/{scene['scene_id']}: export must be a native 3840×2160 PNG")
            report.append(dict(scene_id=scene["scene_id"], measured_cues=len(timeline),
                               duration_seconds=scene["duration_seconds"], output=str(path), verified=True))
        (root / "analysis" / f"{key}_visual_qa.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"WC- FH: {key}: four measured timelines and native 4K visual exports verified", flush=True)


def close_renderer(namespace: dict[str, Any]) -> None:
    renderer = namespace.pop("VX_BROADCAST_RENDERER", None)
    if renderer is not None:
        # Its atexit callback otherwise runs after ThreadPoolExecutor has already shut down.
        atexit.unregister(renderer.close)
        renderer.close()
