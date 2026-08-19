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


def refresh_rich_match_feed() -> dict[str, str | None]:
    """Refresh only the current rich-data feed used by the twice-daily job."""
    SOURCE_WORK_ROOT.mkdir(parents=True, exist_ok=True)
    LOG_ROOT.mkdir(parents=True, exist_ok=True)

    alias = "rich_match_feed"
    spec = SOURCE_FEEDS[alias]
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
            branch = _run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=destination
            ).stdout.strip()
            fetched = _run(
                ["git", "fetch", "--depth", "1", "origin", branch],
                cwd=destination,
                check=False,
            )
            if fetched.returncode != 0:
                row["status"] = "UPSTREAM_UNAVAILABLE_USING_LOCAL_COPY"
            else:
                _run(["git", "reset", "--hard", f"origin/{branch}"], cwd=destination)
                row["status"] = "UPDATED"

        if (destination / ".git").exists():
            row["commit"] = _run(
                ["git", "rev-parse", "HEAD"], cwd=destination
            ).stdout.strip()

    except Exception as exc:  # noqa: BLE001
        row["status"] = "ERROR"
        row["error"] = f"{type(exc).__name__}: {exc}"

    payload = {
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "feed": row,
    }
    (LOG_ROOT / "rich_feed_latest.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )

    print(f"[VORTEX] {label}: {row['status']}")
    return row
