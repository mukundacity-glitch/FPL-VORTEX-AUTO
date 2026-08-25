from __future__ import annotations

import mimetypes
import os
import shutil
import uuid
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

from .config import LOG_ROOT, MASTER_ROOT, MODEL_ROOT, RAW_ROOT, SNAPSHOT_ROOT

# Existing Google Drive folder already created by the VORTEX Data Builder.
DRIVE_ROOT_ID = "1L1nZL0g2FsQ4r4CDyetlK68BT6--ChJ4"
DRIVE_FOLDER_MIME = "application/vnd.google-apps.folder"
TOKEN_URI = "https://oauth2.googleapis.com/token"
MY_DRIVE_ROOT_ID = "root"

# Full Drive scope is intentional here: this private updater must be able to
# access the user's existing VORTEX folders, not only files created by this client.
SCOPES = ["https://www.googleapis.com/auth/drive"]


def _credentials() -> Credentials:
    client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
    refresh_token = os.environ.get("GOOGLE_OAUTH_REFRESH_TOKEN")

    missing = [
        name
        for name, value in {
            "GOOGLE_OAUTH_CLIENT_ID": client_id,
            "GOOGLE_OAUTH_CLIENT_SECRET": client_secret,
            "GOOGLE_OAUTH_REFRESH_TOKEN": refresh_token,
        }.items()
        if not value
    ]
    if missing:
        raise RuntimeError(
            "Google Drive authentication is not configured. Missing GitHub secret(s): "
            + ", ".join(missing)
        )

    return Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=TOKEN_URI,
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )


def _service():
    return build("drive", "v3", credentials=_credentials(), cache_discovery=False)


def _escape(value: str) -> str:
    return value.replace("'", "\\'")


def _find_child(service, parent_id: str, name: str, mime_type: str | None = None):
    parts = [
        f"'{parent_id}' in parents",
        "trashed = false",
        f"name = '{_escape(name)}'",
    ]
    if mime_type:
        parts.append(f"mimeType = '{mime_type}'")

    result = service.files().list(
        q=" and ".join(parts),
        spaces="drive",
        fields="files(id,name,mimeType,modifiedTime,size)",
        pageSize=10,
    ).execute()
    files = result.get("files", [])
    return files[0] if files else None


def _list_children(service, parent_id: str) -> list[dict]:
    files: list[dict] = []
    page_token = None
    while True:
        result = service.files().list(
            q=f"'{parent_id}' in parents and trashed = false",
            spaces="drive",
            fields="nextPageToken,files(id,name,mimeType,modifiedTime,size)",
            pageSize=1000,
            pageToken=page_token,
        ).execute()
        files.extend(result.get("files", []))
        page_token = result.get("nextPageToken")
        if not page_token:
            return files


def _ensure_folder(service, parent_id: str, name: str) -> str:
    existing = _find_child(service, parent_id, name, DRIVE_FOLDER_MIME)
    if existing:
        return str(existing["id"])

    created = service.files().create(
        body={
            "name": name,
            "mimeType": DRIVE_FOLDER_MIME,
            "parents": [parent_id],
        },
        fields="id",
    ).execute()
    return str(created["id"])


def _ensure_path(service, root_id: str, parts: list[str]) -> str:
    parent = root_id
    for part in parts:
        parent = _ensure_folder(service, parent, part)
    return parent


def _resolve_folder(service, root_id: str, parts: list[str]) -> str | None:
    parent = root_id
    for part in parts:
        found = _find_child(service, parent, part, DRIVE_FOLDER_MIME)
        if not found:
            return None
        parent = str(found["id"])
    return parent


def _upload_file(service, local_path: Path, parent_id: str) -> str:
    mime_type = mimetypes.guess_type(local_path.name)[0] or "application/octet-stream"
    media = MediaFileUpload(str(local_path), mimetype=mime_type, resumable=True)
    existing = _find_child(service, parent_id, local_path.name)

    if existing:
        request = service.files().update(
            fileId=existing["id"],
            media_body=media,
            fields="id",
        )
    else:
        request = service.files().create(
            body={"name": local_path.name, "parents": [parent_id]},
            media_body=media,
            fields="id",
        )

    response = None
    while response is None:
        _, response = request.next_chunk()
    return str(response["id"])


def _sync_tree_to_parent(service, local_root: Path, base_parent: str) -> int:
    if not local_root.exists():
        return 0

    uploaded = 0
    for path in sorted(local_root.rglob("*")):
        if not path.is_file():
            continue
        relative_parent = path.parent.relative_to(local_root)
        parent = base_parent
        if relative_parent.parts:
            parent = _ensure_path(service, base_parent, list(relative_parent.parts))
        _upload_file(service, path, parent)
        uploaded += 1
    return uploaded


def _sync_tree(service, local_root: Path, drive_parts: list[str], root_id: str = DRIVE_ROOT_ID) -> int:
    if not local_root.exists():
        return 0
    base_parent = _ensure_path(service, root_id, drive_parts)
    return _sync_tree_to_parent(service, local_root, base_parent)


def _download_file(service, file_id: str, local_path: Path) -> None:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    request = service.files().get_media(fileId=file_id)
    with local_path.open("wb") as handle:
        downloader = MediaIoBaseDownload(handle, request, chunksize=8 * 1024 * 1024)
        done = False
        while not done:
            _, done = downloader.next_chunk()


def _download_tree_from_parent(service, parent_id: str, local_root: Path) -> int:
    local_root.mkdir(parents=True, exist_ok=True)
    count = 0
    stack = [(parent_id, local_root)]
    while stack:
        remote_parent, local_parent = stack.pop()
        for item in _list_children(service, remote_parent):
            local_path = local_parent / item["name"]
            if item["mimeType"] == DRIVE_FOLDER_MIME:
                local_path.mkdir(parents=True, exist_ok=True)
                stack.append((str(item["id"]), local_path))
                continue
            if str(item["mimeType"]).startswith("application/vnd.google-apps."):
                raise RuntimeError(
                    f"Unsupported Google-native file in VORTEX runtime input: {item['name']}"
                )
            _download_file(service, str(item["id"]), local_path)
            count += 1
    return count


def _download_tree(
    service,
    drive_parts: list[str],
    local_root: Path,
    *,
    required: bool = False,
    root_id: str = MY_DRIVE_ROOT_ID,
) -> int:
    folder_id = _resolve_folder(service, root_id, drive_parts)
    if folder_id is None:
        if required:
            raise RuntimeError("Required Google Drive folder is missing: My Drive/" + "/".join(drive_parts))
        local_root.mkdir(parents=True, exist_ok=True)
        return 0
    return _download_tree_from_parent(service, folder_id, local_root)


def _local_tree_stats(root: Path) -> tuple[int, int]:
    files = [p for p in root.rglob("*") if p.is_file()]
    return len(files), sum(p.stat().st_size for p in files)


def _remote_tree_stats(service, parent_id: str) -> tuple[int, int]:
    count = 0
    total = 0
    stack = [parent_id]
    while stack:
        remote_parent = stack.pop()
        for item in _list_children(service, remote_parent):
            if item["mimeType"] == DRIVE_FOLDER_MIME:
                stack.append(str(item["id"]))
            else:
                count += 1
                total += int(item.get("size") or 0)
    return count, total


def _trash(service, file_id: str) -> None:
    service.files().update(fileId=file_id, body={"trashed": True}, fields="id").execute()


def _replace_first_video(service, local_first_video: Path) -> dict[str, int]:
    project_id = _ensure_path(service, MY_DRIVE_ROOT_ID, ["FPL_VORTEX"])

    # Remove leftovers from an interrupted older staging attempt only.
    for item in _list_children(service, project_id):
        name = str(item.get("name", ""))
        if name.startswith(".FPL_VORTEX_FIRST_VIDEO_STAGING_") or name.startswith("FIRST VIDEO.__previous__"):
            _trash(service, str(item["id"]))

    staging_name = ".FPL_VORTEX_FIRST_VIDEO_STAGING_" + uuid.uuid4().hex
    staging_id = _ensure_folder(service, project_id, staging_name)
    uploaded = _sync_tree_to_parent(service, local_first_video, staging_id)

    local_stats = _local_tree_stats(local_first_video)
    remote_stats = _remote_tree_stats(service, staging_id)
    if local_stats != remote_stats:
        _trash(service, staging_id)
        raise RuntimeError(
            "Google Drive staging verification failed: "
            f"local={local_stats}, remote={remote_stats}"
        )

    current = _find_child(service, project_id, "FIRST VIDEO", DRIVE_FOLDER_MIME)
    backup_id = None
    if current:
        backup_id = str(current["id"])
        service.files().update(
            fileId=backup_id,
            body={"name": "FIRST VIDEO.__previous__" + uuid.uuid4().hex},
            fields="id",
        ).execute()

    try:
        service.files().update(
            fileId=staging_id,
            body={"name": "FIRST VIDEO"},
            fields="id",
        ).execute()
    except Exception:
        if backup_id:
            service.files().update(fileId=backup_id, body={"name": "FIRST VIDEO"}, fields="id").execute()
        raise

    if backup_id:
        _trash(service, backup_id)

    return {"files_uploaded": uploaded, "bytes_uploaded": local_stats[1]}


def restore_weekly_from_drive(local_root: Path | str = "output") -> dict[str, int]:
    """Restore persistent inputs/state needed by the weekly notebook into the runner."""
    service = _service()
    local_root = Path(local_root).resolve()
    my_drive = local_root / "MyDrive"
    if my_drive.exists():
        shutil.rmtree(my_drive)
    my_drive.mkdir(parents=True, exist_ok=False)

    mappings = [
        (["FPL_VORTEX", "MODEL_INPUTS"], my_drive / "FPL_VORTEX" / "MODEL_INPUTS", False),
        (["FPL_VORTEX", "DECISION_ENGINE"], my_drive / "FPL_VORTEX" / "DECISION_ENGINE", False),
        (["FPL_VORTEX", "RAW_DATA"], my_drive / "FPL_VORTEX" / "RAW_DATA", False),
        (["FPL_VORTEX", "MODEL_HISTORY"], my_drive / "FPL_VORTEX" / "MODEL_HISTORY", False),
        (["FPL_VORTEX", "elements"], my_drive / "FPL_VORTEX" / "elements", False),
        (["elements"], my_drive / "elements", False),
        (["FPL_VORTEX_DATA", "00_SOURCE_ARCHIVE", "FPL_CORE", "data"], my_drive / "FPL_VORTEX_DATA" / "00_SOURCE_ARCHIVE" / "FPL_CORE" / "data", True),
        (["FPL_VORTEX_DATA", "03_ELITE_MANAGERS"], my_drive / "FPL_VORTEX_DATA" / "03_ELITE_MANAGERS", False),
        (["FPL_VORTEX_DATA", "04_MASTER"], my_drive / "FPL_VORTEX_DATA" / "04_MASTER", False),
        (["FPL_VORTEX_DATA", "05_MODEL_HISTORY"], my_drive / "FPL_VORTEX_DATA" / "05_MODEL_HISTORY", False),
        (["FPL_VORTEX_DATA", "06_SNAPSHOTS"], my_drive / "FPL_VORTEX_DATA" / "06_SNAPSHOTS", False),
        (["FPL_VORTEX_DATA", "07_LOGS"], my_drive / "FPL_VORTEX_DATA" / "07_LOGS", False),
    ]

    report: dict[str, int] = {}
    for drive_parts, destination, required in mappings:
        count = _download_tree(service, drive_parts, destination, required=required)
        report["/".join(drive_parts)] = count
        print(f"[VORTEX] Restored My Drive/{'/'.join(drive_parts)}: {count} file(s)")
    return report


def validate_weekly_outputs(local_root: Path | str = "output") -> dict[str, object]:
    local_root = Path(local_root).resolve()
    first_video = local_root / "MyDrive" / "FPL_VORTEX" / "FIRST VIDEO"
    expected_dirs = {"MP4", "MP3", "SLIDE", "DATA"}
    if not first_video.is_dir():
        raise RuntimeError(f"Weekly output root is missing: {first_video}")
    actual_dirs = {p.name for p in first_video.iterdir() if p.is_dir()}
    root_files = [p.name for p in first_video.iterdir() if p.is_file()]
    if actual_dirs != expected_dirs or root_files:
        raise RuntimeError(
            f"Weekly output structure invalid: dirs={sorted(actual_dirs)}, root_files={root_files}"
        )

    mp4 = first_video / "MP4"
    mp3 = first_video / "MP3"
    slides = first_video / "SLIDE"
    data = first_video / "DATA"
    for path in (mp4, mp3, slides, data):
        if not any(p.is_file() for p in path.rglob("*")):
            raise RuntimeError(f"Expected output directory is empty: {path}")

    final_mp4s = [p for p in mp4.glob("*COMBINED*.mp4") if p.is_file()]
    final_mp3s = [p for p in mp3.glob("*COMBINED*.mp3") if p.is_file()]
    qa_file = data / "final_video_qa.json"
    if len(final_mp4s) != 1:
        raise RuntimeError(f"Expected exactly one combined final MP4, found {len(final_mp4s)}")
    if len(final_mp3s) != 1:
        raise RuntimeError(f"Expected exactly one combined final MP3, found {len(final_mp3s)}")
    if not qa_file.is_file():
        raise RuntimeError("Missing DATA/final_video_qa.json")

    import json

    qa = json.loads(qa_file.read_text(encoding="utf-8"))
    if qa.get("passed") is not True or qa.get("output_contract", {}).get("passed") is not True:
        raise RuntimeError("Final publish QA did not pass")
    qa_final = Path(str(qa.get("final_file", ""))).name
    if qa_final != final_mp4s[0].name:
        raise RuntimeError("Final QA points to a different MP4 than the generated master")

    report = {
        "first_video": str(first_video),
        "final_mp4": final_mp4s[0].name,
        "final_mp3": final_mp3s[0].name,
        "slides": len([p for p in slides.rglob("*") if p.is_file()]),
        "data_files": len([p for p in data.rglob("*") if p.is_file()]),
        "video_output": qa.get("video_output"),
        "fps": qa.get("fps"),
    }
    print(f"[VORTEX] Weekly output validation PASS: {report}")
    return report


def publish_weekly_to_drive(local_root: Path | str = "output") -> dict[str, object]:
    """Publish validated weekly outputs and updated persistent state back to My Drive."""
    local_root = Path(local_root).resolve()
    validate_weekly_outputs(local_root)
    service = _service()
    my_drive = local_root / "MyDrive"

    persistent = [
        (my_drive / "FPL_VORTEX" / "MODEL_INPUTS", ["FPL_VORTEX", "MODEL_INPUTS"]),
        (my_drive / "FPL_VORTEX" / "DECISION_ENGINE", ["FPL_VORTEX", "DECISION_ENGINE"]),
        (my_drive / "FPL_VORTEX" / "RAW_DATA", ["FPL_VORTEX", "RAW_DATA"]),
        (my_drive / "FPL_VORTEX" / "MODEL_HISTORY", ["FPL_VORTEX", "MODEL_HISTORY"]),
        (my_drive / "FPL_VORTEX_DATA" / "05_MODEL_HISTORY", ["FPL_VORTEX_DATA", "05_MODEL_HISTORY"]),
        (my_drive / "FPL_VORTEX_DATA" / "06_SNAPSHOTS", ["FPL_VORTEX_DATA", "06_SNAPSHOTS"]),
        (my_drive / "FPL_VORTEX_DATA" / "07_LOGS", ["FPL_VORTEX_DATA", "07_LOGS"]),
    ]
    persistent_report: dict[str, int] = {}
    for local_path, drive_parts in persistent:
        count = _sync_tree(service, local_path, drive_parts, root_id=MY_DRIVE_ROOT_ID)
        persistent_report["/".join(drive_parts)] = count

    output_report = _replace_first_video(
        service,
        my_drive / "FPL_VORTEX" / "FIRST VIDEO",
    )
    report: dict[str, object] = {
        "persistent": persistent_report,
        "first_video": output_report,
    }
    print(f"[VORTEX] Weekly Google Drive publish PASS: {report}")
    return report


def sync_vortex_to_drive() -> dict[str, int]:
    """Sync generated VORTEX data-builder outputs into the existing Drive vault."""
    service = _service()

    raw_count = _sync_tree(service, RAW_ROOT, ["01_RAW", "AUTO_UPDATER"])
    master_count = _sync_tree(service, MASTER_ROOT, ["04_MASTER", "AUTO_UPDATER"])
    model_count = _sync_tree(service, MODEL_ROOT, ["05_MODEL_HISTORY", "AUTO_UPDATER"])
    snapshot_count = _sync_tree(service, SNAPSHOT_ROOT, ["06_SNAPSHOTS", "AUTO_UPDATER"])
    log_count = _sync_tree(service, LOG_ROOT, ["07_LOGS", "AUTO_UPDATER"])

    print(
        "[VORTEX] Google Drive sync complete | "
        f"raw={raw_count} master={master_count} model={model_count} "
        f"snapshots={snapshot_count} logs={log_count}"
    )
    return {
        "raw_files_uploaded_or_updated": raw_count,
        "master_files_uploaded_or_updated": master_count,
        "model_files_uploaded_or_updated": model_count,
        "snapshot_files_uploaded_or_updated": snapshot_count,
        "log_files_uploaded_or_updated": log_count,
    }
