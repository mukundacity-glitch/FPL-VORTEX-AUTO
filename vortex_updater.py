from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from vortex.config import LOG_ROOT, ensure_local_dirs
from vortex.official_fpl import collect_official_fpl
from vortex.source_sync import sync_source_feeds


def run(*, player_summaries: bool = False) -> dict:
    ensure_local_dirs()

    started = datetime.now(timezone.utc)
    print("=" * 72)
    print("FPL VORTEX AUTO — DATA UPDATE")
    print("=" * 72)
    print(f"Started UTC: {started.isoformat()}")

    source_report = sync_source_feeds()
    official_snapshot = collect_official_fpl(
        include_player_summaries=player_summaries
    )

    finished = datetime.now(timezone.utc)
    report = {
        "status": "PASS",
        "started_at_utc": started.isoformat(),
        "finished_at_utc": finished.isoformat(),
        "elapsed_seconds": round((finished - started).total_seconds(), 2),
        "official_snapshot": str(official_snapshot),
        "source_feeds": source_report,
        "drive_sync": "NOT_CONFIGURED_YET",
    }

    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    (LOG_ROOT / "update_latest.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )

    print("\n[VORTEX] Local automated update: PASS")
    print(f"[VORTEX] Snapshot: {official_snapshot}")
    print("[VORTEX] Google Drive sync: not configured yet")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the FPL VORTEX automated data updater")
    parser.add_argument(
        "--player-summaries",
        action="store_true",
        help="Also fetch every current player's element-summary endpoint.",
    )
    args = parser.parse_args()
    run(player_summaries=args.player_summaries)


if __name__ == "__main__":
    main()
