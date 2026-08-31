from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_OUTPUT_ROOT = Path("/content/drive/MyDrive/FPL_VORTEX/FIRST VIDEO")
PACKAGE_NAME = "youtube_video_1_of_3_package.json"
REPORT_NAME = "youtube_private_upload.json"
YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
YOUTUBE_READ_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
YOUTUBE_PRIVACY_STATUS = "private"
YOUTUBE_THUMBNAIL_MAX_BYTES = 2_000_000


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _required_file(path: Path, label: str) -> Path:
    if not path.is_file() or path.stat().st_size <= 0:
        raise FileNotFoundError(f"Required {label} is missing or empty: {path}")
    return path


def _read_json(path: Path, label: str) -> dict[str, Any]:
    _required_file(path, label)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Expected {label} to contain a JSON object: {path}")
    return payload


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _required_env(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise RuntimeError(f"Missing required GitHub secret: {name}")
    return value


def _oauth_value(youtube_name: str, google_name: str) -> str:
    value = (os.environ.get(youtube_name) or "").strip()
    if value:
        return value
    return _required_env(google_name)


def _safe_child(path_text: str, parent: Path, label: str) -> Path:
    candidate = Path(path_text)
    if not candidate.is_absolute():
        candidate = parent / candidate
    candidate = candidate.resolve()
    if candidate.parent != parent.resolve():
        raise RuntimeError(f"{label} points outside {parent}: {candidate}")
    return _required_file(candidate, label)


def _upload_marker(preview_gw: int) -> str:
    return f"fpl-vortex-auto-gw{preview_gw:02d}-video-1-of-3"


def _validate_package(output_root: Path) -> dict[str, Any]:
    package_path = output_root / "DATA" / PACKAGE_NAME
    package = _read_json(package_path, "YouTube package")

    if package.get("manual_upload_only") is not False:
        raise RuntimeError("YouTube package is not enabled for automatic private upload")
    if package.get("youtube_api_upload_enabled") is not True:
        raise RuntimeError("YouTube API upload is not enabled in the package")
    if package.get("automatic_publication_enabled") is not False:
        raise RuntimeError("Automatic YouTube publication must remain disabled")

    policy = package.get("upload_policy")
    expected_policy = {
        "privacy_status": YOUTUBE_PRIVACY_STATUS,
        "notify_subscribers": False,
        "automatic_publish": False,
        "automatic_schedule": False,
        "manual_publication_required": True,
    }
    if policy != expected_policy:
        raise RuntimeError(
            f"YouTube private-upload policy mismatch: {policy!r} != {expected_policy!r}"
        )

    metadata = package.get("metadata")
    video = package.get("video")
    thumbnail = package.get("thumbnail")
    if not isinstance(metadata, dict):
        raise RuntimeError("YouTube package metadata is missing")
    if not isinstance(video, dict) or not isinstance(thumbnail, dict):
        raise RuntimeError("YouTube package video or thumbnail data is missing")

    preview_gw = int(metadata.get("preview_gw") or 0)
    review_gw = int(metadata.get("review_gw") or 0)
    if not 1 <= preview_gw <= 38 or not 1 <= review_gw <= 38:
        raise RuntimeError(
            f"Invalid dynamic Gameweeks: preview={preview_gw}, review={review_gw}"
        )
    if metadata.get("recommended_visibility") != "Private":
        raise RuntimeError("YouTube package visibility must be Private")
    if metadata.get("notify_subscribers_before_manual_publish") is not False:
        raise RuntimeError("Subscriber notifications must remain disabled")
    if metadata.get("made_for_kids") is not False:
        raise RuntimeError("Unexpected made-for-kids setting")
    if metadata.get("series") != {"part": 1, "total": 3}:
        raise RuntimeError("YouTube package must be VIDEO 1/3")

    title = str(metadata.get("title") or "").strip()
    description = str(metadata.get("description") or "").strip()
    tags = metadata.get("tags")
    category_id = str(metadata.get("category_id") or "").strip()
    language = str(metadata.get("language") or "").strip()
    if not title or len(title) > 100 or f"FPL GW{preview_gw}" not in title:
        raise RuntimeError("YouTube title is missing, too long, or has the wrong Gameweek")
    if not description or len(description.encode("utf-8")) > 5_000:
        raise RuntimeError("YouTube description is missing or too long")
    if not isinstance(tags, list) or not tags or not all(
        isinstance(tag, str) and tag.strip() for tag in tags
    ):
        raise RuntimeError("YouTube tags are missing or invalid")
    if category_id != "17" or not language:
        raise RuntimeError("YouTube Sports category or language is invalid")

    marker = _upload_marker(preview_gw)
    upload_tags = [str(tag).strip() for tag in tags]
    if marker not in upload_tags:
        upload_tags.append(marker)
    if sum(len(tag) for tag in upload_tags) + len(upload_tags) - 1 > 500:
        raise RuntimeError("YouTube tags exceed the 500-character limit")

    visible_copy = " ".join(
        [title, str(thumbnail.get("series_badge") or ""), str(thumbnail.get("headline") or "")]
    )
    if re.search(r"\bDAY\s*\d", visible_copy, flags=re.IGNORECASE):
        raise RuntimeError("YouTube title or thumbnail must not use Day numbering")
    if thumbnail.get("series_badge") != "VIDEO 1/3":
        raise RuntimeError("YouTube thumbnail is missing the VIDEO 1/3 badge")

    video_path = _safe_child(str(video.get("file") or ""), output_root / "MP4", "4K MP4")
    thumbnail_path = _safe_child(
        str(thumbnail.get("file") or ""),
        output_root / "SLIDE",
        "4K thumbnail",
    )
    if list(video.get("resolution") or []) != [3840, 2160]:
        raise RuntimeError("YouTube upload package is not true 4K")
    if list(thumbnail.get("resolution") or []) != [3840, 2160]:
        raise RuntimeError("YouTube thumbnail package is not true 4K")
    if thumbnail_path.stat().st_size > YOUTUBE_THUMBNAIL_MAX_BYTES:
        raise RuntimeError("YouTube thumbnail exceeds the official 2 MB limit")

    return {
        "package_path": package_path,
        "video_path": video_path,
        "thumbnail_path": thumbnail_path,
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
        required_scopes = {YOUTUBE_UPLOAD_SCOPE, YOUTUBE_READ_SCOPE}
        missing = required_scopes - granted_scopes
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
    channel_title = str(item.get("snippet", {}).get("title") or "")
    uploads_playlist = str(
        item.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads") or ""
    )
    if channel_id != expected_channel_id:
        raise RuntimeError(
            "YouTube OAuth channel mismatch; refusing to upload. "
            f"expected={expected_channel_id}, authenticated={channel_id}"
        )
    if not uploads_playlist:
        raise RuntimeError("Authenticated YouTube channel has no uploads playlist")
    return {
        "id": channel_id,
        "title": channel_title,
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
            print(f"[YOUTUBE] Private upload progress: {progress.progress() * 100:.1f}%")
    video_id = str((response or {}).get("id") or "")
    if not video_id:
        raise RuntimeError("YouTube upload completed without a video ID")
    return video_id


def _set_thumbnail(youtube, *, video_id: str, thumbnail_path: Path) -> None:
    from googleapiclient.http import MediaFileUpload

    youtube.thumbnails().set(
        videoId=video_id,
        media_body=MediaFileUpload(
            str(thumbnail_path),
            mimetype="image/jpeg",
            resumable=False,
        ),
    ).execute(num_retries=5)


def _confirm_private(youtube, video_id: str) -> None:
    response = youtube.videos().list(
        part="status",
        id=video_id,
        fields="items(id,status/privacyStatus)",
    ).execute(num_retries=5)
    items = response.get("items") or []
    if len(items) != 1:
        raise RuntimeError("Could not verify the uploaded YouTube video")
    actual = str((items[0].get("status") or {}).get("privacyStatus") or "")
    if actual != YOUTUBE_PRIVACY_STATUS:
        raise RuntimeError(
            f"YouTube safety check failed: expected private, received {actual!r}"
        )


def upload_private(output_root: Path) -> dict[str, Any]:
    output_root = output_root.resolve()
    package = _validate_package(output_root)
    report_path = output_root / "DATA" / REPORT_NAME
    report: dict[str, Any] = {
        "version": "FPL-VORTEX-YOUTUBE-PRIVATE-UPLOAD-V1",
        "started_at": _utc_now(),
        "status": "starting",
        "package_file": str(package["package_path"]),
        "safety": {
            "requested_privacy_status": YOUTUBE_PRIVACY_STATUS,
            "notify_subscribers": False,
            "automatic_publish": False,
            "automatic_schedule": False,
            "manual_publication_required": True,
        },
    }
    _write_json_atomic(report_path, report)

    try:
        youtube = _youtube_service()
        channel = _verify_channel(youtube)
        report["channel"] = {"id": channel["id"], "title": channel["title"]}
        report["status"] = "channel_verified"
        _write_json_atomic(report_path, report)

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
                    "completed_at": _utc_now(),
                    "duplicate_prevented": True,
                    "youtube": {
                        **existing,
                        "studio_url": (
                            f"https://studio.youtube.com/video/{existing['video_id']}/edit"
                        ),
                    },
                }
            )
            _write_json_atomic(report_path, report)
            if existing["privacy_status"] != YOUTUBE_PRIVACY_STATUS:
                report["status"] = "existing_non_private_video_found"
                _write_json_atomic(report_path, report)
                raise RuntimeError(
                    "A matching YouTube video already exists and is not Private. "
                    "No duplicate was uploaded and the existing video was not changed."
                )
            print("[YOUTUBE] Existing Private VIDEO 1/3 found; duplicate upload prevented")
            print(f"[YOUTUBE] Studio: {report['youtube']['studio_url']}")
            return report

        video_id = _insert_private_video(youtube, package)
        report.update(
            {
                "status": "private_video_uploaded_thumbnail_pending",
                "youtube": {
                    "video_id": video_id,
                    "privacy_status": YOUTUBE_PRIVACY_STATUS,
                    "studio_url": f"https://studio.youtube.com/video/{video_id}/edit",
                    "watch_url": f"https://www.youtube.com/watch?v={video_id}",
                    "thumbnail_set": False,
                },
            }
        )
        _write_json_atomic(report_path, report)

        _set_thumbnail(
            youtube,
            video_id=video_id,
            thumbnail_path=package["thumbnail_path"],
        )
        _confirm_private(youtube, video_id)
        report["youtube"]["thumbnail_set"] = True
        report["status"] = "private_upload_complete"
        report["completed_at"] = _utc_now()
        report["duplicate_prevented"] = False
        _write_json_atomic(report_path, report)
        print("[YOUTUBE] PRIVATE upload and dynamic thumbnail: PASS")
        print(f"[YOUTUBE] Studio: {report['youtube']['studio_url']}")
        print("[YOUTUBE] Automatic Public/Unlisted/scheduled publishing: DISABLED")
        return report
    except Exception as exc:
        if report.get("status") != "existing_non_private_video_found":
            report["status"] = "failed"
        report["failed_at"] = _utc_now()
        report["error"] = f"{type(exc).__name__}: {exc}"
        _write_json_atomic(report_path, report)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Upload the validated FPL VORTEX VIDEO 1/3 package to the verified "
            "YouTube channel as Private only"
        )
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path(os.environ.get("DAY1_OUTPUT_ROOT", DEFAULT_OUTPUT_ROOT)),
    )
    args = parser.parse_args()
    upload_private(args.output_root)


if __name__ == "__main__":
    main()
