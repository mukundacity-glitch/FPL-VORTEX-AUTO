from __future__ import annotations

import html as _chip_html
import base64 as _chip_base64
import json as _chip_json
import subprocess as _chip_subprocess
from pathlib import Path as _ChipPath
from typing import Any as _ChipAny

DAY3_PLANNED_CHIP_OPTIONS = ("Triple Captain", "Wildcard", "Free Hit", "Bench Boost")
DAY3_CHIP_PLAN_SCENE = "05c_my_plan_chip"


def chip_plan_copy(chip: str, gameweek: int) -> str:
    if chip not in DAY3_PLANNED_CHIP_OPTIONS or not 1 <= int(gameweek) <= 38:
        raise ValueError("Select one supported planned chip and a valid upcoming Gameweek")
    opening = f"I'm planning to activate my {chip} for Gameweek {int(gameweek)}."
    if chip in {"Wildcard", "Free Hit"}:
        return (opening + " That's why I'm not announcing a normal transfer plan here. "
                f"I'll share my full {chip} strategy, planned squad and key player selections in tomorrow's video.")
    detail = "captain choice and reasoning" if chip == "Triple Captain" else "bench choices and minutes checks"
    return opening + f" This is my plan, not an activated chip. I'll explain my {detail} in tomorrow's video."


async def render_chip_plan(namespace: dict[str, _ChipAny]) -> None:
    if not namespace.get("SLIDE_SELECTION", {}).get(DAY3_CHIP_PLAN_SCENE, False):
        return
    chip = str(namespace["PLANNED_CHIP"])
    gameweek = int(namespace["NEXT_GW"])
    text = chip_plan_copy(chip, gameweek)
    html_path = _ChipPath(namespace["HTML_WORK_DIR"]) / "my_plan_chip_scene_4k.html"
    png_path = _ChipPath(namespace["SLIDE_DIR"]) / "my_plan_chip_scene_4k.png"
    audio_path = _ChipPath(namespace["MP3_DIR"]) / "my_plan_chip.mp3"
    video_path = _ChipPath(namespace["MP4_DIR"]) / (DAY3_CHIP_PLAN_SCENE + ".mp4")
    asset_dirs = [path for path in _ChipPath(namespace["PROJECT_ROOT"]).iterdir()
                  if path.is_dir() and path.name.strip().casefold() == "assets"]
    if len(asset_dirs) != 1:
        raise RuntimeError("Day 3 chip announcement requires one production Assets folder")
    logo_path = asset_dirs[0] / "logo1.png"
    logo = "data:image/png;base64," + _chip_base64.b64encode(logo_path.read_bytes()).decode("ascii")
    source = """<!DOCTYPE html><html><head><meta charset="UTF-8"><style>
html,body{margin:0;width:3840px;height:2160px;overflow:hidden;color:#fff}
#stage{position:relative;width:3840px;height:2160px}
.chipPlanSourceTitle{display:none!important}
.chipPlanPanel{position:absolute;left:240px;right:240px;top:410px;bottom:360px;padding:100px;
display:grid;grid-template-columns:420px minmax(0,1fr);gap:100px;align-items:center;
border:2px solid #36dfef66;border-radius:42px;background:#061322ee}
.chipPlanLogo{width:400px;height:480px;object-fit:contain}
.chipPlanLabel{font:700 62px/1.2 'Montserrat',sans-serif;color:#9deaf4;margin-bottom:32px}
.chipPlanName{font:700 148px/1.15 'Barlow Condensed',sans-serif;color:#ffd447;margin:0 0 36px}
.chipPlanCopy{font:600 76px/1.4 'Barlow Condensed',sans-serif;margin:0}
.chipPlanStatus{margin-top:50px;font:700 48px/1.2 'Montserrat',sans-serif;color:#99eac8}
</style></head><body><div id="stage"><h1 class="chipPlanSourceTitle">My Chip Plan</h1><section class="chipPlanPanel">
<img class="chipPlanLogo" src="__LOGO__" alt="FPL Vortex">
<div><div class="chipPlanLabel">UPCOMING GAMEWEEK __GW__</div><h2 class="chipPlanName">__CHIP__</h2>
<p class="chipPlanCopy">__TEXT__</p><div class="chipPlanStatus">PLANNED • NOT YET ACTIVATED</div></div>
</section></div></body></html>"""
    source = source.replace("__LOGO__", logo).replace("__GW__", str(gameweek))
    source = source.replace("__CHIP__", _chip_html.escape(chip)).replace("__TEXT__", _chip_html.escape(text))
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(namespace["vx_polish_branding"](source, scene_key=DAY3_CHIP_PLAN_SCENE), encoding="utf-8")
    info = await namespace["vx_synthesize_audio"](audio_path, text, namespace["vx_tts_profile"]("analysis"))
    await namespace["export_4k_png"](html_path, png_path)
    profile = namespace["vx_require_render_profile"]()
    duration = float(info["duration"]) + 1.0
    temporary = video_path.with_suffix(".pending.mp4")
    video_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        _chip_subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-loop", "1", "-i", str(png_path),
            "-i", str(audio_path), "-t", str(duration), "-vf", f"scale={profile['width']}:{profile['height']},setsar=1",
            "-r", str(profile["fps"]), "-c:v", "libx264", "-preset", str(profile["ffmpeg_preset"]),
            "-crf", str(profile["ffmpeg_crf"]), "-threads", "4", "-pix_fmt", "yuv420p", "-af", "apad",
            "-ar", "48000", "-ac", "2", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(temporary),
        ], check=True)
        temporary.replace(video_path)
    finally:
        temporary.unlink(missing_ok=True)
    report = {"passed": True, "chip": chip, "status": "planned", "gameweek": gameweek,
              "text": text, "duration": duration, "words": info["words"],
              "animated_html": str(html_path), "audio": str(audio_path),
              "animated_html_sha256": namespace["vx_file_sha256"](html_path),
              "audio_sha256": namespace["vx_file_sha256"](audio_path),
              "final_sha256": namespace["vx_file_sha256"](video_path)}
    (_ChipPath(namespace["DATA_DIR"]) / "my_plan_chip_render.json").write_text(_chip_json.dumps(report, indent=2), encoding="utf-8")
    print(f"✅ Planned {chip} announcement ready for Gameweek {gameweek}")
