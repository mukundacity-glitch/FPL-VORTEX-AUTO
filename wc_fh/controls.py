from __future__ import annotations

import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

POSITIONS = (("GK", 2), ("DEF", 5), ("MID", 5), ("FWD", 3))
SLOTS = tuple(f"{position.lower()}_{index}" for position, count in POSITIONS for index in range(1, count + 1))
CHIPS = ("Wildcard", "Free Hit")
CHIP_OPTIONS = (*CHIPS, "Both")
TASKS = ("Render and upload", "Refresh player picker")
AUTO = "AUTO - model recommendation"


def request_controls(environment: Mapping[str, str] | None = None) -> dict[str, Any]:
    environment = os.environ if environment is None else environment
    task = environment.get("WC_FH_TASK", TASKS[0]).strip()
    chip = environment.get("WC_FH_CHIP", "Wildcard").strip()
    selection = environment.get("WC_FH_SELECTION", "Auto").strip()
    if task not in TASKS or chip not in CHIP_OPTIONS or selection not in {"Auto", "Manual"}:
        raise ValueError("Select a valid WC- FH task, chip and Auto/Manual mode.")
    raw = environment.get("WC_FH_INPUTS", "{}")
    inputs = json.loads(raw)
    if not isinstance(inputs, dict):
        raise ValueError("WC_FH_INPUTS must be a JSON object.")
    def imported_ids(field: str) -> list[int]:
        imported = str(inputs.get(field, "") or "").strip()
        if not imported or selection != "Manual" or task != TASKS[0]:
            return []
        parts = re.split(r"[,\s]+", imported)
        if len(parts) != 15 or any(not re.fullmatch(r"[1-9]\d*", value) for value in parts):
            raise ValueError(f"{field}: paste exactly 15 positive FPL player IDs, separated by commas or spaces.")
        player_ids = list(map(int, parts))
        if len(set(player_ids)) != 15:
            raise ValueError(f"{field}: the imported manual squad contains duplicate players.")
        return player_ids

    chips = CHIPS if chip == "Both" else (chip,)
    squads = {selected_chip: imported_ids("wildcard_squad" if selected_chip == "Wildcard" else "free_hit_squad")
              for selected_chip in chips}
    # A chip-specific import takes precedence over the common import and dropdowns.
    player_ids = imported_ids("manual_squad") if any(not ids for ids in squads.values()) else []
    overrides: dict[str, int | None] = {}
    for slot in SLOTS:
        value = str(inputs.get(slot, AUTO) or AUTO)
        if value.startswith("AUTO"):
            overrides[slot] = None
            continue
        match = re.search(r"\bID\s+(\d+)\b", value)
        if match is None:
            raise ValueError(f"Invalid player dropdown value for {slot}: {value!r}")
        overrides[slot] = int(match.group(1))
    return dict(task=task, chip=chip, selection=selection, player_ids=player_ids, overrides=overrides,
                squads=squads)


def requested_chips(controls: dict[str, Any]) -> tuple[str, ...]:
    return CHIPS if controls["chip"] == "Both" else (controls["chip"],)


def controls_for_chip(controls: dict[str, Any], chip: str) -> dict[str, Any]:
    if chip not in requested_chips(controls):
        raise ValueError(f"{chip} was not requested.")
    return dict(controls, chip=chip, multiple_chips=controls["chip"] == "Both" or controls.get("multiple_chips", False),
                player_ids=controls.get("squads", {}).get(chip) or controls["player_ids"])


def chip_slug(chip: str) -> str:
    return {"Wildcard": "wildcard", "Free Hit": "free_hit"}[chip]


def selected_squad(catalog: dict[str, Any], controls: dict[str, Any]) -> tuple[list[int], int]:
    players = {int(player["id"]): player for player in catalog["players"]}
    chip = controls["chip"]
    if chip not in CHIPS:
        raise ValueError("Select each chip separately when resolving a Both request.")
    controls = controls_for_chip(controls, chip)
    recommended = catalog["recommended"][chip]
    if controls["selection"] == "Auto":
        chosen = list(recommended)
    elif controls["player_ids"]:
        chosen = controls["player_ids"]
    else:
        chosen = [controls["overrides"].get(slot) or recommended[index] for index, slot in enumerate(SLOTS)]
    if len(chosen) != 15 or len(set(chosen)) != 15:
        raise ValueError("Choose 15 different players; a replacement may already occupy another AUTO slot.")
    missing = sorted(set(chosen) - players.keys())
    if missing:
        raise ValueError(f"These FPL player IDs are absent from the current player list: {missing}")
    expected = {position: count for position, count in POSITIONS}
    actual = Counter(players[player_id]["position"] for player_id in chosen)
    if dict(actual) != expected:
        raise ValueError(f"Squad needs 2 GK, 5 DEF, 5 MID, 3 FWD; found {dict(actual)}.")
    if not controls["player_ids"]:
        for player_id, slot in zip(chosen, SLOTS):
            if players[player_id]["position"].lower() != slot.split("_")[0]:
                raise ValueError(f"Player {player_id} has the wrong position for {slot}.")
    clubs = Counter(players[player_id]["club_id"] for player_id in chosen)
    if max(clubs.values()) > 3:
        raise ValueError("The selected squad contains more than three players from one club.")
    ordered = [player_id for position, _ in POSITIONS for player_id in chosen
               if players[player_id]["position"] == position]
    total = sum(int(players[player_id]["price_tenths"]) for player_id in ordered)
    return ordered, total


def save_selection(catalog: dict[str, Any], controls: dict[str, Any], root: Path) -> list[int]:
    chosen, total = selected_squad(catalog, controls)
    by_id = {int(player["id"]): player for player in catalog["players"]}
    report = dict(chip=controls["chip"], selection=controls["selection"],
                  selected_ids=chosen, total_cost_millions=total / 10,
                  price_source="current official FPL prices", players=[by_id[player_id] for player_id in chosen])
    stem = "selection_" + chip_slug(controls["chip"]) if controls.get("multiple_chips") else "selection"
    path = root / "data" / f"{stem}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [f"### {controls['chip']} · {controls['selection']}",
             f"**Total squad cost: £{total / 10:.1f}m · 15/15 players**", "",
             "| Position | Player | Club | Price |", "| --- | --- | --- | --- |"]
    lines.extend(f"| {by_id[player_id]['position']} | {by_id[player_id]['name']} | "
                 f"{by_id[player_id]['club']} | £{by_id[player_id]['price_tenths'] / 10:.1f}m |"
                 for player_id in chosen)
    text = "\n".join(lines) + "\n"
    (root / "data" / f"{stem}.md").write_text(text, encoding="utf-8")
    print(text, flush=True)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as handle:
            handle.write(text)
    return chosen


if __name__ == "__main__":
    controls = request_controls()
    print(f"WC- FH: {controls['task']} / {controls['chip']} / {controls['selection']} / Final 4K")
