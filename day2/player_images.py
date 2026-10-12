from __future__ import annotations

import base64
import csv
import json
import os
import re
import subprocess
import time
import unicodedata
from datetime import datetime, timezone
from difflib import SequenceMatcher
from io import BytesIO
from pathlib import Path
from typing import Any

import requests
from PIL import Image

teams_raw: dict[int, dict[str, Any]] = {}
_cache_dir = Path("/tmp/fpl-vortex-day2-player-images")
_core_restore_attempted = False
ENABLE_PLAYER_IMAGE_FALLBACK_LOCAL = True
_VX_FOTMOB_SQUARE_CACHE: dict[str, str] = {}

def vx_bytes_to_data_uri(data: bytes, mime_type: str = "image/png") -> str:
    return f"data:{mime_type};base64," + base64.b64encode(data).decode("ascii")

def vx_normalize_name(value: Any) -> str:
    value = unicodedata.normalize("NFKD", str(value or ""))
    return re.sub(r"[^a-z0-9]+", "", "".join(character for character in value if not unicodedata.combining(character)).lower())


def _fpl_full_name(player: dict[str, Any]) -> str:
    return " ".join(str(player.get(key) or "").strip() for key in ("first_name", "second_name")).strip()


def _fpl_team_name(player: dict[str, Any]) -> str:
    try:
        return str(teams_raw.get(int(player.get("team") or player.get("team_id") or 0), {}).get("name") or "")
    except (TypeError, ValueError):
        return ""


def _read_cache(name: str, max_age_hours: float = 168) -> Any:
    path = _cache_dir / name
    try:
        if time.time() - path.stat().st_mtime <= max_age_hours * 3600:
            return json.loads(path.read_text())
    except (OSError, ValueError):
        pass
    return None


def _write_cache(name: str, value: Any) -> Any:
    try:
        _cache_dir.mkdir(parents=True, exist_ok=True)
        path = _cache_dir / name
        temporary = path.with_suffix(".pending.json")
        temporary.write_text(json.dumps(value), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        pass
    return value

def fpl_photo_url(fpl_player: dict[str, Any]) -> str:
    if hasattr(fpl_player, "to_dict"):
        fpl_player = fpl_player.to_dict()
    code = fpl_player.get("code")
    if code:
        try:
            code = int(code)
        except Exception:
            pass
        return "https://resources.premierleague.com/premierleague/" + f"photos/players/250x250/p{code}.png"
    return ""

def _vx_fpl_core_players_path() -> Path | None:
    """Return the refreshed current-season FPL-Core players.csv when available."""
    explicit = globals().get("FPL_CORE_PLAYERS_PATH")
    if explicit:
        path = Path(explicit)
        if path.is_file():
            return path

    root = globals().get("DRIVE_ROOT")
    if root:
        path = Path(root) / "players.csv"
        if path.is_file():
            return path

    now = datetime.now(timezone.utc)
    season_start = now.year if now.month >= 7 else now.year - 1
    archive = Path("/content/drive/MyDrive/FPL_VORTEX_DATA/00_SOURCE_ARCHIVE/FPL_CORE/data")
    season_labels = (f"{season_start}-{season_start + 1}", f"{season_start}-{str(season_start + 1)[-2:]}")
    for season in season_labels:
        path = archive / season / "players.csv"
        if path.is_file():
            return path
    global _core_restore_attempted
    if _core_restore_attempted or not os.environ.get("RCLONE_CONFIG"):
        return None
    _core_restore_attempted = True
    for season in season_labels:
        path = archive / season / "players.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".pending.csv")
        remote = f"vortex-drive:FPL_VORTEX_DATA/00_SOURCE_ARCHIVE/FPL_CORE/data/{season}/players.csv"
        try:
            result = subprocess.run(["rclone", "copyto", remote, str(temporary), "--retries", "1", "--low-level-retries", "1"],
                                    capture_output=True, text=True, timeout=20, check=False)
            if result.returncode == 0 and temporary.is_file() and temporary.stat().st_size:
                temporary.replace(path)
                return path
        except (OSError, subprocess.TimeoutExpired):
            pass
        finally:
            temporary.unlink(missing_ok=True)
    return None


_FPL_CORE_IMAGE_INDEX = {
    "path": "",
    "mtime_ns": -1,
    "by_id": {},
    "by_name": {},
}


def _vx_load_fpl_core_image_index() -> dict[str, Any]:
    """Index FPL-Core by official FPL player id and normalized names."""
    path = _vx_fpl_core_players_path()
    if path is None:
        return {"by_id": {}, "by_name": {}}

    try:
        mtime_ns = int(path.stat().st_mtime_ns)
    except Exception:
        return {"by_id": {}, "by_name": {}}

    if (
        _FPL_CORE_IMAGE_INDEX["path"] == str(path)
        and _FPL_CORE_IMAGE_INDEX["mtime_ns"] == mtime_ns
    ):
        return _FPL_CORE_IMAGE_INDEX

    by_id = {}
    by_name = {}
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                try:
                    player_id = int(float(row.get("player_id", "")))
                    player_code = int(float(row.get("player_code", "")))
                except Exception:
                    continue

                normalized = dict(row)
                normalized["player_id"] = player_id
                normalized["player_code"] = player_code
                by_id[player_id] = normalized

                names = {
                    vx_normalize_name(row.get("web_name")),
                    vx_normalize_name(
                        " ".join(
                            p for p in (
                                str(row.get("first_name", "") or "").strip(),
                                str(row.get("second_name", "") or "").strip(),
                            )
                            if p
                        )
                    ),
                }
                for name_key in names:
                    if name_key:
                        by_name.setdefault(name_key, []).append(normalized)
    except Exception:
        return {"by_id": {}, "by_name": {}}

    _FPL_CORE_IMAGE_INDEX.update(
        {
            "path": str(path),
            "mtime_ns": mtime_ns,
            "by_id": by_id,
            "by_name": by_name,
        }
    )
    return _FPL_CORE_IMAGE_INDEX


def _vx_current_team_code(fpl_player: dict[str, Any]) -> int | None:
    row = fpl_player.to_dict() if hasattr(fpl_player, "to_dict") else dict(fpl_player)
    direct = row.get("team_code")
    if direct not in (None, ""):
        try:
            return int(direct)
        except Exception:
            pass

    try:
        team_id = int(row.get("team") or row.get("team_id") or 0)
    except Exception:
        team_id = 0

    try:
        meta = (teams_raw or {}).get(team_id, {}) or {}
        if meta.get("code") not in (None, ""):
            return int(meta["code"])
    except Exception:
        pass

    teams_obj = list(teams_raw.values())
    try:
        if hasattr(teams_obj, "loc"):
            match = teams_obj.loc[teams_obj["id"].astype(int) == team_id]
            if not match.empty:
                return int(match.iloc[-1]["code"])
    except Exception:
        pass
    return None


def _vx_fpl_core_player_code(fpl_player: dict[str, Any]) -> int | None:
    """
    Recover the Premier League player code from the refreshed FPL-Core player map.
    Exact official FPL player id is preferred; name matching is only a guarded rescue.
    """
    row = fpl_player.to_dict() if hasattr(fpl_player, "to_dict") else dict(fpl_player)
    index = _vx_load_fpl_core_image_index()
    by_id = index.get("by_id", {})
    by_name = index.get("by_name", {})

    try:
        player_id = int(row.get("id") or row.get("player_id"))
    except Exception:
        player_id = None

    if player_id is not None and player_id in by_id:
        try:
            return int(by_id[player_id]["player_code"])
        except Exception:
            pass

    wanted_names = {
        vx_normalize_name(str(row.get("web_name", "") or "")),
        vx_normalize_name(_fpl_full_name(row)),
    }
    wanted_names.discard("")
    wanted_team_code = _vx_current_team_code(row)

    candidates = []
    for key in wanted_names:
        candidates.extend(by_name.get(key, []))

    unique = {}
    for candidate in candidates:
        try:
            unique[int(candidate["player_code"])] = candidate
        except Exception:
            continue

    for candidate in unique.values():
        if wanted_team_code is not None:
            try:
                if int(candidate.get("team_code")) != int(wanted_team_code):
                    continue
            except Exception:
                continue
        return int(candidate["player_code"])
    return None


def fpl_core_photo_url(fpl_player: dict[str, Any]) -> str:
    """FPL-Core is an identity/code rescue; the actual image still comes from PL."""
    code = _vx_fpl_core_player_code(fpl_player)
    if not code:
        return ""
    return (
        "https://resources.premierleague.com/premierleague/"
        f"photos/players/250x250/p{int(code)}.png"
    )


FOTMOB_SEARCH_ENDPOINTS = (
    "https://www.fotmob.com/api/data/search/suggest",
    "https://www.fotmob.com/api/search/suggest",
)
_FOTMOB_LAST_CALL = 0.0


def _vx_extract_fotmob_player_rows(payload: Any) -> list[dict[str, Any]]:
    """Normalize both current and legacy FotMob search response shapes."""
    rows = []

    def add(value: Any) -> None:
        if not isinstance(value, dict):
            return
        value_type = str(value.get("type", "") or "").strip().lower()
        if value_type and value_type not in {"player", "squadmember"}:
            return
        if value.get("id") in (None, "") and value.get("playerId") in (None, ""):
            return
        rows.append(value)

    def walk(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict) and isinstance(item.get("suggestions"), list):
                    for suggestion in item["suggestions"]:
                        add(suggestion)
                else:
                    add(item)
        elif isinstance(value, dict):
            for key in ("players", "squadMember", "suggestions"):
                if isinstance(value.get(key), list):
                    for item in value[key]:
                        add(item)
            if isinstance(value.get("groups"), list):
                walk(value["groups"])
            if isinstance(value.get("data"), (dict, list)):
                walk(value["data"])

    walk(payload)
    deduped = {}
    for row in rows:
        try:
            player_id = int(row.get("id") or row.get("playerId"))
        except Exception:
            continue
        deduped[player_id] = row
    return list(deduped.values())


def _fotmob_search(name: str, max_age_hours: float = 168) -> list[dict[str, Any]]:
    """Search FotMob conservatively and cache the normalized player candidates."""
    global _FOTMOB_LAST_CALL
    name = str(name or "").strip()
    if not name or not ENABLE_PLAYER_IMAGE_FALLBACK_LOCAL:
        return []

    cache_name = f"fotmob_player_{vx_normalize_name(name)}.json"
    cached = _read_cache(cache_name, max_age_hours=max_age_hours)
    if (
        isinstance(cached, dict)
        and isinstance(cached.get("players"), list)
        and cached["players"]
    ):
        return cached["players"]

    # Do not let a transient FotMob/network failure poison the fallback for a week.
    recent_negative = _read_cache(cache_name, max_age_hours=1)
    if (
        isinstance(recent_negative, dict)
        and isinstance(recent_negative.get("players"), list)
        and not recent_negative["players"]
    ):
        return []

    elapsed = time.time() - _FOTMOB_LAST_CALL
    if elapsed < 0.40:
        time.sleep(0.40 - elapsed)

    last_error = ""
    for endpoint in FOTMOB_SEARCH_ENDPOINTS:
        try:
            response = requests.get(
                endpoint,
                params={"term": name, "hits": 20},
                headers={"User-Agent": "Mozilla/5.0 FPL-VORTEX/2.0"},
                timeout=15,
            )
            _FOTMOB_LAST_CALL = time.time()
            response.raise_for_status()
            payload = response.json()
            players_found = _vx_extract_fotmob_player_rows(payload)
            if players_found:
                _write_cache(
                    cache_name,
                    {
                        "players": players_found,
                        "source": endpoint,
                        "checked_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
                return players_found
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"

    _write_cache(
        cache_name,
        {
            "players": [],
            "_error": last_error,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return []


def fotmob_player_photo_url(fpl_player: dict[str, Any]) -> str:
    """Resolve a FotMob player id only when the name/team match is sufficiently strong."""
    row = fpl_player.to_dict() if hasattr(fpl_player, "to_dict") else dict(fpl_player)
    full_name = _fpl_full_name(row)
    web_name = str(row.get("web_name", "") or "").strip()
    team_name = _fpl_team_name(row)

    queries = []
    for query in (full_name, web_name):
        if query and query not in queries:
            queries.append(query)

    best = None
    best_score = -1.0
    for query in queries:
        for candidate in _fotmob_search(query):
            candidate_name = str(
                candidate.get("name")
                or candidate.get("localizedName")
                or candidate.get("playerName")
                or ""
            ).strip()
            if not candidate_name:
                continue

            name_score = max(
                SequenceMatcher(
                    None,
                    vx_normalize_name(full_name),
                    vx_normalize_name(candidate_name),
                ).ratio() if full_name else 0.0,
                SequenceMatcher(
                    None,
                    vx_normalize_name(web_name),
                    vx_normalize_name(candidate_name),
                ).ratio() if web_name else 0.0,
            )

            candidate_team = str(
                candidate.get("teamName")
                or candidate.get("info")
                or (
                    candidate.get("team", {}).get("name")
                    if isinstance(candidate.get("team"), dict)
                    else ""
                )
                or ""
            ).strip()
            team_score = (
                SequenceMatcher(
                    None,
                    vx_normalize_name(team_name),
                    vx_normalize_name(candidate_team),
                ).ratio()
                if team_name and candidate_team
                else 0.0
            )

            # Exact/near-exact names can stand alone; looser names require a club match.
            accepted = (
                name_score >= 0.96
                or (name_score >= 0.86 and team_score >= 0.55)
            )
            if not accepted:
                continue

            score = name_score + (0.35 * team_score)
            if score > best_score:
                best = candidate
                best_score = score

    if not best:
        return ""

    try:
        fotmob_id = int(best.get("id") or best.get("playerId"))
    except Exception:
        return ""

    return f"https://images.fotmob.com/image_resources/playerimages/{fotmob_id}.png"


_VX_OFFICIAL_PORTRAIT_URIS = {}

def _vx_official_portrait_uri(url: str) -> str:

    if url in _VX_OFFICIAL_PORTRAIT_URIS:
        return _VX_OFFICIAL_PORTRAIT_URIS[url]
    uri = ""
    try:
        response = requests.get(url, timeout=12, headers={"User-Agent": "Mozilla/5.0 FPL-VORTEX/2.0"})
        response.raise_for_status()
        payload = response.content
        with Image.open(BytesIO(payload)) as image:
            mime = Image.MIME.get(image.format)
            image.verify()
        if mime in {"image/png", "image/jpeg", "image/webp"}:
            uri = f"data:{mime};base64," + base64.b64encode(payload).decode("ascii")
    except Exception:
        # The resolver continues its existing real-photo/official-shirt fallback chain.
        pass
    _VX_OFFICIAL_PORTRAIT_URIS[url] = uri
    return uri

def vx_fotmob_square_photo(url: str) -> str:
    """Pad to the FPL canvas without changing a portrait's intrinsic proportions."""

    source_url = str(url or "").strip()
    if not source_url:
        return ""
    if source_url in _VX_FOTMOB_SQUARE_CACHE:
        return _VX_FOTMOB_SQUARE_CACHE[source_url]
    try:
        response = requests.get(source_url, timeout=12)
        response.raise_for_status()
        with Image.open(BytesIO(response.content)) as image:
            portrait = image.convert("RGBA")
        portrait.thumbnail((250, 250), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (250, 250), (0, 0, 0, 0))
        canvas.alpha_composite(portrait, ((250 - portrait.width) // 2, 250 - portrait.height))
        output = BytesIO()
        canvas.save(output, format="PNG")
        normalized = "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")
    except Exception as exc:
        print(f"⚠️ FotMob portrait normalization unavailable ({type(exc).__name__}); using the official shirt fallback.")
        return ""
    _VX_FOTMOB_SQUARE_CACHE[source_url] = normalized
    return normalized

_VX_REAL_FPL_JERSEY_MEMORY = {}
_vx_jersey_session = requests.Session()
_vx_jersey_session.headers.update(
    {
        "User-Agent": "Mozilla/5.0 FPL-VORTEX/2.0",
        "Accept": "image/avif,image/webp,image/apng,image/png,image/*,*/*;q=0.8",
    }
)


def _vx_valid_png_bytes(payload: bytes) -> bool:
    if not isinstance(payload, (bytes, bytearray)) or len(payload) < 300:
        return False
    try:
        image = Image.open(BytesIO(payload))
        image.verify()
        return str(image.format or "").upper() == "PNG"
    except Exception:
        return False


def vx_team_jersey_data_uri(team_id: int) -> str:
    """
    Return the REAL official FPL club shirt as an embedded PNG data URI.

    This is resolved/downloaded in Python. It does not draw a shirt, use CSS,
    or depend on browser onerror logic. The official current FPL team code is
    used in the Fantasy Premier League shirt CDN path.
    """
    try:
        team_id = int(team_id)
        meta = teams_raw.get(team_id, {}) or {}
        team_code = int(meta["code"])
    except Exception:
        return ""

    if team_code in _VX_REAL_FPL_JERSEY_MEMORY:
        return _VX_REAL_FPL_JERSEY_MEMORY[team_code]

    shirt_cache_dir = _cache_dir / "official_fpl_shirts"
    shirt_cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = shirt_cache_dir / f"shirt_{team_code}-220.png"

    if cache_path.is_file():
        try:
            cached_bytes = cache_path.read_bytes()
            if _vx_valid_png_bytes(cached_bytes):
                uri = vx_bytes_to_data_uri(cached_bytes, "image/png")
                _VX_REAL_FPL_JERSEY_MEMORY[team_code] = uri
                return uri
        except Exception:
            pass

    urls = (
        f"https://fantasy.premierleague.com/dist/img/shirts/standard/shirt_{team_code}-220.png",
        f"https://fantasy.premierleague.com/dist/img/shirts/standard/shirt_{team_code}-110.png",
    )

    for url in urls:
        try:
            response = _vx_jersey_session.get(url, timeout=12)
            ctype = str(response.headers.get("content-type", "") or "").lower()
            if (
                response.status_code == 200
                and ctype.startswith("image/")
                and _vx_valid_png_bytes(response.content)
            ):
                payload = bytes(response.content)
                tmp = cache_path.with_suffix(".tmp")
                tmp.write_bytes(payload)
                tmp.replace(cache_path)
                uri = vx_bytes_to_data_uri(payload, "image/png")
                _VX_REAL_FPL_JERSEY_MEMORY[team_code] = uri
                return uri
        except Exception:
            continue

    # Real assets only: never manufacture a synthetic SVG shirt here.
    # Do not memoize failure; a later call may succeed after a transient CDN issue.
    return ""



def install_player_images(namespace: dict[str, Any]) -> None:
    original = namespace.get("vx_player_visual")
    if original is None or getattr(original, "_day2_real_player_images", False):
        return
    global teams_raw, _cache_dir
    teams_raw = namespace.get("teams_raw", {})
    _cache_dir = Path(namespace.get("DATA_DIR") or "/tmp/fpl-vortex-day2-player-images") / "player_images"
    live_by_id = namespace.get("_VXV_PLAYER_BY_ID", {})
    visual_cache: dict[tuple[int, int, str], dict[str, Any]] = {}

    def player_visual(player_row: dict[str, Any]) -> dict[str, Any]:
        row = player_row if isinstance(player_row, dict) else {}
        try:
            player_id = int(row.get("id") or row.get("player_id") or 0)
        except (TypeError, ValueError):
            player_id = 0
        live = dict(live_by_id.get(player_id, {}) or row)
        live.setdefault("id", player_id)
        live.setdefault("web_name", str(row.get("name") or row.get("web_name") or ""))
        try:
            team_id = int(live.get("team") or row.get("team") or 0)
        except (TypeError, ValueError):
            team_id = 0
        live["team"] = team_id
        team = teams_raw.get(team_id, {})
        team_code = team.get("code") or live.get("team_code") or 0
        display_name = str(row.get("name") or live.get("web_name") or _fpl_full_name(live) or "PLAYER").strip()
        key = (player_id, team_id, display_name)
        if key in visual_cache:
            return dict(visual_cache[key])
        chosen = _vx_official_portrait_uri(fpl_photo_url(live))
        reason = "official_portrait"
        if not chosen:
            chosen = _vx_official_portrait_uri(fpl_core_photo_url(live))
            reason = "fpl_core_portrait"
        if not chosen:
            fotmob_url = fotmob_player_photo_url(live)
            chosen = vx_fotmob_square_photo(fotmob_url) if fotmob_url else ""
            reason = "fotmob_portrait"
        kind = "photo" if chosen else "jersey"
        if not chosen:
            chosen = vx_team_jersey_data_uri(team_id)
            reason = "official_club_shirt" if chosen else "image_unavailable"
        badge_url = str((f"https://resources.premierleague.com/premierleague/badges/70/t{int(team_code)}.png" if team_code else "") or row.get("badge") or "")
        badge_blob, badge_mime = namespace["_vxv_fetch_bytes"](badge_url)
        result = {
            "src": chosen, "fallback": chosen, "kind": kind, "reason": reason,
            "badge": namespace["_vxv_data_uri"](badge_blob, badge_mime) if badge_blob else badge_url,
            "squad_number": live.get("squad_number"), "display_name": display_name,
        }
        if chosen:
            visual_cache[key] = dict(result)
        return result

    setattr(player_visual, "_day2_real_player_images", True)
    namespace["vx_player_visual"] = player_visual
    namespace.get("_VXV_VISUAL_CACHE", {}).clear()
    print("[DAY 2] Player images: embedded Premier League -> FPL-Core -> FotMob -> real official club shirt")
