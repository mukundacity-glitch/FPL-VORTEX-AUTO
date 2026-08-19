from __future__ import annotations

import mimetypes
import os
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from .config import LOG_ROOT, MASTER_ROOT, MODEL_ROOT, RAW_ROOT, SNAPSHOT_ROOT

# Existing Google Drive folder already created by the VORTEX Data Builder.
DRIVE_ROOT_ID = "1L1nZL0g2FsQ4r4CDyetlK68BT6--ChJ4"
DRIVE_FOLDER_MIME = "application/vnd.google-apps.folder"
TOKEN_URI = "https://oauth2.googleapis.com/token"

# Full Drive scope is intentional here: this private updater must be able to
# access the user's existing FPL_VORTEX_DATA folder, not only files originally
# created by this OAuth client.
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


def _upload_file(service, local_path: Path, parent_id: str) -> str:
    mime_type = mimetypes.guess_type(local_path.name)[0] or "application/octet-stream"
    media = MediaFileUpload(str(local_path), mimetype=mime_type, resumable=True)
    existing = _find_child(service, parent_id, local_path.name)

    if existing:
        updated = service.files().update(
            fileId=existing["id"],
            media_body=media,
            fields="id",
        ).execute()
        return str(updated["id"])

    created = service.files().create(
        body={"name": local_path.name, "parents": [parent_id]},
        media_body=media,
        fields="id",
    ).execute()
    return str(created["id"])


def _sync_tree(service, local_root: Path, drive_parts: list[str]) -> int:
    if not local_root.exists():
        return 0

    uploaded = 0
    base_parent = _ensure_path(service, DRIVE_ROOT_ID, drive_parts)

    for path in sorted(local_root.rglob("*")):
        if not path.is_file():
            continue

        relative_parent = path.parent.relative_to(local_root)
        parent = base_parent
        if relative_parent.parts:
            parent = _ensure_path(
                service,
                base_parent,
                list(relative_parent.parts),
            )

        _upload_file(service, path, parent)
        uploaded += 1

    return uploaded


def sync_vortex_to_drive() -> dict[str, int]:
    """Sync generated VORTEX runtime outputs into the existing Drive vault."""
    service = _service()

    raw_count = _sync_tree(
        service,
        RAW_ROOT,
        ["01_RAW", "AUTO_UPDATER"],
    )

    master_count = _sync_tree(
        service,
        MASTER_ROOT,
        ["04_MASTER", "AUTO_UPDATER"],
    )

    model_count = _sync_tree(
        service,
        MODEL_ROOT,
        ["05_MODEL_HISTORY", "AUTO_UPDATER"],
    )

    snapshot_count = _sync_tree(
        service,
        SNAPSHOT_ROOT,
        ["06_SNAPSHOTS", "AUTO_UPDATER"],
    )

    log_count = _sync_tree(
        service,
        LOG_ROOT,
        ["07_LOGS", "AUTO_UPDATER"],
    )

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
