from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .common import read_json

CONFIG_PATH = Path(__file__).with_name("config.json")
MYDRIVE = Path("/content/drive/MyDrive")
READ_ONLY_INPUTS = (
    "FPL_VORTEX/MODEL_INPUTS",
    "FPL_VORTEX/DECISION_ENGINE",
    "FPL_VORTEX/FIRST VIDEO/DATA",
    "FPL_VORTEX/elements",
    "elements",
    "FPL_VORTEX_DATA/03_ELITE_MANAGERS",
)


def _run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=check, text=True, capture_output=True)


def _secret(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise RuntimeError(f"Missing required GitHub secret: {name}")
    return value


def configure() -> Path:
    client_id = _secret("GOOGLE_OAUTH_CLIENT_ID")
    client_secret = _secret("GOOGLE_OAUTH_CLIENT_SECRET")
    refresh_token = _secret("GOOGLE_OAUTH_REFRESH_TOKEN")
    if not client_id.endswith(".apps.googleusercontent.com"):
        raise RuntimeError("GOOGLE_OAUTH_CLIENT_ID has an invalid format")

    body = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        refreshed = json.loads(response.read().decode("utf-8"))

    expires_in = int(refreshed.get("expires_in", 3600))
    token = {
        "access_token": refreshed["access_token"],
        "token_type": refreshed.get("token_type", "Bearer"),
        "refresh_token": refresh_token,
        "expiry": (
            datetime.now(timezone.utc)
            + timedelta(seconds=max(60, expires_in - 60))
        ).isoformat().replace("+00:00", "Z"),
    }
    cfg = read_json(CONFIG_PATH, "Day 2 config")
    asset_folder_id = str(cfg["shared_assets"]["drive_folder_id"])
    temp = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "day2-rclone"
    temp.mkdir(parents=True, exist_ok=True)
    path = temp / "rclone.conf"
    token_json = json.dumps(token, separators=(",", ":"))
    path.write_text(
        "\n".join(
            (
                "[vortex-drive]",
                "type = drive",
                f"client_id = {client_id}",
                f"client_secret = {client_secret}",
                "scope = drive",
                f"token = {token_json}",
                "",
                "[day2-shared-assets]",
                "type = drive",
                f"client_id = {client_id}",
                f"client_secret = {client_secret}",
                "scope = drive",
                f"token = {token_json}",
                f"root_folder_id = {asset_folder_id}",
                "",
            )
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)
    github_env = os.environ.get("GITHUB_ENV")
    if github_env:
        with Path(github_env).open("a", encoding="utf-8") as handle:
            handle.write(f"RCLONE_CONFIG={path}\n")
    os.environ["RCLONE_CONFIG"] = str(path)
    print("[DAY 2] Google Drive OAuth connection: PASS")
    return path


def _copy_remote(remote: str, local: Path, *, required: bool = False) -> bool:
    exists = _run("rclone", "lsf", f"vortex-drive:{remote}", "--max-depth", "1", check=False)
    if exists.returncode != 0:
        if required:
            raise RuntimeError(f"Required Drive path is missing: My Drive/{remote}")
        print(f"[DAY 2] Optional Drive input absent: My Drive/{remote}")
        return False
    local.mkdir(parents=True, exist_ok=True)
    _run(
        "rclone", "copy", f"vortex-drive:{remote}", str(local),
        "--create-empty-src-dirs", "--transfers", "4", "--checkers", "8",
    )
    for path in local.rglob("*"):
        try:
            path.chmod(0o555 if path.is_dir() else 0o444)
        except OSError:
            pass
    print(f"[DAY 2] Restored read-only input: My Drive/{remote}")
    return True


def restore() -> None:
    cfg = read_json(CONFIG_PATH, "Day 2 config")
    MYDRIVE.mkdir(parents=True, exist_ok=True)
    for remote in READ_ONLY_INPUTS:
        _copy_remote(remote, MYDRIVE / remote)

    output_root = Path(cfg["output"]["local_root"]).resolve()
    expected = (MYDRIVE / "FPL_VORTEX/Day_2").resolve()
    if output_root != expected:
        raise RuntimeError(f"Unsafe Day 2 output root: {output_root}")
    shutil.rmtree(output_root, ignore_errors=True)
    for name in ("MP3", "Data", "MP4", "slide"):
        (output_root / name).mkdir(parents=True, exist_ok=True)

    notebook = cfg["notebook"]
    notebook_local = Path(notebook["local_path"])
    notebook_local.parent.mkdir(parents=True, exist_ok=True)
    _run("rclone", "copyto", f"vortex-drive:{notebook['drive_path']}", str(notebook_local))
    if not notebook_local.is_file() or notebook_local.stat().st_size <= 0:
        raise RuntimeError("Day 2 notebook download failed")

    asset_dir = Path(os.environ.get("DAY2_ASSET_DIR", Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "day2-assets"))
    asset_dir.mkdir(parents=True, exist_ok=True)
    for name in cfg["shared_assets"]["files"]:
        _run("rclone", "copyto", f"day2-shared-assets:{name}", str(asset_dir / name))
        if not (asset_dir / name).is_file() or (asset_dir / name).stat().st_size <= 0:
            raise RuntimeError(f"Required shared asset is missing: {name}")
    print(f"[DAY 2] Isolated output root prepared: {output_root}")
    print(f"[DAY 2] Notebook restored: {notebook_local}")
    print(f"[DAY 2] Shared Day 1 media assets restored read-only to runner: {asset_dir}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def restore_checkpoint(video_date: str) -> bool:
    """Reuse only a verified same-date manual retry render from Day 2 Drive."""
    try:
        datetime.strptime(video_date, "%Y-%m-%d")
    except ValueError as exc:
        raise RuntimeError("Checkpoint video date must be YYYY-MM-DD") from exc

    cfg = read_json(CONFIG_PATH, "Day 2 config")
    output_root = Path(cfg["output"]["local_root"]).resolve()
    expected = (MYDRIVE / "FPL_VORTEX/Day_2").resolve()
    if output_root != expected:
        raise RuntimeError(f"Unsafe Day 2 checkpoint root: {output_root}")

    drive_root = str(cfg["output"]["drive_path"]).rstrip("/")
    program_name = str(cfg["output"]["program_filename"])
    remote_program = f"vortex-drive:{drive_root}/MP4/{program_name}"
    remote_ledger = (
        f"vortex-drive:{drive_root}/Data/final_master_verification.json"
    )

    stat = _run(
        "rclone", "lsjson", "--stat", remote_program, check=False
    )
    if stat.returncode != 0 or not stat.stdout.strip():
        print("[DAY 2] No reusable Drive render checkpoint found")
        return False

    try:
        info = json.loads(stat.stdout)
        if isinstance(info, list):
            info = info[0] if info else {}
    except json.JSONDecodeError:
        print("[DAY 2] Drive checkpoint metadata is unreadable; rendering fresh")
        return False

    modified = str((info or {}).get("ModTime") or "")
    if not modified.startswith(video_date):
        print(
            "[DAY 2] Drive checkpoint is from a different video date; "
            "rendering fresh"
        )
        return False

    program_local = output_root / "MP4" / program_name
    ledger_local = output_root / "Data" / "final_master_verification.json"
    program_local.parent.mkdir(parents=True, exist_ok=True)
    ledger_local.parent.mkdir(parents=True, exist_ok=True)

    ledger_check = _run("rclone", "lsjson", "--stat", remote_ledger, check=False)
    if ledger_check.returncode != 0:
        print("[DAY 2] Drive checkpoint ledger is missing; rendering fresh")
        return False

    _run("rclone", "copyto", remote_program, str(program_local))
    _run("rclone", "copyto", remote_ledger, str(ledger_local))

    verification = read_json(ledger_local, "Day 2 render checkpoint")
    if str(verification.get("file") or "") != program_name:
        raise RuntimeError("Day 2 checkpoint ledger points to the wrong file")
    if str(verification.get("quality") or "").upper() != "FINAL":
        raise RuntimeError("Day 2 checkpoint is not a FINAL render")
    if int(verification.get("width") or 0) != 3840:
        raise RuntimeError("Day 2 checkpoint width is not 3840")
    if int(verification.get("height") or 0) != 2160:
        raise RuntimeError("Day 2 checkpoint height is not 2160")
    if int(verification.get("size_bytes") or 0) != program_local.stat().st_size:
        raise RuntimeError("Day 2 checkpoint size does not match its ledger")

    expected_sha = str(verification.get("sha256") or "").lower()
    actual_sha = _sha256(program_local)
    if not expected_sha or actual_sha != expected_sha:
        raise RuntimeError("Day 2 checkpoint SHA-256 does not match its ledger")

    if str(verification.get("vcodec") or "").lower() != "h264":
        raise RuntimeError("Day 2 checkpoint is not H.264")
    if not str(verification.get("acodec") or "").strip():
        raise RuntimeError("Day 2 checkpoint has no verified audio codec")

    print(
        "[DAY 2] Verified same-date Drive render checkpoint restored: "
        f"{program_local.name}"
    )
    print(
        "[DAY 2] Retry will skip notebook rendering and continue with "
        "the proven Day 1 media method"
    )
    return True


def publish() -> None:
    cfg = read_json(CONFIG_PATH, "Day 2 config")
    local_root = Path(cfg["output"]["local_root"])
    drive_path = str(cfg["output"]["drive_path"])
    if not local_root.is_dir():
        print("[DAY 2] No output directory to publish")
        return
    _run(
        "rclone", "copy", str(local_root), f"vortex-drive:{drive_path}",
        "--create-empty-src-dirs", "--transfers", "4", "--checkers", "8",
    )
    print(f"[DAY 2] Published only My Drive/{drive_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Day 2-only Google Drive I/O")
    parser.add_argument("command", choices=("configure", "restore", "checkpoint", "publish"))
    parser.add_argument("--video-date", default="")
    args = parser.parse_args()
    if args.command == "configure":
        configure()
    elif args.command == "restore":
        restore()
    elif args.command == "checkpoint":
        if not args.video_date:
            raise SystemExit("--video-date is required for checkpoint")
        if not restore_checkpoint(args.video_date):
            raise SystemExit(2)
    else:
        publish()


if __name__ == "__main__":
    main()
