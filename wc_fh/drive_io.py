from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

NOTEBOOK_ID = "1RVyrEGhHg77h4ek7ASowfRHIeYFDl2aB"
ASSET_FOLDER_ID = "1h3Loznr5RnsMyh-kUd-zMxEgOf98uFSt"
REMOTE_ROOT = "FPL_VORTEX/WC_FH_GITHUB"
LOCAL_ROOT = Path("/content/drive/MyDrive") / REMOTE_ROOT
ASSET_NAMES = ("opening.mp4", "outro.mp4", "popup.mp4", "Background_music.mp3", "logo1.png", "logo.png", "pl.png")


def output_root() -> Path:
    configured = Path(os.environ["WC_FH_OUTPUT_ROOT"]).resolve()
    expected_parent = (LOCAL_ROOT / "runs").resolve()
    if configured.parent != expected_parent or not re.fullmatch(r"\d+_\d+", configured.name):
        raise ValueError("WC- FH outputs must use their isolated runs/<run_id>_<attempt> directory.")
    return configured


def run(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(arguments, check=False, capture_output=True, text=True)
    if check and result.returncode:
        raise RuntimeError(f"{arguments[0]} failed (exit {result.returncode}): {result.stderr[-1800:]}")
    return result


def configure() -> None:
    names = ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "GOOGLE_OAUTH_REFRESH_TOKEN")
    values = {name: str(os.environ.get(name, "")).strip() for name in names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise RuntimeError("Missing existing repository secrets: " + ", ".join(missing))
    body = urllib.parse.urlencode(dict(client_id=values[names[0]], client_secret=values[names[1]],
                                       refresh_token=values[names[2]], grant_type="refresh_token")).encode()
    request = urllib.request.Request("https://oauth2.googleapis.com/token", data=body,
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(request, timeout=40) as response:
        refreshed = json.load(response)
    token = dict(access_token=refreshed["access_token"], token_type="Bearer", refresh_token=values[names[2]],
                 expiry=(datetime.now(timezone.utc) + timedelta(seconds=max(60, int(refreshed.get("expires_in", 3600)) - 60))).isoformat().replace("+00:00", "Z"))
    directory = Path(os.environ["RUNNER_TEMP"]) / "wc-fh-credentials"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    config = directory / "rclone.conf"
    sections = []
    for remote, folder_id in [("wc-fh-drive", None), ("wc-fh-assets", ASSET_FOLDER_ID)]:
        sections.extend([f"[{remote}]", "type = drive", f"client_id = {values[names[0]]}",
                         f"client_secret = {values[names[1]]}", "scope = drive", f"token = {json.dumps(token)}"])
        if folder_id:
            sections.append(f"root_folder_id = {folder_id}")
        sections.append("")
    config.write_text("\n".join(sections), encoding="utf-8")
    config.chmod(0o600)
    with Path(os.environ["GITHUB_ENV"]).open("a", encoding="utf-8") as handle:
        handle.write(f"RCLONE_CONFIG={config}\n")
    print("WC- FH: private Drive connection configured; credentials stay outside outputs.")


def copy_optional(remote: str, local: Path) -> bool:
    exists = run("rclone", "lsf", f"wc-fh-drive:{remote}", "--max-depth", "1", check=False)
    if exists.returncode:
        print(f"Optional cache absent: {remote}")
        return False
    local.mkdir(parents=True, exist_ok=True)
    run("rclone", "copy", f"wc-fh-drive:{remote}", str(local), "--transfers", "4")
    return True


def restore() -> None:
    root = output_root()
    root.mkdir(parents=True, exist_ok=True)
    notebook = Path(os.environ["WC_FH_NOTEBOOK_PATH"])
    notebook.parent.mkdir(parents=True, exist_ok=True)
    run("rclone", "backend", "copyid", "wc-fh-drive:", NOTEBOOK_ID, str(notebook))
    data = json.loads(notebook.read_text(encoding="utf-8"))
    if not isinstance(data.get("cells"), list):
        raise ValueError("The downloaded WC/FH Drive file is not a notebook.")
    cache = root / "analysis" / "model_inputs"
    if not copy_optional(f"{REMOTE_ROOT}/cache/model_inputs", cache):
        copy_optional("FPL_VORTEX/Analysis/wc_fh/model_inputs", cache)
    if os.environ.get("WC_FH_TASK") != "Refresh player picker":
        assets = Path(os.environ["FPL_MEDIA_ASSET_DIR"])
        assets.mkdir(parents=True, exist_ok=True)
        run("rclone", "copy", "wc-fh-assets:", str(assets), "--transfers", "4")
        missing = [name for name in ASSET_NAMES if not (assets / name).is_file() or not (assets / name).stat().st_size]
        if missing:
            raise FileNotFoundError("Missing original approved media: " + ", ".join(missing))
    print(f"WC- FH: current notebook restored by ID; output is {root}")


def publish() -> None:
    root = output_root()
    if not root.is_dir():
        print("WC- FH: no outputs to save.")
        return
    remote = f"{REMOTE_ROOT}/runs/{root.name}"
    run("rclone", "copy", str(root), f"wc-fh-drive:{remote}", "--transfers", "4")
    cache = root / "analysis" / "model_inputs"
    if cache.is_dir():
        run("rclone", "copy", str(cache), f"wc-fh-drive:{REMOTE_ROOT}/cache/model_inputs", "--transfers", "4")
    print(f"WC- FH: saved outputs and metadata to My Drive/{remote}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="WC- FH-only Drive connection, inputs and outputs.")
    parser.add_argument("command", choices=("configure", "restore", "publish"))
    command = parser.parse_args().command
    {"configure": configure, "restore": restore, "publish": publish}[command]()
