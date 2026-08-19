from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .config import LOG_ROOT, SOURCE_FEEDS, WORK_ROOT

SOURCE_WORK_ROOT = WORK_ROOT / "sources"


def _run(args: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=True,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"Command failed: {' '.join(args)}\n"
            f"STDOUT:\n{result.stdout[-2500:]}\n"
            f"STDERR:\n{result.stderr[-2500:]}"
        )
    return result


def sync_source_feeds() -> dict[str, dict[str, str | None]]:
    """Refresh upstream feeds in the runner workspace while exposing only VORTEX labels."""
    SOURCE_WORK_ROOT.mkdir(parents=True, exist_ok=True)
    LOG_ROOT.mkdir(parents=True, exist_ok=True)

    report: dict[str, dict[str, str | None]] = {}

    for alias, spec in SOURCE_FEEDS.items():
        destination = SOURCE_WORK_ROOT / alias
        origin = str(spec["origin"])
        label = str(spec["label"])

        row: dict[str, str | None] = {
            "label": label,
            "status": None,
            "commit": None,
            "path": str(destination),
        }

        try:
            if not (destination / ".git").exists():
                print(f"[VORTEX] Initialising {label} ...")
                _run(["git", "clone", "--depth", "1", origin, str(destination)])
                row["status"] = "CLONED"
            else:
                print(f"[VORTEX] Refreshing {label} ...")
                fetched = _run(["git", "fetch", "--depth", "1", "origin"], cwd=destination, check=False)
                if fetched.returncode != 0:
                    row["status"] = "UPSTREAM_UNAVAILABLE_USING_LOCAL_COPY"
                else:
                    branch = _run(
                        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                        cwd=destination,
                    ).stdout.strip()
                    reset = _run(
                        ["git", "reset", "--hard", f"origin/{branch}"],
                        cwd=destination,
                        check=False,
                    )
                    row["status"] = "UPDATED" if reset.returncode == 0 else "FETCHED"

            if (destination / ".git").exists():
                row["commit"] = _run(
                    ["git", "rev-parse", "HEAD"], cwd=destination
                ).stdout.strip()

        except Exception as exc:  # noqa: BLE001
            row["status"] = "ERROR"
            row["error"] = f"{type(exc).__name__}: {exc}"

        print(f"[VORTEX] {label}: {row['status']}")
        report[alias] = row

    payload = {
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "feeds": report,
    }
    (LOG_ROOT / "source_sync_latest.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    return report
