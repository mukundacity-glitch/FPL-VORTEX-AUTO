from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from day3.publish_private import (
    _probe_video,
    _set_thumbnail,
    _upload_video,
    _verify_channel,
    _verify_private,
    _youtube_service,
)


def chapter(seconds: float) -> str:
    total = max(0, int(seconds))
    if total >= 3600:
        return f"{total//3600}:{total//60%60:02}:{total%60:02}"
    return f"{total//60}:{total%60:02}"


def metadata(chip: str, gameweek: int, selection: dict[str, Any],
             scenes: list[dict[str, Any]], audit: dict[str, Any]) -> dict[str, Any]:
    title = f"FPL GW{gameweek} {chip} Team: Best Picks, Fixtures & Captain | FPL VORTEX"
    if len(title) > 100:
        raise ValueError("The WC/FH YouTube title exceeds 100 characters.")
    strategy = ("A six-Gameweek Wildcard squad, fixture analysis and sustainable transfer plan."
                if chip == "Wildcard" else "A one-Gameweek Free Hit squad built for the starting XI and captaincy.")
    lines = [f"FPL Gameweek {gameweek} {chip} team selection, best picks and captaincy from FPL VORTEX.",
             strategy, "", f"Selection: {selection['selection']}",
             f"Total squad cost: £{selection['total_cost_millions']:.1f}m at current official FPL prices.",
             "", "In this video:", "• Fixture difficulty and expected points",
             "• Complete 15-player squad and budget", "• Starting XI, bench and captaincy", "",
             "Chapters:", "0:00 FPL VORTEX opening"]
    offset = float(audit["opening_duration"])
    for scene in scenes:
        lines.append(f"{chapter(offset + float(scene.get('start_seconds', 0)))} {scene['title']}")
    lines.extend([f"{chapter(offset+float(audit['body_duration']))} Outro", "", "Selected squad:"])
    lines.extend(f"{player['position']}: {player['name']} ({player['club']}) — £{player['price_tenths']/10:.1f}m"
                 for player in selection["players"])
    hashtag = "FPLWildcard" if chip == "Wildcard" else "FPLFreeHit"
    lines.extend(["", "Official FPL: https://fantasy.premierleague.com/", "",
                  "Projections are model estimates and can change with team news and availability.", "",
                  f"#FPL #{hashtag} #FPLGW{gameweek} #FantasyPremierLeague #FPLVORTEX"])
    description = "\n".join(lines)
    if len(description.encode("utf-8")) > 5000:
        raise ValueError("The YouTube description exceeds 5,000 bytes.")
    tags = ["FPL", "Fantasy Premier League", f"FPL GW{gameweek}", f"Gameweek {gameweek}",
            f"FPL {chip}", f"GW{gameweek} {chip} team", "FPL team selection", "FPL captaincy",
            "FPL fixtures", "FPL tips", "FPL VORTEX"]
    return dict(title=title, description=description, tags=tags, category_id="17", language="en-GB")


def inside(root: Path, value: str) -> Path:
    path = Path(value).resolve()
    if not path.is_relative_to(root) or not path.is_file() or not path.stat().st_size:
        raise ValueError(f"Missing or non-isolated output: {path}")
    return path


def build_package(root: Path) -> dict[str, Any]:
    root = root.resolve()
    manifest = json.loads((root / "delivery_manifest.json").read_text())
    selection = json.loads((root / "data" / "selection.json").read_text())
    chip = selection["chip"]
    key = ("wildcard" if chip == "Wildcard" else "free_hit") + "_" + selection["selection"].lower()
    if set(manifest) != {key} or manifest[key].get("mode") != "Final":
        raise ValueError("YouTube upload requires exactly the selected Final chip video.")
    delivery = manifest[key]
    video = inside(root, delivery["final_mp4"])
    probe = _probe_video(video)
    if (probe["width"], probe["height"]) != (3840, 2160) or probe["duration"] <= 0:
        raise ValueError("WC- FH upload requires a valid 3840×2160 Final video.")
    audit = json.loads((root / "analysis" / f"{key}_media_audit.json").read_text())
    if (audit.get("media_effects_version") != "body-music-footer-v7"
            or audit.get("background_music_volume") != 0.08
            or not audit.get("popup_times") or audit.get("footer_popup", {}).get("audio") != "muted"):
        raise ValueError("The combined movie is missing its required 8% background music/footer effects.")
    scenes = json.loads((root / "analysis" / f"{key}_scene_manifest.json").read_text())
    pngs = delivery.get("slide_pngs_4k", [])
    if len(pngs) != 4:
        raise ValueError("The Final video must have four matching 4K slide PNGs.")
    source = inside(root, pngs[0])
    directory = root / "youtube"
    directory.mkdir(parents=True, exist_ok=True)
    thumbnail = directory / "thumbnail.jpg"
    with Image.open(source) as image:
        if image.size != (3840, 2160):
            raise ValueError("Matching thumbnail source must be a native 4K slide.")
        fitted = ImageOps.fit(image.convert("RGB"), (1280, 720), method=Image.Resampling.LANCZOS)
        for quality in (92, 85, 75):
            fitted.save(thumbnail, "JPEG", quality=quality, optimize=True)
            if thumbnail.stat().st_size < 2 * 1024 * 1024:
                break
        else:
            raise ValueError("The custom thumbnail exceeds 2 MB.")
    details = metadata(chip, int(delivery["gameweek"]), selection, scenes, audit)
    package = dict(chip=chip, selection=selection["selection"], video_file=str(video),
                   thumbnail_file=str(thumbnail), video_probe=probe, metadata=details,
                   privacy_status="private", notify_subscribers=False, automatic_publish=False)
    (directory / "metadata.json").write_text(json.dumps(package, indent=2, ensure_ascii=False), encoding="utf-8")
    (directory / "description.txt").write_text(details["description"], encoding="utf-8")
    (directory / "title.txt").write_text(details["title"] + "\n", encoding="utf-8")
    return package


def publish(root: Path) -> dict[str, Any]:
    root = root.resolve()
    package = build_package(root)
    report_path = root / "youtube" / "upload.json"
    youtube = _youtube_service()
    channel = _verify_channel(youtube)
    report = json.loads(report_path.read_text()) if report_path.is_file() else {}
    video_id = str(report.get("youtube", {}).get("video_id", ""))
    if report and report.get("video_file") != package["video_file"]:
        raise ValueError("The existing upload receipt belongs to another video.")
    if not video_id:
        video_id = _upload_video(youtube, Path(package["video_file"]), package["metadata"])
        report = dict(package, channel=channel, started_at=datetime.now(timezone.utc).isoformat(),
                      status="uploaded", youtube=dict(video_id=video_id,
                          studio_url=f"https://studio.youtube.com/video/{video_id}/edit"))
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    _verify_private(youtube, video_id)
    _set_thumbnail(youtube, video_id, Path(package["thumbnail_file"]))
    _verify_private(youtube, video_id)
    report.update(status="private_draft_ready", thumbnail_applied=True,
                  completed_at=datetime.now(timezone.utc).isoformat())
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as handle:
            handle.write(f"\n### WC- FH YouTube draft ready\n[{package['metadata']['title']}]({report['youtube']['studio_url']})\n\n"
                         "Private · matching thumbnail · description · SEO tags · hashtags\n")
    print(f"WC- FH private YouTube draft ready: {report['youtube']['studio_url']}")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Save the selected Final WC/FH video privately to YouTube with SEO metadata.")
    parser.add_argument("--output-root", required=True, type=Path)
    publish(parser.parse_args().output_root)
