from __future__ import annotations

import base64
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


FPL_BASE = "https://fantasy.premierleague.com/api"
WORKFLOW_PATH = ".github/workflows/day-3.yml"
BEGIN_MARKER = "      # BEGIN GENERATED PLAYER INPUTS"
END_MARKER = "      # END GENERATED PLAYER INPUTS"
POSITION_NAMES = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}


def _get_json(session: requests.Session, url: str) -> Any:
    response = session.get(url, timeout=30)
    response.raise_for_status()
    return response.json()


def _normalized_chip_name(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _latest_permanent_public_squad(
    session: requests.Session,
    entry_id: int,
    bootstrap: dict[str, Any],
) -> list[int]:
    history = _get_json(session, f"{FPL_BASE}/entry/{entry_id}/history/")
    free_hit_events = {
        int(chip.get("event") or 0)
        for chip in (history.get("chips") or [])
        if _normalized_chip_name(chip.get("name")) == "freehit"
    }

    now = datetime.now(timezone.utc)
    candidate_events: list[int] = []
    for event in bootstrap.get("events") or []:
        event_id = int(event.get("id") or 0)
        deadline_text = str(event.get("deadline_time") or "")
        try:
            deadline = datetime.fromisoformat(deadline_text.replace("Z", "+00:00"))
        except ValueError:
            continue
        if event_id > 0 and deadline <= now:
            candidate_events.append(event_id)

    for event_id in sorted(set(candidate_events), reverse=True):
        if event_id in free_hit_events:
            continue
        try:
            picks = _get_json(
                session,
                f"{FPL_BASE}/entry/{entry_id}/event/{event_id}/picks/",
            )
        except requests.HTTPError:
            continue
        squad = [
            int(row.get("element") or 0)
            for row in (picks.get("picks") or [])
            if int(row.get("element") or 0) > 0
        ]
        if len(squad) == 15 and len(set(squad)) == 15:
            return squad

    raise RuntimeError(
        f"Could not resolve a visible permanent 15-player squad for FPL entry {entry_id}."
    )


def _choice_label(player: dict[str, Any]) -> str:
    player_id = int(player["id"])
    name = str(player.get("web_name") or player.get("second_name") or player_id).strip()
    position = POSITION_NAMES.get(int(player.get("element_type") or 0), "?")
    return f"{player_id} | {name} | {position}"


def _yaml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _build_generated_inputs(
    squad_players: list[dict[str, Any]],
    incoming_players: list[dict[str, Any]],
) -> str:
    out_options = ["NONE"] + [_choice_label(player) for player in squad_players]
    in_options = ["NONE"] + [_choice_label(player) for player in incoming_players]

    def choice_input(
        key: str,
        description: str,
        options: list[str],
        default: str = "NONE",
    ) -> list[str]:
        lines = [
            f"      {key}:",
            f"        description: {_yaml_string(description)}",
            "        required: true",
            f"        default: {_yaml_string(default)}",
            "        type: choice",
            "        options:",
        ]
        lines.extend(f"          - {_yaml_string(option)}" for option in options)
        return lines

    lines = [BEGIN_MARKER]
    for index in range(1, 6):
        lines.extend(
            choice_input(
                f"player_out_{index}",
                f"Transfer → Manual only: Player OUT {index}",
                out_options,
            )
        )
        lines.extend(
            choice_input(
                f"player_in_{index}",
                f"Transfer → Manual only: Player IN {index}",
                in_options,
            )
        )
    lines.append(END_MARKER)
    return "\n".join(lines)


def _replace_generated_block(workflow_text: str, generated_block: str) -> str:
    pattern = re.compile(
        re.escape(BEGIN_MARKER) + r".*?" + re.escape(END_MARKER),
        flags=re.DOTALL,
    )
    updated, count = pattern.subn(generated_block, workflow_text, count=1)
    if count != 1:
        raise RuntimeError(
            f"Could not find exactly one generated input block in {WORKFLOW_PATH}; found {count}."
        )
    return updated


def _github_update(workflow_text: str, token: str) -> None:
    repository = str(os.environ.get("GITHUB_REPOSITORY", "") or "").strip()
    branch = str(os.environ.get("GITHUB_REF_NAME", "main") or "main").strip()
    if not repository:
        raise RuntimeError("GITHUB_REPOSITORY is missing.")

    api_url = f"https://api.github.com/repos/{repository}/contents/{WORKFLOW_PATH}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    current = requests.get(
        api_url,
        headers=headers,
        params={"ref": branch},
        timeout=30,
    )
    current.raise_for_status()
    current_payload = current.json()
    sha = str(current_payload.get("sha") or "")
    if not sha:
        raise RuntimeError("GitHub did not return the current Day 3 workflow SHA.")

    payload = {
        "message": "Refresh Day 3 player dropdowns",
        "content": base64.b64encode(workflow_text.encode("utf-8")).decode("ascii"),
        "sha": sha,
        "branch": branch,
    }
    updated = requests.put(api_url, headers=headers, json=payload, timeout=60)
    updated.raise_for_status()
    print("✅ Day 3 player dropdowns refreshed for the next manual run.")


def refresh() -> None:
    token = str(os.environ.get("DAY3_WORKFLOW_TOKEN", "") or "").strip()
    if not token:
        print(
            "ℹ️ DAY3_WORKFLOW_TOKEN is not configured. "
            "Current dropdowns remain usable; automatic next-run refresh was skipped."
        )
        return

    entry_id = int(str(os.environ.get("FPL_ENTRY_ID", "145784") or "145784"))
    session = requests.Session()
    session.headers.update({"User-Agent": "FPL-VORTEX-Day3-GitHub/1.0"})

    bootstrap = _get_json(session, f"{FPL_BASE}/bootstrap-static/")
    elements = {
        int(player["id"]): dict(player)
        for player in (bootstrap.get("elements") or [])
    }
    squad_ids = _latest_permanent_public_squad(
        session,
        entry_id,
        bootstrap,
    )
    missing = [player_id for player_id in squad_ids if player_id not in elements]
    if missing:
        raise RuntimeError(f"Current squad IDs are missing from bootstrap data: {missing}")

    position_order = {1: 0, 2: 1, 3: 2, 4: 3}
    squad_players = sorted(
        (elements[player_id] for player_id in squad_ids),
        key=lambda player: (
            position_order.get(int(player.get("element_type") or 0), 9),
            str(player.get("web_name") or "").lower(),
        ),
    )
    incoming_players = sorted(
        (
            player
            for player_id, player in elements.items()
            if player_id not in set(squad_ids)
            and str(player.get("status") or "a") != "u"
        ),
        key=lambda player: (
            str(player.get("web_name") or "").lower(),
            int(player.get("id") or 0),
        ),
    )

    if len(squad_players) != 15:
        raise RuntimeError(f"Expected 15 current squad players, found {len(squad_players)}.")
    if not incoming_players:
        raise RuntimeError("Incoming player list is empty.")

    workflow_file = Path(WORKFLOW_PATH)
    workflow_text = workflow_file.read_text(encoding="utf-8")
    generated_block = _build_generated_inputs(squad_players, incoming_players)
    refreshed_text = _replace_generated_block(workflow_text, generated_block)

    if refreshed_text == workflow_text:
        print("✅ Day 3 player dropdowns are already current.")
        return

    _github_update(refreshed_text, token)


if __name__ == "__main__":
    refresh()
