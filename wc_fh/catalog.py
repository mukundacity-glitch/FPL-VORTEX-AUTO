from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .controls import AUTO, CHIPS, POSITIONS, SLOTS


def build_catalog(bootstrap: dict[str, Any], projections: Iterable[dict[str, Any]],
                  squads: dict[str, list[int]], *, budget: float = 100.0,
                  generated_at: str | None = None) -> dict[str, Any]:
    rows = list(projections)
    gameweek = min(int(row["gameweek"]) for row in rows)
    horizon = set(sorted({int(row["gameweek"]) for row in rows})[:6])
    scores: dict[int, dict[str, float]] = {}
    for row in rows:
        player_id = int(row["player_id"])
        score = float(row["gameweek_expected_points"])
        if not math.isfinite(score):
            raise ValueError(f"Non-finite model projection for player {player_id}")
        record = scores.setdefault(player_id, {"Wildcard": 0.0, "Free Hit": 0.0})
        if int(row["gameweek"]) in horizon:
            record["Wildcard"] += score
        if int(row["gameweek"]) == gameweek:
            record["Free Hit"] += score
    teams = {int(team["id"]): str(team["short_name"]) for team in bootstrap["teams"]}
    positions = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
    players = [dict(id=int(player["id"]), name=str(player["web_name"]),
                    club=teams[int(player["team"])], club_id=int(player["team"]),
                    position=positions[int(player["element_type"])],
                    price_tenths=int(player["now_cost"]), status=str(player.get("status", "a")),
                    projected=int(player["id"]) in scores,
                    scores={chip: round(scores.get(int(player["id"]), {}).get(chip, 0.0), 2) for chip in CHIPS})
               for player in bootstrap["elements"]]
    by_id = {player["id"]: player for player in players}
    recommendations: dict[str, list[int]] = {}
    for chip, ids in squads.items():
        if len(ids) != 15 or len(set(ids)) != 15 or set(ids) - by_id.keys():
            raise ValueError(f"Invalid model-recommended {chip} squad")
        recommendations[chip] = [player_id for position, _ in POSITIONS for player_id in ids
                                 if by_id[player_id]["position"] == position]
    return dict(version=1, generated_at=generated_at or datetime.now(timezone.utc).isoformat(),
                gameweek=gameweek, budget_millions=float(budget),
                source="Official FPL bootstrap + notebook model projections and optimized squads",
                players=players, recommended=recommendations)


def save_catalog(catalog: dict[str, Any], directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "players.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    template = Path(__file__).with_name("picker_template.html").read_text(encoding="utf-8")
    payload = json.dumps(catalog, ensure_ascii=False).replace("<", "\\u003c")
    (directory / "squad_picker.html").write_text(template.replace("__PLAYER_DATA__", payload), encoding="utf-8")


def player_label(player: dict[str, Any], recommended: dict[str, list[int]]) -> str:
    chips = ["WC" if chip == "Wildcard" else "FH" for chip, ids in recommended.items() if player["id"] in ids]
    marker = f"★ {'/'.join(chips)} | " if chips else ""
    score_label = (f"WC {player['scores']['Wildcard']:.2f} / FH {player['scores']['Free Hit']:.2f} xPts"
                   if player.get("projected", True) else "not projected")
    return (f"{marker}{player['name']} ({player['club']}) — ID {player['id']} — "
            f"£{player['price_tenths'] / 10:.1f}m — {score_label}")


def update_workflow(catalog: dict[str, Any], path: Path) -> None:
    recommended = catalog["recommended"]
    recommended_ids = set().union(*(set(ids) for ids in recommended.values()))
    players = sorted(catalog["players"], key=lambda player: (
        player["id"] not in recommended_ids,
        -max(player["scores"]["Wildcard"] / 6.0, player["scores"]["Free Hit"]),
        player["name"].casefold(), player["id"]))
    lines = ["    # BEGIN PLAYER DROPDOWNS", f"    # Player/price snapshot: {catalog['generated_at']}; refresh with wc_fh.catalog."]
    for slot in SLOTS:
        position = slot.split("_")[0].upper()
        lines.extend([f"      {slot}:", f"        description: {json.dumps(position + ' ' + slot.split('_')[1] + ' | Manual only; recommended picks first')}",
                      "        type: choice", "        required: false", f"        default: {json.dumps(AUTO)}", "        options:",
                      f"          - {json.dumps(AUTO)}"])
        lines.extend("          - " + json.dumps(player_label(player, recommended), ensure_ascii=False)
                     for player in players if player["position"] == position)
    lines.append("    # END PLAYER DROPDOWNS")
    source = path.read_text(encoding="utf-8")
    begin, end = "    # BEGIN PLAYER DROPDOWNS", "    # END PLAYER DROPDOWNS"
    if source.count(begin) != 1 or source.count(end) != 1:
        raise ValueError("Workflow player-list markers are missing or ambiguous")
    start_index = source.index(begin)
    end_index = source.index(end) + len(end)
    path.write_text(source[:start_index] + "\n".join(lines) + source[end_index:], encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build current WC/FH dropdowns and the live-cost picker.")
    parser.add_argument("--bootstrap", type=Path, required=True)
    parser.add_argument("--projections", type=Path, required=True)
    parser.add_argument("--wildcard-squad", type=Path, required=True)
    parser.add_argument("--freehit-squad", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workflow", type=Path)
    args = parser.parse_args()
    with args.projections.open(encoding="utf-8") as handle:
        projections = list(csv.DictReader(handle))
    squads = {}
    for chip, path in [("Wildcard", args.wildcard_squad), ("Free Hit", args.freehit_squad)]:
        with path.open(encoding="utf-8") as handle:
            squads[chip] = [int(row["player_id"]) for row in csv.DictReader(handle)]
    catalog = build_catalog(json.loads(args.bootstrap.read_text()), projections, squads)
    save_catalog(catalog, args.output)
    if args.workflow:
        update_workflow(catalog, args.workflow)
    print(f"WC- FH player controls: {len(catalog['players'])} players, GW{catalog['gameweek']}")


if __name__ == "__main__":
    main()
