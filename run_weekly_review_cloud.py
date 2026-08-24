from __future__ import annotations

import ast
import asyncio
import io
import json
import mimetypes
import os
import shutil
import sys
import traceback
import types
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

# -----------------------------------------------------------------------------
# FPL VORTEX WEEKLY REVIEW - GitHub Actions cloud runner
# -----------------------------------------------------------------------------
# The Drive notebook remains the source of truth. This runner downloads the
# latest saved notebook, mirrors only the persistent state it needs into a fake
# /content/drive/MyDrive tree, executes the notebook cell-by-cell (including
# top-level await cells), then syncs the finished output back to the SAME Drive
# folder used by Colab: MyDrive/FPL_VORTEX/First Day Video.
# -----------------------------------------------------------------------------

NOTEBOOK_FILE_ID = "10NrZhXWzEAEi78T6koxxr97MTIp6v6RV"
DRIVE_PROJECT_ROOT_ID = "1HwNnSQXgizV8Z4HzICPSvR5YJcbyi-aE"  # MyDrive/FPL_VORTEX
DRIVE_DATA_ROOT_ID = "1L1nZL0g2FsQ4r4CDyetlK68BT6--ChJ4"  # MyDrive/FPL_VORTEX_DATA

# Branding assets used by the notebook.
DRIVE_LOGO_FILE_ID = "1A_Lf3s6iW1XjB7pN6oSDIgzT9KlsFn5R"
DRIVE_PL_FILE_ID = "1RNkk3uObj62NW76rhMaT8jAuHMCB4rjY"

# Same FPL-Core revision used by the standalone phone build / Drive archive.
FPL_CORE_REPO = "olbauday/FPL-Core-Insights"
FPL_CORE_COMMIT = "087ae45c2b58dcbf0542cacff1e59e913e73e577"

TOKEN_URI = "https://oauth2.googleapis.com/token"
SCOPES = ["https://www.googleapis.com/auth/drive"]
DRIVE_FOLDER_MIME = "application/vnd.google-apps.folder"

LOCAL_CONTENT = Path("/content")
LOCAL_MYDRIVE = LOCAL_CONTENT / "drive" / "MyDrive"
LOCAL_PROJECT = LOCAL_MYDRIVE / "FPL_VORTEX"
LOCAL_DATA = LOCAL_MYDRIVE / "FPL_VORTEX_DATA"
LOCAL_ELEMENTS = LOCAL_MYDRIVE / "elements"
LOCAL_RUN = Path(".cloud_run")
LOCAL_NOTEBOOK = LOCAL_RUN / "FPL_VORTEX_WEEKLY_LATEST.ipynb"
LOCAL_REPORT = LOCAL_RUN / "weekly_cloud_run_report.json"

# Persistent Drive state needed by the weekly model/governance layers.
DATA_FOLDERS_TO_MIRROR = (
    "03_ELITE_MANAGERS",
    "04_MASTER",
    "05_MODEL_HISTORY",
    "06_SNAPSHOTS",
    "07_LOGS",
)
PROJECT_FOLDERS_TO_SYNC = (
    "MODEL_INPUTS",
    "RAW_DATA",
    "MODEL_HISTORY",
    "DECISION_ENGINE",
)
DATA_FOLDERS_TO_SYNC = (
    "05_MODEL_HISTORY",
    "06_SNAPSHOTS",
    "07_LOGS",
)


def log(message: str = "") -> None:
    print(message, flush=True)


def credentials() -> Credentials:
    names = (
        "GOOGLE_OAUTH_CLIENT_ID",
        "GOOGLE_OAUTH_CLIENT_SECRET",
        "GOOGLE_OAUTH_REFRESH_TOKEN",
    )
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        raise RuntimeError("Missing GitHub secret(s): " + ", ".join(missing))
    return Credentials(
        token=None,
        refresh_token=os.environ["GOOGLE_OAUTH_REFRESH_TOKEN"],
        token_uri=TOKEN_URI,
        client_id=os.environ["GOOGLE_OAUTH_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],
        scopes=SCOPES,
    )


def drive_service():
    return build("drive", "v3", credentials=credentials(), cache_discovery=False)


def escape_q(value: str) -> str:
    return value.replace("'", "\\'")


def list_children(service, parent_id: str):
    token = None
    while True:
        result = service.files().list(
            q=f"'{parent_id}' in parents and trashed = false",
            spaces="drive",
            fields="nextPageToken,files(id,name,mimeType,size,modifiedTime)",
            pageSize=1000,
            pageToken=token,
        ).execute()
        yield from result.get("files", [])
        token = result.get("nextPageToken")
        if not token:
            break


def find_child(service, parent_id: str, name: str, mime_type: str | None = None):
    parts = [
        f"'{parent_id}' in parents",
        "trashed = false",
        f"name = '{escape_q(name)}'",
    ]
    if mime_type:
        parts.append(f"mimeType = '{mime_type}'")
    result = service.files().list(
        q=" and ".join(parts),
        spaces="drive",
        fields="files(id,name,mimeType,size,modifiedTime)",
        pageSize=20,
    ).execute()
    files = result.get("files", [])
    return files[0] if files else None


def download_file(service, file_id: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = service.files().get_media(fileId=file_id)
    with destination.open("wb") as fh:
        downloader = MediaIoBaseDownload(fh, request, chunksize=8 * 1024 * 1024)
        done = False
        while not done:
            _, done = downloader.next_chunk()


def download_folder(service, folder_id: str, destination: Path, *, skip_names=()) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    skip = set(skip_names)
    for item in list_children(service, folder_id):
        name = str(item["name"])
        if name in skip:
            continue
        target = destination / name
        if item["mimeType"] == DRIVE_FOLDER_MIME:
            download_folder(service, item["id"], target)
        elif str(item["mimeType"]).startswith("application/vnd.google-apps."):
            # Weekly runtime state is stored as ordinary files. Native editor
            # files are intentionally not materialized into the fake mount.
            log(f"[CLOUD] Skip native Drive file: {target}")
        else:
            download_file(service, item["id"], target)


def ensure_folder(service, parent_id: str, name: str) -> str:
    existing = find_child(service, parent_id, name, DRIVE_FOLDER_MIME)
    if existing:
        return str(existing["id"])
    created = service.files().create(
        body={"name": name, "mimeType": DRIVE_FOLDER_MIME, "parents": [parent_id]},
        fields="id",
    ).execute()
    return str(created["id"])


def ensure_drive_path(service, root_id: str, parts: tuple[str, ...] | list[str]) -> str:
    parent = root_id
    for part in parts:
        parent = ensure_folder(service, parent, part)
    return parent


def upload_file(service, source: Path, parent_id: str) -> str:
    mime_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
    media = MediaFileUpload(str(source), mimetype=mime_type, resumable=True)
    existing = find_child(service, parent_id, source.name)
    if existing:
        result = service.files().update(
            fileId=existing["id"], media_body=media, fields="id"
        ).execute()
    else:
        result = service.files().create(
            body={"name": source.name, "parents": [parent_id]},
            media_body=media,
            fields="id",
        ).execute()
    return str(result["id"])


def upload_tree(service, local_root: Path, drive_parent_id: str) -> int:
    if not local_root.exists():
        return 0
    count = 0
    for source in sorted(local_root.rglob("*")):
        if not source.is_file():
            continue
        rel_parent = source.parent.relative_to(local_root)
        parent = drive_parent_id
        if rel_parent.parts:
            parent = ensure_drive_path(service, drive_parent_id, list(rel_parent.parts))
        upload_file(service, source, parent)
        count += 1
    return count


def replace_drive_output_tree(service, local_output: Path) -> int:
    """Match Colab semantics: replace only FPL_VORTEX/First Day Video."""
    output_id = ensure_folder(service, DRIVE_PROJECT_ROOT_ID, "First Day Video")
    for item in list(list_children(service, output_id)):
        service.files().update(fileId=item["id"], body={"trashed": True}).execute()
    return upload_tree(service, local_output, output_id)


def prepare_fpl_core() -> None:
    target_root = LOCAL_DATA / "00_SOURCE_ARCHIVE" / "FPL_CORE"
    data_target = target_root / "data"
    if data_target.exists() and any(data_target.iterdir()):
        return
    target_root.mkdir(parents=True, exist_ok=True)
    url = f"https://github.com/{FPL_CORE_REPO}/archive/{FPL_CORE_COMMIT}.zip"
    log(f"[CLOUD] Download pinned FPL-Core: {FPL_CORE_COMMIT[:12]}")
    response = requests.get(url, timeout=180)
    response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        archive.extractall(LOCAL_RUN / "fpl_core_zip")
    extracted_roots = [p for p in (LOCAL_RUN / "fpl_core_zip").iterdir() if p.is_dir()]
    if len(extracted_roots) != 1:
        raise RuntimeError("Could not identify FPL-Core archive root")
    source_data = extracted_roots[0] / "data"
    if not source_data.exists():
        raise RuntimeError("Pinned FPL-Core archive contains no data directory")
    shutil.copytree(source_data, data_target, dirs_exist_ok=True)


def install_fake_colab() -> None:
    """Provide only the two Colab APIs the notebook references."""
    colab = types.ModuleType("google.colab")
    drive = types.ModuleType("google.colab.drive")
    userdata = types.ModuleType("google.colab.userdata")

    def mount(path: str, **_kwargs):
        Path(path).mkdir(parents=True, exist_ok=True)
        LOCAL_MYDRIVE.mkdir(parents=True, exist_ok=True)
        log(f"[CLOUD] Colab Drive mount mapped locally at {LOCAL_MYDRIVE}")
        return None

    def get_secret(name: str, default=None):
        return os.environ.get(name, default)

    drive.mount = mount
    userdata.get = get_secret
    colab.drive = drive
    colab.userdata = userdata
    sys.modules["google.colab"] = colab
    sys.modules["google.colab.drive"] = drive
    sys.modules["google.colab.userdata"] = userdata


def prepare_fake_drive(service) -> None:
    if LOCAL_CONTENT.exists():
        # Never remove all of /content on an arbitrary machine; only the fake
        # Drive subtree that this GitHub runner owns.
        fake_drive = LOCAL_CONTENT / "drive"
        if fake_drive.exists():
            shutil.rmtree(fake_drive)
    LOCAL_MYDRIVE.mkdir(parents=True, exist_ok=True)
    LOCAL_RUN.mkdir(parents=True, exist_ok=True)

    log("[CLOUD] Mirroring persistent FPL_VORTEX state (excluding old output)...")
    download_folder(
        service,
        DRIVE_PROJECT_ROOT_ID,
        LOCAL_PROJECT,
        skip_names=("First Day Video",),
    )

    LOCAL_DATA.mkdir(parents=True, exist_ok=True)
    log("[CLOUD] Mirroring governance / elite data needed by the notebook...")
    for folder_name in DATA_FOLDERS_TO_MIRROR:
        item = find_child(service, DRIVE_DATA_ROOT_ID, folder_name, DRIVE_FOLDER_MIME)
        if item:
            download_folder(service, item["id"], LOCAL_DATA / folder_name)
        else:
            (LOCAL_DATA / folder_name).mkdir(parents=True, exist_ok=True)

    prepare_fpl_core()

    LOCAL_ELEMENTS.mkdir(parents=True, exist_ok=True)
    download_file(service, DRIVE_LOGO_FILE_ID, LOCAL_ELEMENTS / "logo.png")
    download_file(service, DRIVE_PL_FILE_ID, LOCAL_ELEMENTS / "pl.png")

    download_file(service, NOTEBOOK_FILE_ID, LOCAL_NOTEBOOK)
    log(f"[CLOUD] Notebook downloaded: {LOCAL_NOTEBOOK}")


def run_cell(source: str, filename: str, namespace: dict) -> None:
    code = compile(
        source,
        filename,
        "exec",
        flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT,
        dont_inherit=True,
    )
    result = eval(code, namespace)
    if asyncio.iscoroutine(result):
        asyncio.run(result)


def execute_notebook() -> dict:
    payload = json.loads(LOCAL_NOTEBOOK.read_text(encoding="utf-8"))
    namespace = {
        "__name__": "__main__",
        "__file__": str(LOCAL_NOTEBOOK),
    }
    code_cells = [c for c in payload.get("cells", []) if c.get("cell_type") == "code"]
    log(f"[CLOUD] Executing {len(code_cells)} notebook code cells...")
    executed = 0
    for notebook_index, cell in enumerate(payload.get("cells", [])):
        if cell.get("cell_type") != "code":
            continue
        source = "".join(cell.get("source", []))
        if not source.strip():
            continue
        label = f"{LOCAL_NOTEBOOK.name}::cell_{notebook_index}"
        log(f"\n[CLOUD] >>> cell {notebook_index}")
        try:
            run_cell(source, label, namespace)
        except Exception as exc:
            raise RuntimeError(
                f"Notebook failed in code cell index {notebook_index}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        executed += 1
    return {"executed_code_cells": executed, "namespace": namespace}


def sync_persistent_state(service) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name in PROJECT_FOLDERS_TO_SYNC:
        local = LOCAL_PROJECT / name
        if not local.exists():
            continue
        parent = ensure_folder(service, DRIVE_PROJECT_ROOT_ID, name)
        counts[f"FPL_VORTEX/{name}"] = upload_tree(service, local, parent)

    for name in DATA_FOLDERS_TO_SYNC:
        local = LOCAL_DATA / name
        if not local.exists():
            continue
        parent = ensure_folder(service, DRIVE_DATA_ROOT_ID, name)
        counts[f"FPL_VORTEX_DATA/{name}"] = upload_tree(service, local, parent)
    return counts


def main() -> int:
    started = datetime.now(timezone.utc)
    report = {
        "status": "RUNNING",
        "started_at_utc": started.isoformat(),
        "notebook_file_id": NOTEBOOK_FILE_ID,
        "drive_output": "MyDrive/FPL_VORTEX/First Day Video",
        "github_run_id": os.environ.get("GITHUB_RUN_ID"),
        "github_sha": os.environ.get("GITHUB_SHA"),
    }

    log("=" * 78)
    log("FPL VORTEX - WEEKLY REVIEW CLOUD RUNNER")
    log("Latest Drive notebook -> GitHub Actions -> same Drive output folder")
    log("=" * 78)

    service = drive_service()
    try:
        prepare_fake_drive(service)
        install_fake_colab()
        result = execute_notebook()

        local_output = LOCAL_PROJECT / "First Day Video"
        if not local_output.exists():
            raise RuntimeError(f"Notebook created no output tree at {local_output}")

        output_count = replace_drive_output_tree(service, local_output)
        persistent_counts = sync_persistent_state(service)

        finished = datetime.now(timezone.utc)
        report.update(
            {
                "status": "PASS",
                "finished_at_utc": finished.isoformat(),
                "elapsed_seconds": round((finished - started).total_seconds(), 2),
                "executed_code_cells": result["executed_code_cells"],
                "output_files_uploaded": output_count,
                "persistent_files_uploaded_or_updated": persistent_counts,
            }
        )
        log("\n[CLOUD] WEEKLY REVIEW: PASS")
        log(f"[CLOUD] Uploaded {output_count} output file(s) to MyDrive/FPL_VORTEX/First Day Video")
        return 0
    except Exception as exc:
        finished = datetime.now(timezone.utc)
        report.update(
            {
                "status": "FAIL",
                "finished_at_utc": finished.isoformat(),
                "elapsed_seconds": round((finished - started).total_seconds(), 2),
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            }
        )
        log("\n[CLOUD] WEEKLY REVIEW: FAIL")
        log(report["traceback"])
        return 1
    finally:
        LOCAL_RUN.mkdir(parents=True, exist_ok=True)
        LOCAL_REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")
        # Save the cloud run report into the existing Drive log tree even on
        # failures when authentication itself succeeded.
        try:
            weekly_logs = ensure_drive_path(service, DRIVE_DATA_ROOT_ID, ["07_LOGS", "WEEKLY_REVIEW"])
            upload_file(service, LOCAL_REPORT, weekly_logs)
        except Exception as report_exc:
            log(f"[CLOUD] Could not upload run report: {report_exc}")


if __name__ == "__main__":
    raise SystemExit(main())
