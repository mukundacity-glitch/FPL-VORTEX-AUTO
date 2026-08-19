from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PROJECT_NAME = "FPL VORTEX"
SEASON = "2026-27"
FPL_API = "https://fantasy.premierleague.com/api"

# Visible VORTEX-facing names. Upstream identity is retained only as provenance.
SOURCE_FEEDS = {
    "historical_seed": {
        "label": "FPL VORTEX HISTORICAL",
        "origin": "https://github.com/vaastav/Fantasy-Premier-League.git",
    },
    "rich_match_feed": {
        "label": "FPL VORTEX RICH MATCH DATA",
        "origin": "https://github.com/olbauday/FPL-Core-Insights.git",
    },
}

# Local paths used inside the automated runner before Drive upload/sync.
WORK_ROOT = Path(".vortex_work")
RAW_ROOT = WORK_ROOT / "raw"
MASTER_ROOT = WORK_ROOT / "master"
LOG_ROOT = WORK_ROOT / "logs"

# Google Drive destination. The updater will address this through Drive API,
# not by assuming a mounted Colab filesystem.
DRIVE_ROOT_NAME = "FPL_VORTEX_DATA"

# Twice-daily automation slots, intentionally offset from the top of the hour.
UPDATE_CRON_UTC = ("17 8 * * *", "17 18 * * *")

REQUEST_TIMEOUT_SECONDS = 45
HTTP_RETRIES = 3


def ensure_local_dirs() -> None:
    for path in (WORK_ROOT, RAW_ROOT, MASTER_ROOT, LOG_ROOT):
        path.mkdir(parents=True, exist_ok=True)
