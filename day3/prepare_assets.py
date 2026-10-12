from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path


def prepare_assets(asset_dir: Path) -> None:
    replacements: dict[str, str] = {}
    for missing, source in (("6.png", "5.png"), ("7.png", "8.png")):
        target = asset_dir / missing
        if not target.is_file():
            original = asset_dir / source
            if not original.is_file():
                raise FileNotFoundError(original)
            shutil.copyfile(original, target)
            replacements[missing] = source
    transition = asset_dir / "trans.mp4"
    if not transition.is_file():
        temporary = transition.with_suffix(".pending.mp4")
        try:
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-loop", "1", "-i", str(asset_dir / "1.png"),
                "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "4", "-vf", "scale=3840:2160,setsar=1",
                "-r", "24", "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-threads", "4", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(temporary),
            ], check=True)
            temporary.replace(transition)
        finally:
            temporary.unlink(missing_ok=True)
        replacements["trans.mp4"] = "four-second local transition from 1.png"
    (asset_dir / "day3_asset_fallbacks.json").write_text(json.dumps(replacements, indent=2), encoding="utf-8")
    print(f"Day 3 local asset fallbacks: {replacements or 'none needed'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("asset_dir", type=Path)
    prepare_assets(parser.parse_args().asset_dir)
