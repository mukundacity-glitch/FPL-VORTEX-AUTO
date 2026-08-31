"""Build the private YouTube metadata package for FPL VORTEX Video 1/3.

This module never contacts YouTube. It validates the final true-4K video and
creates JSON containing the dynamic title, description, hashtags, tags,
chapters, and the private-upload safety policy. Thumbnail creation and upload
are intentionally manual.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from datetime import datetime, timezone
import json
import os
import re
import subprocess


DEFAULT_OUTPUT_ROOT = Path("/content/drive/MyDrive/FPL_VORTEX/FIRST VIDEO")
YT_VIDEO_PART = 1
YT_VIDEO_TOTAL = 3
YT_VIDEO_SIZE = (3840, 2160)


def _vx23_required_file(path, label):
    path = Path(path)
    if not path.is_file() or path.stat().st_size <= 0:
        raise FileNotFoundError(f"Required {label} is missing or empty: {path}")
    return path


def _vx23_single_file(folder, pattern, label):
    matches = sorted(path for path in Path(folder).glob(pattern) if path.is_file())
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected exactly one {label} matching {pattern!r} in {folder}; "
            f"found {len(matches)}"
        )
    return _vx23_required_file(matches[0], label)


def _vx23_probe_video(path):
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,codec_name,avg_frame_rate",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    streams = payload.get("streams") or []
    if len(streams) != 1:
        raise RuntimeError(f"Expected one video stream in {path}; found {len(streams)}")
    stream = streams[0]
    rate = str(stream.get("avg_frame_rate") or "0/1")
    try:
        numerator, denominator = rate.split("/", 1)
        fps = float(numerator) / float(denominator)
    except (TypeError, ValueError, ZeroDivisionError):
        fps = 0.0
    return {
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
        "codec": str(stream.get("codec_name") or ""),
        "fps": fps,
        "duration": float(payload.get("format", {}).get("duration") or 0.0),
    }


def _vx23_read_json(path, label):
    path = _vx23_required_file(path, label)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Expected {label} to be a JSON object: {path}")
    return payload


def _vx23_atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(temporary, path)



def _vx23_timestamp(total_seconds):
    whole = max(0, int(float(total_seconds)))
    hours, remainder = divmod(whole, 3600)
    minutes, seconds = divmod(remainder, 60)
    return (
        f"{hours}:{minutes:02d}:{seconds:02d}"
        if hours
        else f"{minutes}:{seconds:02d}"
    )


def _vx23_chapters(qa, review_gw):
    labels = {
        "00_intro": "FPL VORTEX intro",
        "gw_review": f"GW{review_gw} review",
        "02_fdr": "Fixture difficulty",
        "03_projected_goals": "Projected goals",
        "04_clean_sheets": "Clean-sheet odds",
        "05_outro": "Next video",
    }
    integration = qa.get("external_media_integration")
    if not isinstance(integration, dict):
        integration = {}
    opening = integration.get("opening")
    if not isinstance(opening, dict):
        opening = {}
    opening_duration = (
        float(opening.get("duration") or 0.0)
        if integration.get("applied") is True
        else 0.0
    )
    elapsed = opening_duration
    rows = []
    for index, section in enumerate(qa.get("sections", [])):
        if not isinstance(section, dict):
            continue
        key = str(section.get("key") or "")
        label = labels.get(key, str(section.get("scene") or key).title())
        if index == 0 and key == "00_intro" and opening_duration > 0:
            rows.append("0:00 Opening & FPL VORTEX intro")
        else:
            rows.append(f"{_vx23_timestamp(elapsed)} {label}")
        elapsed += float(section.get("duration") or 0.0)
    if not rows or not rows[0].startswith("0:00 "):
        raise RuntimeError("YouTube chapters must begin at 0:00")
    return rows


def _vx23_metadata(preview_gw, review_gw, review_status, qa):
    title = (
        f"FPL GW{preview_gw} Tips: Fixtures, Goals & Clean Sheets | "
        f"GW{review_gw} Review (1/3) #FPL"
    )
    status_text = "live/partial" if review_status == "LIVE/PARTIAL" else "completed"
    hashtags = ["#FPL", "#FantasyPremierLeague", f"#FPLGW{preview_gw}"]
    chapters = _vx23_chapters(qa, review_gw)
    description = "\n".join(
        [
            (
                f"FPL Gameweek {preview_gw} tips and preview, plus a {status_text} "
                f"GW{review_gw} review from FPL VORTEX."
            ),
            "",
            f"VIDEO {YT_VIDEO_PART}/{YT_VIDEO_TOTAL} for Gameweek {preview_gw} covers:",
            f"• GW{review_gw} squad review and key returns",
            f"• GW{preview_gw}–GW{min(38, preview_gw + 5)} fixture difficulty",
            "• Model-projected team goals and attacking outlook",
            "• Clean-sheet probabilities and defensive outlook",
            "",
            "CHAPTERS",
            *chapters,
            "",
            "FPL VORTEX uses public FPL data and model-led analysis. Projections can change with new team news and are provided for information, not guarantees.",
            "",
            " ".join(hashtags),
        ]
    )
    tags = [
        "FPL",
        "Fantasy Premier League",
        f"FPL GW{preview_gw}",
        f"Gameweek {preview_gw}",
        f"FPL GW{preview_gw} tips",
        f"FPL GW{preview_gw} preview",
        "FPL fixtures",
        "fixture difficulty",
        "projected goals",
        "clean sheet odds",
        "FPL review",
        "FPL VORTEX",
    ]
    if len(title) > 100:
        raise RuntimeError(f"YouTube title exceeds 100 characters: {len(title)}")
    if len(description.encode("utf-8")) > 5000:
        raise RuntimeError("YouTube description exceeds 5,000 UTF-8 bytes")
    if sum(len(tag) for tag in tags) + max(0, len(tags) - 1) > 500:
        raise RuntimeError("YouTube tags exceed the 500-character limit")
    return {
        "title": title,
        "description": description,
        "hashtags": hashtags,
        "tags": tags,
        "chapters": chapters,
        "category": "Sports",
        "category_id": "17",
        "language": "en-GB",
        "recommended_visibility": "Private",
        "made_for_kids": False,
        "notify_subscribers_before_manual_publish": False,
        "series": {"part": YT_VIDEO_PART, "total": YT_VIDEO_TOTAL},
        "preview_gw": int(preview_gw),
        "review_gw": int(review_gw),
        "review_status": review_status,
    }


def build_youtube_studio_package(output_root):
    output_root = Path(output_root).resolve()
    video_dir = output_root / "MP4"
    data_dir = output_root / "DATA"
    for folder in (video_dir, data_dir):
        if not folder.is_dir():
            raise FileNotFoundError(f"Required Day 1 output directory is missing: {folder}")

    qa = _vx23_read_json(data_dir / "final_video_qa.json", "final video QA")
    review = _vx23_read_json(
        data_dir / "gw_review_package.json",
        "Gameweek review package",
    )
    if qa.get("passed") is not True:
        raise RuntimeError("Final video QA has not passed; refusing YouTube packaging")
    if qa.get("external_media_integration", {}).get("applied") is not True:
        raise RuntimeError(
            "Opening/music integration must pass before the YouTube Studio package is built"
        )

    configured_video = str(qa.get("final_file") or "").strip()
    if configured_video:
        candidate = Path(configured_video)
        final_video = video_dir / candidate.name
        if final_video.resolve().parent != video_dir.resolve():
            raise RuntimeError(f"Final QA points outside the MP4 directory: {candidate}")
        final_video = _vx23_required_file(final_video, "final 4K MP4")
    else:
        final_video = _vx23_single_file(
            video_dir,
            "*COMBINED*.mp4",
            "combined final MP4",
        )

    video_probe = _vx23_probe_video(final_video)
    if (video_probe["width"], video_probe["height"]) != YT_VIDEO_SIZE:
        raise RuntimeError(
            "YouTube Studio package requires a true 4K final MP4; "
            f"found {video_probe['width']}x{video_probe['height']}"
        )
    if list(qa.get("video_output") or []) != [3840, 2160]:
        raise RuntimeError(
            f"Final video QA is not true 4K: {qa.get('video_output')}"
        )

    match = re.search(r"(?:^|_)GW(\d{1,2})(?:_|$)", final_video.name, re.I)
    preview_gw = int(match.group(1)) if match else 0
    review_gw = int(review.get("gw") or 0)
    if not 1 <= preview_gw <= 38 or not 1 <= review_gw <= 38:
        raise RuntimeError(
            f"Invalid dynamic Gameweeks: preview={preview_gw}, review={review_gw}"
        )

    review_status = str(review.get("status") or "FINAL").upper()
    if review_status not in {"FINAL", "LIVE/PARTIAL"}:
        raise RuntimeError(f"Invalid Gameweek review status: {review_status!r}")

    metadata = _vx23_metadata(preview_gw, review_gw, review_status, qa)

    package = {
        "version": "FPL-VORTEX-YOUTUBE-PRIVATE-AUTO-V4",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "manual_upload_only": False,
        "youtube_api_upload_enabled": True,
        "automatic_publication_enabled": False,
        "upload_policy": {
            "privacy_status": "private",
            "notify_subscribers": False,
            "automatic_publish": False,
            "automatic_schedule": False,
            "manual_publication_required": True,
        },
        "video": {
            "file": str(final_video),
            "resolution": [video_probe["width"], video_probe["height"]],
            "codec": video_probe["codec"],
            "fps": video_probe["fps"],
            "duration_seconds": video_probe["duration"],
        },
        "metadata": metadata,
        "studio_checklist": [
            "GitHub uploads the packaged 4K MP4 to the verified channel as Private.",
            "GitHub applies this generated title, description, and tags.",
            "Add your thumbnail manually in YouTube Studio after reviewing the Private upload.",
            "Open the video in YouTube Studio and review every field.",
            "Only change Visibility to Public manually when the video is ready.",
        ],
    }
    package_path = data_dir / "youtube_video_1_of_3_package.json"
    _vx23_atomic_json(package_path, package)

    print("\n" + "=" * 74)
    print("✅ PRIVATE YOUTUBE UPLOAD PACKAGE — VIDEO 1/3")
    print("=" * 74)
    print("✅ YouTube API upload enabled: Private only; automatic publishing disabled")
    print(f"✅ Dynamic preview/review: GW{preview_gw} / GW{review_gw} ({review_status})")
    print(f"✅ Copy/paste package: {package_path}")
    print("\nTITLE\n" + metadata["title"])
    print("\nDESCRIPTION\n" + metadata["description"])
    return package


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Create the dynamic SEO metadata package for automatic Private "
            "YouTube upload without generating an image"
        )
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path(os.environ.get("DAY1_OUTPUT_ROOT", DEFAULT_OUTPUT_ROOT)),
    )
    args = parser.parse_args()
    build_youtube_studio_package(args.output_root)


if __name__ == "__main__":
    main()

