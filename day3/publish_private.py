from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps


YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
YOUTUBE_READ_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _required_env(name: str) -> str:
    value = str(os.environ.get(name, "") or "").strip()
    if not value:
        raise RuntimeError(f"Missing required GitHub secret: {name}")
    return value


def _oauth_value(primary: str, fallback: str) -> str:
    value = str(os.environ.get(primary, "") or "").strip()
    return value or _required_env(fallback)


def _one_final_video(output_root: Path) -> Path:
    mp4_dir = output_root / "MP4"
    candidates = sorted(
        path
        for pattern in ("*COMBINED*.mp4", "*combined*.mp4")
        for path in mp4_dir.glob(pattern)
        if path.is_file() and path.stat().st_size > 0
    )
    unique: dict[Path, None] = {}
    for path in candidates:
        unique[path.resolve()] = None
    candidates = sorted(unique)

    if len(candidates) != 1:
        all_mp4 = sorted(path.name for path in mp4_dir.glob("*.mp4") if path.is_file())
        raise RuntimeError(
            "Expected exactly one complete final MP4. "
            f"Matched {len(candidates)} candidates: {[p.name for p in candidates]}. "
            f"All MP4 files: {all_mp4}"
        )
    return candidates[0]


def _probe_video(video_path: Path) -> dict[str, Any]:
    completed = subprocess.run(
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
            str(video_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    stream = (payload.get("streams") or [{}])[0]
    return {
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
        "codec": str(stream.get("codec_name") or ""),
        "duration": float((payload.get("format") or {}).get("duration") or 0.0),
    }


def _infer_gameweeks(output_root: Path, final_video: Path) -> tuple[int, int | None]:
    for text in (final_video.name, str(final_video)):
        match = re.search(r"GW[_ -]?(\d{1,2})(?=[^0-9]|$)", text, flags=re.IGNORECASE)
        if match:
            preview_gw = int(match.group(1))
            if 1 <= preview_gw <= 38:
                return preview_gw, preview_gw - 1 if preview_gw > 1 else None

    review_path = output_root / "DATA" / "gw_review_package.json"
    if review_path.is_file():
        try:
            review = json.loads(review_path.read_text(encoding="utf-8"))
            review_gw = int(review.get("gw") or 0)
            if 1 <= review_gw <= 37:
                return review_gw + 1, review_gw
        except Exception:
            pass

    raise RuntimeError("Could not infer the planning Gameweek from final Day 3 output.")


def _thumbnail_source(output_root: Path, plan: str) -> Path:
    slide_dir = output_root / "SLIDE"
    preferred_names = {
        "ROLL": (
            "my_plan_5a_roll_scene_4k.png",
            "transfer_decision_desk_scene_4k.png",
            "final_shortlists_scene_4k.png",
        ),
        "TRANSFER": (
            "my_plan_5b_transfer_scene_4k.png",
            "transfer_decision_desk_scene_4k.png",
            "final_shortlists_scene_4k.png",
        ),
        "NEITHER": (
            "transfer_decision_desk_scene_4k.png",
            "final_shortlists_scene_4k.png",
            "projected_goals_scene_4k.png",
        ),
    }
    for name in preferred_names.get(plan, preferred_names["NEITHER"]):
        candidate = slide_dir / name
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate

    generic = sorted(
        path for path in slide_dir.glob("*.png")
        if path.is_file() and path.stat().st_size > 0
    )
    if not generic:
        raise FileNotFoundError("No rendered Day 3 slide is available for the YouTube thumbnail.")
    return generic[0]


def _build_thumbnail(output_root: Path, plan: str) -> Path:
    source = _thumbnail_source(output_root, plan)
    destination = output_root / "DATA" / "youtube_thumbnail.jpg"
    destination.parent.mkdir(parents=True, exist_ok=True)

    with Image.open(source) as image:
        image = image.convert("RGB")
        fitted = ImageOps.fit(
            image,
            (1280, 720),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )
        fitted.save(destination, format="JPEG", quality=94, optimize=True, progressive=True)

    if not destination.is_file() or destination.stat().st_size <= 0:
        raise RuntimeError("Thumbnail generation failed.")
    if destination.stat().st_size > 50 * 1024 * 1024:
        raise RuntimeError("Generated thumbnail exceeds YouTube's 50 MB limit.")

    print(f"✅ Matching thumbnail source: {source}")
    print(f"✅ YouTube thumbnail: {destination}")
    return destination


def _metadata(preview_gw: int, review_gw: int | None, plan: str, transfer_mode: str) -> dict[str, Any]:
    plan_text = {
        "NEITHER": "Final Analysis",
        "ROLL": "Roll Transfer Plan",
        "TRANSFER": "Transfer Plan",
    }.get(plan, "Final Analysis")
    transfer_note = ""
    if plan == "TRANSFER":
        transfer_note = f" • {transfer_mode.title()} transfer selection"

    title = f"FPL GW{preview_gw} Final Decisions: Transfers, Bench & Captain | FPL VORTEX"
    if len(title) > 100:
        raise RuntimeError("Generated YouTube title exceeds 100 characters.")

    review_line = (
        f"Includes the GW{review_gw} review and the complete GW{preview_gw} decision process."
        if review_gw
        else f"Complete GW{preview_gw} decision process."
    )
    description = "\n".join(
        [
            f"FPL VORTEX Gameweek {preview_gw} final decision video.",
            review_line,
            "",
            f"Plan mode: {plan_text}{transfer_note}",
            "",
            "Covered in this video:",
            "• Projected goals and clean-sheet outlook",
            "• DEFCON and final player shortlists",
            "• Transfer Decision Desk",
            "• Gameweek review and elite ownership trends",
            "• Final benching and captaincy decisions",
            "",
            "Model-led FPL analysis using current public data. Projections are not guarantees.",
            "",
            f"#FPL #FantasyPremierLeague #FPLGW{preview_gw} #FPLVORTEX",
        ]
    )
    tags = [
        "FPL",
        "Fantasy Premier League",
        f"FPL GW{preview_gw}",
        f"Gameweek {preview_gw}",
        "FPL transfers",
        "FPL captain",
        "FPL bench",
        "FPL tips",
        "FPL VORTEX",
    ]
    return {
        "title": title,
        "description": description,
        "tags": tags,
        "category_id": "17",
        "language": "en-GB",
    }


def _youtube_service():
    from google.auth.transport.requests import Request as GoogleRequest
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    credentials = Credentials(
        token=None,
        refresh_token=_required_env("YOUTUBE_OAUTH_REFRESH_TOKEN"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=_oauth_value("YOUTUBE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_ID"),
        client_secret=_oauth_value(
            "YOUTUBE_OAUTH_CLIENT_SECRET",
            "GOOGLE_OAUTH_CLIENT_SECRET",
        ),
        scopes=[YOUTUBE_UPLOAD_SCOPE, YOUTUBE_READ_SCOPE],
    )
    credentials.refresh(GoogleRequest())
    return build("youtube", "v3", credentials=credentials, cache_discovery=False)


def _verify_channel(youtube) -> dict[str, str]:
    expected_channel_id = _required_env("YOUTUBE_CHANNEL_ID")
    response = youtube.channels().list(
        part="id,snippet",
        mine=True,
        fields="items(id,snippet/title)",
    ).execute(num_retries=5)
    items = response.get("items") or []
    if len(items) != 1:
        raise RuntimeError(f"Expected exactly one authenticated YouTube channel; found {len(items)}.")
    actual_id = str(items[0].get("id") or "")
    if actual_id != expected_channel_id:
        raise RuntimeError(
            "YouTube OAuth channel mismatch; refusing upload. "
            f"expected={expected_channel_id}, authenticated={actual_id}"
        )
    return {
        "id": actual_id,
        "title": str((items[0].get("snippet") or {}).get("title") or ""),
    }


def _upload_video(youtube, video_path: Path, metadata: dict[str, Any]) -> str:
    from googleapiclient.http import MediaFileUpload

    request = youtube.videos().insert(
        part="snippet,status",
        notifySubscribers=False,
        body={
            "snippet": {
                "title": metadata["title"],
                "description": metadata["description"],
                "tags": metadata["tags"],
                "categoryId": metadata["category_id"],
                "defaultLanguage": metadata["language"],
            },
            "status": {
                "privacyStatus": "private",
                "selfDeclaredMadeForKids": False,
            },
        },
        media_body=MediaFileUpload(
            str(video_path),
            mimetype="video/mp4",
            chunksize=8 * 1024 * 1024,
            resumable=True,
        ),
    )

    response = None
    while response is None:
        progress, response = request.next_chunk(num_retries=5)
        if progress is not None:
            print(f"📤 YouTube upload: {progress.progress() * 100:.1f}%")

    video_id = str((response or {}).get("id") or "")
    if not video_id:
        raise RuntimeError("YouTube upload completed without a video ID.")
    return video_id


def _set_thumbnail(youtube, video_id: str, thumbnail_path: Path) -> None:
    from googleapiclient.http import MediaFileUpload

    response = youtube.thumbnails().set(
        videoId=video_id,
        media_body=MediaFileUpload(
            str(thumbnail_path),
            mimetype="image/jpeg",
            resumable=False,
        ),
    ).execute(num_retries=5)
    items = response.get("items") or []
    if not items:
        raise RuntimeError("YouTube did not confirm the custom thumbnail.")
    print("✅ Matching custom thumbnail applied to private YouTube upload.")


def _verify_private(youtube, video_id: str) -> None:
    response = youtube.videos().list(
        part="status",
        id=video_id,
        fields="items(id,status/privacyStatus)",
    ).execute(num_retries=5)
    items = response.get("items") or []
    if len(items) != 1:
        raise RuntimeError("Could not verify uploaded YouTube video.")
    privacy = str((items[0].get("status") or {}).get("privacyStatus") or "")
    if privacy != "private":
        raise RuntimeError(f"Upload safety check failed: privacyStatus={privacy!r}")


def publish(output_root: Path, plan: str, transfer_mode: str) -> dict[str, Any]:
    output_root = output_root.resolve()
    final_video = _one_final_video(output_root)
    probe = _probe_video(final_video)
    if (probe["width"], probe["height"]) != (3840, 2160):
        raise RuntimeError(
            "Day 3 GitHub workflow is FINAL-only and requires true 4K output; "
            f"found {probe['width']}x{probe['height']}."
        )

    preview_gw, review_gw = _infer_gameweeks(output_root, final_video)
    thumbnail_path = _build_thumbnail(output_root, plan)
    metadata = _metadata(preview_gw, review_gw, plan, transfer_mode)

    report_path = output_root / "DATA" / "youtube_private_upload.json"
    report: dict[str, Any] = {
        "version": "FPL-VORTEX-DAY3-GITHUB-PRIVATE-V1",
        "started_at": _utc_now(),
        "plan": plan,
        "transfer_mode": transfer_mode,
        "video_file": str(final_video),
        "thumbnail_file": str(thumbnail_path),
        "video_probe": probe,
        "metadata": metadata,
        "privacy_status": "private",
        "automatic_publish": False,
        "notify_subscribers": False,
    }

    youtube = _youtube_service()
    channel = _verify_channel(youtube)
    report["channel"] = channel

    video_id = _upload_video(youtube, final_video, metadata)
    report["youtube"] = {
        "video_id": video_id,
        "studio_url": f"https://studio.youtube.com/video/{video_id}/edit",
        "watch_url": f"https://www.youtube.com/watch?v={video_id}",
    }
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    _set_thumbnail(youtube, video_id, thumbnail_path)
    _verify_private(youtube, video_id)

    report["completed_at"] = _utc_now()
    report["thumbnail_applied"] = True
    report["status"] = "private_draft_ready"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print("✅ Day 3 private YouTube draft is ready.")
    print(f"✅ Studio: {report['youtube']['studio_url']}")
    print("🔒 Automatic publication remains disabled.")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload the final Day 3 MP4 as a private YouTube draft with its matching thumbnail.")
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--plan", required=True, choices=["NEITHER", "ROLL", "TRANSFER"])
    parser.add_argument("--transfer-mode", required=True, choices=["AUTO", "MANUAL"])
    args = parser.parse_args()
    publish(args.output_root, args.plan, args.transfer_mode)


if __name__ == "__main__":
    main()
