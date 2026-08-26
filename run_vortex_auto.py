from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from vortex.config import LOG_ROOT, ensure_local_dirs
from vortex.current_export import export_current_rich_data
from vortex.drive_sync import sync_vortex_to_drive
from vortex.official_fpl import collect_official_fpl
from vortex.source_refresh import refresh_rich_match_feed


def _drive_secrets_present() -> bool:
    return all(
        os.environ.get(name)
        for name in (
            "GOOGLE_OAUTH_CLIENT_ID",
            "GOOGLE_OAUTH_CLIENT_SECRET",
            "GOOGLE_OAUTH_REFRESH_TOKEN",
        )
    )


def _validate_rich_feed(rich_feed: dict, rich_export: object) -> None:
    status = str(rich_feed.get("status") or "UNKNOWN")
    if status == "ERROR":
        detail = rich_feed.get("error") or "unknown upstream error"
        raise RuntimeError(f"Rich match feed refresh failed: {detail}")
    if rich_export is None:
        raise RuntimeError(
            "Rich match feed refresh produced no current-season export. "
            "Check the upstream season folder and source availability."
        )


def run() -> dict:
    ensure_local_dirs()
    started = datetime.now(timezone.utc)

    print("=" * 72)
    print("FPL VORTEX AUTO — TWICE-DAILY DATA UPDATE")
    print("=" * 72)
    print(f"Started UTC: {started.isoformat()}")

    rich_feed = refresh_rich_match_feed()
    rich_export = export_current_rich_data()
    _validate_rich_feed(rich_feed, rich_export)
    official_snapshot = collect_official_fpl(include_player_summaries=False)

    drive_report = None
    drive_status = "SKIPPED_MISSING_SECRETS"

    if _drive_secrets_present():
        drive_report = sync_vortex_to_drive()
        drive_status = "PASS"
    elif os.environ.get("GITHUB_ACTIONS"):
        raise RuntimeError(
            "Google Drive OAuth secrets are required in GitHub Actions; "
            "the update was not published."
        )
    else:
        print("[VORTEX] Google Drive sync skipped: OAuth secrets not configured yet")

    finished = datetime.now(timezone.utc)

    report = {
        "status": "PASS",
        "started_at_utc": started.isoformat(),
        "finished_at_utc": finished.isoformat(),
        "elapsed_seconds": round((finished - started).total_seconds(), 2),
        "rich_feed": rich_feed,
        "rich_export": str(rich_export) if rich_export else None,
        "official_snapshot": str(official_snapshot),
        "drive_status": drive_status,
        "drive_report": drive_report,
    }

    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    (LOG_ROOT / "auto_update_latest.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )

    print("\n[VORTEX] AUTOMATED UPDATE: PASS")
    print(f"[VORTEX] Drive status: {drive_status}")
    return report


if __name__ == "__main__":
    run()
