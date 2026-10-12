from __future__ import annotations

import ast
import base64
import json
import types
from pathlib import Path
from typing import Any


WC_FH_IMAGE_SOURCE = '"""FPL VORTEX player images: official FPL code -> Premier League photo chain -> VORTEX fallback.\n\nIdentity is never guessed from a player name and no AI/synthetic player portrait is generated.\n"""\nfrom pathlib import Path\nfrom datetime import datetime, timezone\nfrom io import BytesIO\nfrom PIL import Image\nimport json\nimport tempfile\nimport requests\n\nPHOTO_RESOLVER_VERSION = "official-fpl-code-four-endpoints-v4"\nPL_PHOTO_BASE_CURRENT = "https://resources.premierleague.com/premierleague25/photos/players"\nPL_PHOTO_BASE_LEGACY = "https://resources.premierleague.com/premierleague/photos/players"\n\n_session = requests.Session()\n_session.headers.update({\n    "User-Agent": "Mozilla/5.0 FPL-VORTEX/4.0",\n    "Accept": "image/avif,image/webp,image/png,image/*,*/*;q=0.8",\n})\n\n\ndef _row(player):\n    if hasattr(player, "to_dict"):\n        player = player.to_dict()\n    return dict(player or {})\n\n\ndef _player_code(player):\n    row = _row(player)\n    value = row.get("photo_code") or row.get("code") or row.get("player_code")\n    if value in (None, ""):\n        return None\n    text = str(value).strip()\n    if text.lower().startswith("p"):\n        text = text[1:]\n    if ".png" in text.lower():\n        text = text.split(".", 1)[0]\n    try:\n        code = int(float(text))\n    except (TypeError, ValueError):\n        return None\n    return code if code > 0 else None\n\n\ndef official_photo_urls(fpl_player, season=None):\n    """Exact shared FPL VORTEX photo chain, ordered from preferred to legacy."""\n    code = _player_code(fpl_player)\n    if code is None:\n        return []\n    return [\n        f"{PL_PHOTO_BASE_CURRENT}/500x500/{code}.png",\n        f"{PL_PHOTO_BASE_CURRENT}/250x250/{code}.png",\n        f"{PL_PHOTO_BASE_LEGACY}/250x250/p{code}.png",\n        f"{PL_PHOTO_BASE_LEGACY}/110x140/p{code}.png",\n    ]\n\n\ndef _valid_image(payload):\n    if not isinstance(payload, (bytes, bytearray)) or len(payload) < 300:\n        return False\n    try:\n        with Image.open(BytesIO(payload)) as image:\n            image.verify()\n        with Image.open(BytesIO(payload)) as image:\n            image.load()\n        return True\n    except Exception:\n        return False\n\n\ndef _download_image(url):\n    try:\n        response = _session.get(url, timeout=(4, 10))\n        if response.status_code != 200:\n            return None\n        content_type = str(response.headers.get("content-type", "")).lower()\n        if content_type and not content_type.startswith("image/"):\n            return None\n        payload = bytes(response.content)\n        return payload if _valid_image(payload) else None\n    except requests.RequestException:\n        return None\n\n\ndef _save_png(payload, path):\n    path = Path(path)\n    path.parent.mkdir(parents=True, exist_ok=True)\n    with tempfile.NamedTemporaryFile(dir=path.parent,prefix=path.name+\'.\',suffix=\'.part\',delete=False) as handle:\n        temporary=Path(handle.name)\n    try:\n        with Image.open(BytesIO(payload)) as image:\n            image.convert("RGBA").save(temporary,format="PNG")\n        temporary.replace(path)\n    finally:temporary.unlink(missing_ok=True)\n    return path\n\n\ndef _official_fpl_shirt_urls(team_code):\n    return [\n        f"https://fantasy.premierleague.com/dist/img/shirts/standard/shirt_{team_code}-220.png",\n        f"https://fantasy.premierleague.com/dist/img/shirts/standard/shirt_{team_code}-110.png",\n    ]\n\n\ndef resolve_media(player_ids, work, bootstrap, core_root=None):\n    """Resolve current players strictly by official FPL id/code metadata."""\n    work = Path(work)\n    directory = work / "wildcard_player_media"\n    directory.mkdir(parents=True, exist_ok=True)\n\n    players = {int(player["id"]): player for player in bootstrap["elements"]}\n    teams = {int(team["id"]): team for team in bootstrap["teams"]}\n\n    deadlines = [\n        str(event.get("deadline_time") or "")\n        for event in bootstrap.get("events", [])\n        if event.get("deadline_time")\n    ]\n    year = min(int(value[:4]) for value in deadlines) if deadlines else datetime.now(timezone.utc).year\n    season = f"{year}-{year + 1}"\n\n    result = {}\n    for player_id in sorted(set(map(int, player_ids))):\n        if player_id not in players:\n            raise KeyError(f"Official FPL player id {player_id} is not present in bootstrap-static.")\n\n        player = players[player_id]\n        photo_code = _player_code(player)\n        team_id = int(player.get("team") or 0)\n        team = teams.get(team_id, {})\n        team_code = int(team.get("code") or 0)\n        team_short = str(team.get("short_name") or team.get("name") or "")\n        if photo_code is None:\n            raise RuntimeError(\n                f"Official FPL player {player_id} ({player.get(\'web_name\')}) has no usable player code."\n            )\n\n        path = directory / f"player_{player_id}_{photo_code}_{team_code}_official-v4.png"\n        stamp = path.with_suffix(".source.json")\n\n        old = {}\n        if stamp.exists():\n            try:\n                old = json.loads(stamp.read_text(encoding="utf-8"))\n            except (ValueError, OSError):\n                old = {}\n\n        try:\n            checked = datetime.fromisoformat(str(old.get("checked_at") or ""))\n        except (TypeError, ValueError):\n            checked = None\n\n        fresh = (\n            checked is not None\n            and checked.tzinfo is not None\n            and (datetime.now(timezone.utc) - checked).total_seconds() < 86400\n        )\n        valid_cache = (\n            path.is_file()\n            and fresh\n            and old.get("resolver_version") == PHOTO_RESOLVER_VERSION\n            and int(old.get("player_id") or -1) == player_id\n            and int(old.get("photo_code") or -1) == photo_code\n            and int(old.get("team_code") or -1) == team_code\n        )\n        if valid_cache:\n            try:\n                with Image.open(path) as image:\n                    image.verify()\n                with Image.open(path) as image:\n                    image.load()\n            except Exception:\n                valid_cache = False\n\n        if valid_cache:\n            item = dict(old)\n            item.update(\n                photo=str(path.resolve()),\n                player_id=player_id,\n                name=str(player.get("web_name") or ""),\n                photo_code=photo_code,\n                team_code=team_code,\n                club=team_short,\n                badge=None,\n            )\n            result[player_id] = item\n            continue\n\n        payload = None\n        source_url = None\n        photo_kind = "missing"\n\n        for candidate in official_photo_urls({"photo_code": photo_code}):\n            payload = _download_image(candidate)\n            if payload is not None:\n                source_url = candidate\n                photo_kind = "player"\n                break\n\n        # VORTEX fallback: official current FPL club shirt, never another person\'s face.\n        if payload is None and team_code > 0:\n            for candidate in _official_fpl_shirt_urls(team_code):\n                payload = _download_image(candidate)\n                if payload is not None:\n                    source_url = candidate\n                    photo_kind = "shirt"\n                    break\n\n        if payload is not None:\n            _save_png(payload, path)\n            photo_path = str(path.resolve())\n        else:\n            photo_path = None\n\n        item = {\n            "photo": photo_path,\n            "photo_kind": photo_kind,\n            "source_url": source_url,\n            "player_id": player_id,\n            "name": str(player.get("web_name") or ""),\n            "photo_code": photo_code,\n            "team_code": team_code,\n            "club": team_short,\n            "badge": None,\n            "resolver": (\n                "official FPL player_code -> Premier League 500x500 -> Premier League 250x250 "\n                "-> legacy Premier League 250x250 -> legacy Premier League 110x140 -> VORTEX shirt fallback"\n            ),\n            "resolver_version": PHOTO_RESOLVER_VERSION,\n            "season": season,\n            "identity_rule": "Official FPL player id -> official FPL code/photo_code only; no name matching",\n            "checked_at": datetime.now(timezone.utc).isoformat(),\n            "image_normalization": "official-original-aspect-v4",\n        }\n\n        if payload is not None:\n            stamp.write_text(json.dumps(item, indent=2), encoding="utf-8")\n        result[player_id] = item\n\n    (work / "wildcard_player_media_audit.json").write_text(\n        json.dumps(result, indent=2),\n        encoding="utf-8",\n    )\n    return result\n'

def patch_player_images(payload: dict[str, Any]) -> None:
    cells = payload.get("cells") or []
    targets = []
    for cell in cells:
        if cell.get("cell_type") != "code":
            continue
        source = cell.get("source") or []
        source = "".join(source) if isinstance(source, list) else str(source)
        if "def vx_player_visual(" in source:
            targets.append((cell, source))
    if len(targets) != 1:
        raise RuntimeError("Day 2 requires exactly one shared player-image resolver")
    cell, source = targets[0]
    tree = ast.parse(source)
    obsolete = {
        "_vxv_rgb_bins", "_vxv_dist", "_vxv_portrait_match", "_vxv_bright_colour",
        "_vxv_hex", "_vxv_badge_palette", "_vxv_contrast_text", "_vxv_jersey_svg",
    }
    lines = source.splitlines(keepends=True)
    replacements = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name in obsolete:
            replacements.append((node.lineno - 1, node.end_lineno, ""))
        elif node.name == "vx_player_visual":
            replacements.append((node.lineno - 1, node.end_lineno, '''def vx_player_visual(player_row):
    raise RuntimeError("Install the Day 2 WC/FH player-image resolver before rendering")
'''))
    for start, end, replacement in sorted(replacements, reverse=True):
        lines[start:end] = [replacement]
    source = "".join(lines)
    source = source.replace(
        "# visually compatible with the current club badge; otherwise a current-team\n# jersey SVG is generated automatically from the badge palette. Real squad\n# number is printed only when the live FPL payload supplies one.",
        "# resolved by the exact WC/FH official image module installed by the runner.\n# No name matching, colour rejection, or generated player jersey is used.",
    )
    compile(source, "DAY2_FINAL.ipynb::player-images", "exec")
    cell["source"] = source.splitlines(keepends=True)


def install_player_images(namespace: dict[str, Any]) -> None:
    original = namespace.get("vx_player_visual")
    if original is None or getattr(original, "_day2_wc_fh_player_images", False):
        return
    bootstrap = namespace.get("bootstrap")
    if not isinstance(bootstrap, dict) or not bootstrap.get("elements") or not bootstrap.get("teams"):
        raise RuntimeError("Day 2 WC/FH images require the existing live FPL bootstrap")
    module = types.ModuleType("vortex_day2_wc_fh_player_images")
    exec(WC_FH_IMAGE_SOURCE, module.__dict__)
    work = Path(namespace["DATA_DIR"]) / "wc_fh_official_images"
    players = {int(player["id"]): player for player in bootstrap["elements"]}
    teams = {int(team["id"]): team for team in bootstrap["teams"]}
    records: dict[int, dict[str, Any]] = {}

    def player_visual(player_row: dict[str, Any]) -> dict[str, Any]:
        row = player_row if isinstance(player_row, dict) else {}
        try:
            player_id = int(row.get("id") or row.get("player_id") or 0)
        except (TypeError, ValueError) as error:
            raise ValueError("Day 2 player image requires an official FPL player ID") from error
        record = module.resolve_media([player_id], work, bootstrap)[player_id]
        records[player_id] = record
        (work / "wildcard_player_media_audit.json").write_text(
            json.dumps(records, indent=2), encoding="utf-8"
        )
        live_player = players[player_id]
        team = teams.get(int(live_player.get("team") or 0), {})
        team_code = int(team.get("code") or 0)
        badge_url = str((f"https://resources.premierleague.com/premierleague/badges/70/t{team_code}.png" if team_code else "") or row.get("badge") or "")
        badge_blob, badge_mime = namespace["_vxv_fetch_bytes"](badge_url)
        photo = record.get("photo")
        chosen = "data:image/png;base64," + base64.b64encode(Path(photo).read_bytes()).decode("ascii") if photo else ""
        return {
            "src": chosen, "fallback": chosen,
            "kind": "photo" if record["photo_kind"] == "player" else "jersey",
            "reason": "official_portrait" if record["photo_kind"] == "player" else "official_club_shirt" if record["photo_kind"] == "shirt" else "image_unavailable",
            "badge": namespace["_vxv_data_uri"](badge_blob, badge_mime) if badge_blob else badge_url,
            "squad_number": live_player.get("squad_number"),
            "display_name": str(row.get("name") or live_player.get("web_name") or "PLAYER").strip(),
        }

    setattr(player_visual, "_day2_wc_fh_player_images", True)
    namespace["vx_player_visual"] = player_visual
    namespace["_day2_wc_fh_image_module"] = module
    namespace.get("_VXV_VISUAL_CACHE", {}).clear()
    print("[DAY 2] Player images: exact WC/FH official four-endpoint resolver and identity/club-aware 24-hour cache")
