from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .config import LOG_ROOT, RAW_ROOT, SEASON, WORK_ROOT

SOURCE_SEASON_DIR = WORK_ROOT / "sources" / "rich_match_feed" / "data" / "2026-2027"
EXPORT_DIR = RAW_ROOT / "rich_match_current" / SEASON


def export_current_rich_data() -> Path | None:
    """Copy the current-season rich feed into the VORTEX raw area for Drive sync."""
    if not SOURCE_SEASON_DIR.exists():
        print(f"[VORTEX] Rich current-season folder not found: {SOURCE_SEASON_DIR}")
        return None

    EXPORT_DIR.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SOURCE_SEASON_DIR, EXPORT_DIR, dirs_exist_ok=True)

    files = [p for p in EXPORT_DIR.rglob("*") if p.is_file()]
    payload = {
        "source": "FPL VORTEX RICH MATCH DATA",
        "season": SEASON,
        "exported_at_utc": datetime.now(timezone.utc).isoformat(),
        "files": len(files),
        "path": str(EXPORT_DIR),
    }

    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    (LOG_ROOT / "rich_export_latest.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )

    print(f"[VORTEX] Rich current data exported | files={len(files)}")
    return EXPORT_DIR
