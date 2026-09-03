from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .common import read_json, required_file, utc_now, write_json_atomic


DEFAULT_OUTPUT_ROOT = Path("/content/drive/MyDrive/FPL_VORTEX/Day_2")
DEFAULT_CONFIG = Path(__file__).with_name("metadata.json")
VIDEO_NAME = "FPL_VORTEX_DAY2_PRIVATE_REVIEW.mp4"
MEDIA_REPORT_NAME = "day2_media_qa.json"
PACKAGE_NAME = "day2_youtube_package.json"
REPORT_NAME = "day2_youtube_private_upload.json"

YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
YOUTUBE_READ_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
YOUTUBE_PRIVACY_STATUS = "private"
PRIVATE_POLICY = {
    "privacy_status": "private",
    "notify_subscribers": False,
    "automatic_publish": False,
    "automatic_schedule": False,
    "manual_publication_required": True,
}


def _required_env(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise RuntimeError(f"Missing required GitHub secret: {name}")
    return value


def _oauth_value(youtube_name: str, google_name: str) -> str:
    value = (os.environ.get(youtube_name) or "").strip()
    return value or _required_env(google_name)


def _probe_video(path: Path) -> dict[str, Any]:
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


def _parse_video_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise RuntimeError(
            f"video_date must use YYYY-MM-DD, received {value!r}"
        ) from exc
    return parsed


def _render_metadata(config: dict[str, Any], video_date: str) -> dict[str, Any]:
    parsed = _parse_video_date(video_date)
    topic = str(config.get("topic") or "").strip()
    summary = str(config.get("summary") or "").strip()
    title_template = str(config.get("title_template") or "").strip()
    description_template = str(config.get("description_template") or "").strip()
    if not all((topic, summary, title_template, description_template)):
        raise RuntimeError("Day 2 metadata config is incomplete")

    values = {
        "video_date": parsed.isoformat(),
        "month_name": parsed.strftime("%B"),
        "day_2digit": parsed.strftime("%d"),
        "topic": topic,
        "summary": summary,
    }
    title = title_template.format(**values)
    description = description_template.format(**values)
    hashtags = [str(item).strip() for item in config.get("hashtags") or [] if str(item).strip()]
    tags = [str(item).strip() for item in config.get("tags") or [] if str(item).strip()]

    if len(title) > 100:
        raise RuntimeError(f"YouTube title exceeds 100 characters: {len(title)}")
    if "Day 2" not in title:
        raise RuntimeError("Day 2 title must visibly identify Day 2")
    if parsed.strftime("%B") not in title or parsed.strftime("%d") not in title:
        raise RuntimeError("Day 2 title must include the configured month and day")
    if len(description.encode("utf-8")) > 5000:
        raise RuntimeError("YouTube description exceeds 5,000 UTF-8 bytes")
    if not hashtags or "#FPL" not in hashtags:
        raise RuntimeError("Day 2 hashtags must include #FPL")

    return {
        "video_date": parsed.isoformat(),
        "title": title,
        "description": description,
        "hashtags": hashtags,
        "tags": tags,
        "category_id": str(config.get("category_id") or "17"),
        "language": str(config.get("language") or "en-GB"),
        "recommended_visibility": "Private",
        "made_for_kids": False,
        "notify_subscribers_before_manual_publish": False,
        "day": 2,
    }


def build_package(
    output_root: Path,
    config_path: Path,
    video_date_override: str | None,
) -> dict[str, Any]:
    output_root = output_root.resolve()
    config = read_json(config_path.resolve(), "Day 2 metadata config")
    configured_date = str(video_date_override or config.get("video_date") or "").strip()
    if not configured_date:
        raise RuntimeError("Day 2 video_date is missing")

    video = required_file(output_root / "MP4" / VIDEO_NAME, "Day 2 review MP4")
    media_report = read_json(
        output_root / "Data" / MEDIA_REPORT_NAME,
        "Day 2 media QA",
    )
    if media_report.get("passed") is not True:
        raise RuntimeError("Day 2 media QA has not passed")

    probe = _probe_video(video)
    metadata = _render_metadata(config, configured_date)
    upload_ready = [probe["width"], probe["height"]] == [3840, 2160]

    package = {
        "version": "FPL-VORTEX-DAY2-YOUTUBE-PRIVATE-V1",
        "generated_at": utc_now(),
        "manual_upload_only": False,
        "youtube_api_upload_enabled": True,
        "automatic_publication_enabled": False,
        "upload_policy": dict(PRIVATE_POLICY),
        "upload_ready": upload_ready,
        "video": {
            "file": str(video),
            "resolution": [probe["width"], probe["height"]],
            "codec": probe["codec"],
            "fps": probe["fps"],
            "duration_seconds": probe["duration"],
        },
        "metadata": metadata,
        "studio_checklist": [
            "The GitHub workflow may upload this Day 2 MP4 only as Private.",
            "Review title, description, tags, thumbnail, and video in YouTube Studio.",
            "No workflow step may schedule or make the video Public or Unlisted.",
            "Change visibility manually in YouTube Studio only after review.",
        ],
    }
    package_path = output_root / "Data" / PACKAGE_NAME
    write_json_atomic(package_path, package)

    print("[DAY 2 YOUTUBE] Metadata package: PASS")
    print(f"[DAY 2 YOUTUBE] Title: {metadata['title']}")
    print("[DAY 2 YOUTUBE] Privacy: private")
    print(f"[DAY 2 YOUTUBE] Upload-ready 4K: {upload_ready}")
    print(f"[DAY 2 YOUTUBE] Package: {package_path}")
    return package


def _validate_package(output_root: Path) -> dict[str, Any]:
    package_path = output_root / "Data" / PACKAGE_NAME
    package = read_json(package_path, "Day 2 YouTube package")

    if package.get("manual_upload_only") is not False:
        raise RuntimeError("Day 2 package is not enabled for private API upload")
    if package.get("youtube_api_upload_enabled") is not True:
        raise RuntimeError("Day 2 YouTube API upload is not enabled")
    if package.get("automatic_publication_enabled") is not False:
        raise RuntimeError("Automatic YouTube publication must remain disabled")
    if package.get("upload_policy") != PRIVATE_POLICY:
        raise RuntimeError("Day 2 private-upload policy is invalid")
    if package.get("upload_ready") is not True:
        raise RuntimeError("Day 2 YouTube upload requires a verified 3840x2160 render")

    metadata = package.get("metadata")
    video = package.get("video")
    if not isinstance(metadata, dict) or not isinstance(video, dict):
        raise RuntimeError("Day 2 YouTube package is incomplete")
    if metadata.get("recommended_visibility") != "Private":
        raise RuntimeError("Day 2 visibility must be Private")
    if metadata.get("day") != 2:
        raise RuntimeError("Day 2 metadata identity is invalid")

    video_path = Path(str(video.get("file") or "")).resolve()
    expected_parent = (output_root / "MP4").resolve()
    if video_path.parent != expected_parent:
        raise RuntimeError("Day 2 package points outside the isolated MP4 directory")
    required_file(video_path, "Day 2 private-review MP4")
    if list(video.get("resolution") or []) != [3840, 2160]:
        raise RuntimeError("Day 2 YouTube package is not true 4K")

    marker = "fpl-vortex-auto-day2-" + re.sub(
        r"[^0-9]", "", str(metadata.get("video_date") or "")
    )
    upload_tags = [str(tag).strip() for tag in metadata.get("tags") or []]
    if marker not in upload_tags:
        upload_tags.append(marker)
    if sum(len(tag) for tag in upload_tags) + max(0, len(upload_tags) - 1) > 500:
        raise RuntimeError("YouTube tags exceed the 500-character limit")

    return {
        "package_path": package_path,
        "video_path": video_path,
        "metadata": metadata,
        "upload_tags": upload_tags,
        "marker": marker,
    }


def _youtube_service():
    try:
        from google.auth.transport.requests import Request as GoogleRequest
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError(
            "YouTube upload dependencies are missing; install google-api-python-client "
            "and google-auth"
        ) from exc

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
    granted_scopes = set(credentials.granted_scopes or ())
    if granted_scopes:
        required = {YOUTUBE_UPLOAD_SCOPE, YOUTUBE_READ_SCOPE}
        missing = required - granted_scopes
        if missing:
            raise RuntimeError(
                "YouTube refresh token is missing required OAuth scope(s): "
                + ", ".join(sorted(missing))
            )
    return build("youtube", "v3", credentials=credentials, cache_discovery=False)


def _verify_channel(youtube) -> dict[str, str]:
    expected_channel_id = _required_env("YOUTUBE_CHANNEL_ID")
    if not re.fullmatch(r"UC[A-Za-z0-9_-]{22}", expected_channel_id):
        raise RuntimeError("YOUTUBE_CHANNEL_ID has an invalid format")

    response = youtube.channels().list(
        part="id,snippet,contentDetails",
        mine=True,
        fields="items(id,snippet/title,contentDetails/relatedPlaylists/uploads)",
    ).execute(num_retries=5)
    items = response.get("items") or []
    if len(items) != 1:
        raise RuntimeError(
            f"Expected OAuth to resolve exactly one YouTube channel; found {len(items)}"
        )
    item = items[0]
    channel_id = str(item.get("id") or "")
    uploads_playlist = str(
        item.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads") or ""
    )
    if channel_id != expected_channel_id:
        raise RuntimeError(
            "YouTube OAuth channel mismatch; refusing Day 2 upload. "
            f"expected={expected_channel_id}, authenticated={channel_id}"
        )
    if not uploads_playlist:
        raise RuntimeError("Authenticated YouTube channel has no uploads playlist")
    return {
        "id": channel_id,
        "title": str(item.get("snippet", {}).get("title") or ""),
        "uploads_playlist": uploads_playlist,
    }


def _find_existing_upload(
    youtube,
    *,
    uploads_playlist: str,
    marker: str,
    title: str,
) -> dict[str, str] | None:
    playlist = youtube.playlistItems().list(
        part="contentDetails",
        playlistId=uploads_playlist,
        maxResults=50,
        fields="items/contentDetails/videoId",
    ).execute(num_retries=5)
    video_ids = [
        str(item.get("contentDetails", {}).get("videoId") or "")
        for item in playlist.get("items") or []
    ]
    video_ids = [video_id for video_id in video_ids if video_id]
    if not video_ids:
        return None

    response = youtube.videos().list(
        part="snippet,status",
        id=",".join(video_ids),
        maxResults=50,
        fields="items(id,snippet(title,tags),status/privacyStatus)",
    ).execute(num_retries=5)
    for item in response.get("items") or []:
        snippet = item.get("snippet") or {}
        item_title = str(snippet.get("title") or "")
        item_tags = [str(tag) for tag in snippet.get("tags") or []]
        if marker in item_tags or item_title == title:
            return {
                "video_id": str(item.get("id") or ""),
                "title": item_title,
                "privacy_status": str(
                    (item.get("status") or {}).get("privacyStatus") or "unknown"
                ),
                "match": "marker" if marker in item_tags else "exact_title",
            }
    return None


def _insert_private_video(youtube, package: dict[str, Any]) -> str:
    try:
        from googleapiclient.http import MediaFileUpload
    except ImportError as exc:
        raise RuntimeError("google-api-python-client is required for YouTube upload") from exc

    metadata = package["metadata"]
    body = {
        "snippet": {
            "title": metadata["title"],
            "description": metadata["description"],
            "tags": package["upload_tags"],
            "categoryId": metadata["category_id"],
            "defaultLanguage": metadata["language"],
        },
        "status": {
            "privacyStatus": YOUTUBE_PRIVACY_STATUS,
            "selfDeclaredMadeForKids": False,
        },
    }
    media = MediaFileUpload(
        str(package["video_path"]),
        mimetype="video/mp4",
        chunksize=8 * 1024 * 1024,
        resumable=True,
    )
    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
        notifySubscribers=False,
    )
    response = None
    while response is None:
        progress, response = request.next_chunk(num_retries=5)
        if progress is not None:
            print(
                f"[DAY 2 YOUTUBE] Private upload progress: "
                f"{progress.progress() * 100:.1f}%"
            )
    video_id = str((response or {}).get("id") or "")
    if not video_id:
        raise RuntimeError("Day 2 YouTube upload completed without a video ID")
    return video_id


def _confirm_private(youtube, video_id: str) -> None:
    response = youtube.videos().list(
        part="status",
        id=video_id,
        fields="items(id,status/privacyStatus)",
    ).execute(num_retries=5)
    items = response.get("items") or []
    if len(items) != 1:
        raise RuntimeError("Could not verify the uploaded Day 2 YouTube video")
    actual = str((items[0].get("status") or {}).get("privacyStatus") or "")
    if actual != YOUTUBE_PRIVACY_STATUS:
        raise RuntimeError(
            f"YouTube safety check failed: expected private, received {actual!r}"
        )


def upload_private(output_root: Path) -> dict[str, Any]:
    output_root = output_root.resolve()
    package = _validate_package(output_root)
    report_path = output_root / "Data" / REPORT_NAME
    report: dict[str, Any] = {
        "version": "FPL-VORTEX-DAY2-YOUTUBE-PRIVATE-UPLOAD-V1",
        "started_at": utc_now(),
        "status": "starting",
        "package_file": str(package["package_path"]),
        "safety": dict(PRIVATE_POLICY),
    }
    write_json_atomic(report_path, report)

    try:
        youtube = _youtube_service()
        channel = _verify_channel(youtube)
        report["channel"] = {"id": channel["id"], "title": channel["title"]}
        report["status"] = "channel_verified"
        write_json_atomic(report_path, report)

        existing = _find_existing_upload(
            youtube,
            uploads_playlist=channel["uploads_playlist"],
            marker=package["marker"],
            title=package["metadata"]["title"],
        )
        if existing:
            report.update(
                {
                    "status": "existing_private_upload_found",
                    "completed_at": utc_now(),
                    "duplicate_prevented": True,
                    "youtube": {
                        **existing,
                        "studio_url": (
                            f"https://studio.youtube.com/video/"
                            f"{existing['video_id']}/edit"
                        ),
                    },
                }
            )
            write_json_atomic(report_path, report)
            if existing["privacy_status"] != YOUTUBE_PRIVACY_STATUS:
                report["status"] = "existing_non_private_video_found"
                write_json_atomic(report_path, report)
                raise RuntimeError(
                    "A matching Day 2 YouTube video already exists and is not Private. "
                    "No duplicate was uploaded and the existing video was not changed."
                )
            print("[DAY 2 YOUTUBE] Existing Private Day 2 upload found; duplicate prevented")
            print(f"[DAY 2 YOUTUBE] Studio: {report['youtube']['studio_url']}")
            return report

        video_id = _insert_private_video(youtube, package)
        report.update(
            {
                "status": "private_video_uploaded_verification_pending",
                "youtube": {
                    "video_id": video_id,
                    "privacy_status": YOUTUBE_PRIVACY_STATUS,
                    "studio_url": f"https://studio.youtube.com/video/{video_id}/edit",
                },
            }
        )
        write_json_atomic(report_path, report)

        _confirm_private(youtube, video_id)
        report["status"] = "private_upload_complete"
        report["completed_at"] = utc_now()
        report["duplicate_prevented"] = False
        write_json_atomic(report_path, report)

        print("[DAY 2 YOUTUBE] PRIVATE upload: PASS")
        print(f"[DAY 2 YOUTUBE] Studio: {report['youtube']['studio_url']}")
        print("[DAY 2 YOUTUBE] Public/Unlisted/scheduled publishing: DISABLED")
        return report
    except Exception as exc:
        if report.get("status") != "existing_non_private_video_found":
            report["status"] = "failed"
        report["failed_at"] = utc_now()
        report["error"] = f"{type(exc).__name__}: {exc}"
        write_json_atomic(report_path, report)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build or upload the isolated Day 2 private YouTube package"
    )
    parser.add_argument(
        "command",
        choices=("build", "upload"),
        help="build validates metadata; upload performs a Private-only upload",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path(os.environ.get("DAY2_OUTPUT_ROOT", DEFAULT_OUTPUT_ROOT)),
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--video-date", default="")
    args = parser.parse_args()

    if args.command == "build":
        build_package(
            args.output_root,
            args.config,
            args.video_date.strip() or None,
        )
    else:
        upload_private(args.output_root)


if __name__ == "__main__":
    main()
