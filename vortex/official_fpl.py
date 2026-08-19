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


def _parquet_safe_frame(records: Any) -> pd.DataFrame:
    """Build a Parquet-safe DataFrame while preserving nested API values as JSON text.

    The FPL API sometimes adds dict/list-valued fields (for example event.rules).
    PyArrow can fail on empty structs such as `{}`. The canonical raw JSON files keep
    the original structure; Parquet gets a stable JSON-string representation for any
    nested object/list columns.
    """
    frame = pd.DataFrame(records)
    if frame.empty:
        return frame

    nested_types = (dict, list, tuple)

    for column in frame.columns:
        if frame[column].dtype != "object":
            continue

        non_null = frame[column].dropna()
        if non_null.empty:
            continue

        if non_null.map(lambda value: isinstance(value, nested_types)).any():
            def serialise(value: Any) -> Any:
                if isinstance(value, nested_types):
                    return json.dumps(
                        value,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                if value is None:
                    return None
                return str(value)

            frame[column] = frame[column].map(serialise)

    return frame


def _save_parquet(records: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _parquet_safe_frame(records).to_parquet(path, index=False)


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def collect_official_fpl(*, include_player_summaries: bool = False) -> Path:
    """Collect one immutable Official FPL snapshot and return its folder."""
    stamp = utc_stamp()
    snapshot = RAW_ROOT / "official_fpl" / stamp
    snapshot.mkdir(parents=True, exist_ok=True)

    bootstrap = _get_json(f"{FPL_API}/bootstrap-static/")
    fixtures = _get_json(f"{FPL_API}/fixtures/")

    # Raw JSON is the lossless source of truth.
    _save_json(bootstrap, snapshot / "bootstrap-static.json")
    _save_json(fixtures, snapshot / "fixtures.json")

    events = bootstrap.get("events", [])
    players = bootstrap.get("elements", [])
    teams = bootstrap.get("teams", [])

    # Parquet is the analytics-friendly representation. Nested API values are
    # serialised to JSON text so empty structs/lists cannot crash PyArrow.
    _save_parquet(events, snapshot / "events.parquet")
    _save_parquet(players, snapshot / "players.parquet")
    _save_parquet(teams, snapshot / "teams.parquet")
    _save_parquet(fixtures, snapshot / "fixtures.parquet")

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
