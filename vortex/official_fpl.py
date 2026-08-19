from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from .config import FPL_API, HTTP_RETRIES, RAW_ROOT, REQUEST_TIMEOUT_SECONDS


SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "FPL-VORTEX-AUTO/0.1"})


def _get_json(url: str) -> Any:
    last_error: Exception | None = None
    for attempt in range(1, HTTP_RETRIES + 1):
        try:
            response = SESSION.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()
            return response.json()
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < HTTP_RETRIES:
                time.sleep(1.5 * attempt)
    raise RuntimeError(f"FPL request failed after {HTTP_RETRIES} attempts: {url}") from last_error


def _save_json(data: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def collect_official_fpl(*, include_player_summaries: bool = False) -> Path:
    """Collect one immutable Official FPL snapshot and return its folder."""
    stamp = utc_stamp()
    snapshot = RAW_ROOT / "official_fpl" / stamp
    snapshot.mkdir(parents=True, exist_ok=True)

    bootstrap = _get_json(f"{FPL_API}/bootstrap-static/")
    fixtures = _get_json(f"{FPL_API}/fixtures/")

    _save_json(bootstrap, snapshot / "bootstrap-static.json")
    _save_json(fixtures, snapshot / "fixtures.json")

    events = bootstrap.get("events", [])
    players = bootstrap.get("elements", [])
    teams = bootstrap.get("teams", [])

    pd.DataFrame(events).to_parquet(snapshot / "events.parquet", index=False)
    pd.DataFrame(players).to_parquet(snapshot / "players.parquet", index=False)
    pd.DataFrame(teams).to_parquet(snapshot / "teams.parquet", index=False)
    pd.DataFrame(fixtures).to_parquet(snapshot / "fixtures.parquet", index=False)

    for event in events:
        gw = event.get("id")
        if not gw:
            continue
        if event.get("is_current") or event.get("finished") or event.get("data_checked"):
            try:
                live = _get_json(f"{FPL_API}/event/{int(gw)}/live/")
                _save_json(live, snapshot / "event_live" / f"gw_{int(gw):02d}.json")
            except Exception as exc:  # noqa: BLE001
                print(f"[VORTEX] GW{gw} live data skipped: {exc}")

    if include_player_summaries:
        summary_dir = snapshot / "element_summary"
        for idx, player in enumerate(players, start=1):
            player_id = int(player["id"])
            try:
                summary = _get_json(f"{FPL_API}/element-summary/{player_id}/")
                _save_json(summary, summary_dir / f"{player_id}.json")
            except Exception as exc:  # noqa: BLE001
                print(f"[VORTEX] Player {player_id} summary skipped: {exc}")
            if idx % 50 == 0:
                print(f"[VORTEX] Player summaries: {idx}/{len(players)}")
            time.sleep(0.08)

    metadata = {
        "source": "FPL VORTEX CURRENT",
        "origin": "official_fpl_api",
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "snapshot": str(snapshot),
        "events": len(events),
        "players": len(players),
        "teams": len(teams),
        "fixtures": len(fixtures),
        "player_summaries": bool(include_player_summaries),
    }
    _save_json(metadata, snapshot / "_snapshot_metadata.json")

    print(
        f"[VORTEX] Official FPL snapshot ready | "
        f"players={len(players)} teams={len(teams)} fixtures={len(fixtures)}"
    )
    return snapshot
