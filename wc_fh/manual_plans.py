from __future__ import annotations

import copy
import itertools
import math
from collections import Counter
from typing import Any


def finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (ValueError, TypeError):
        return None
    return number if math.isfinite(number) else None


def choose_lineup(cards: list[dict[str, Any]], gameweek: int, *, keeper_id: int | None = None) -> dict[str, Any]:
    available = [card for card in cards if finite(card["weekly_points"].get(gameweek)) is not None
                 and (card["position"] != "GK" or keeper_id is None or card["id"] == keeper_id)]
    best = None
    for candidate in itertools.combinations(available, 11):
        counts = Counter(player["position"] for player in candidate)
        if counts["GK"] != 1 or not 3 <= counts["DEF"] <= 5 or not 2 <= counts["MID"] <= 5 or not 1 <= counts["FWD"] <= 3:
            continue
        ranked = sorted(candidate, key=lambda player: (-player["weekly_points"][gameweek], player["id"]))
        points = sum(player["weekly_points"][gameweek] for player in candidate)
        objective = points + ranked[0]["weekly_points"][gameweek]
        if best is None or objective > best[0]:
            best = objective, list(candidate), ranked, points, counts
    if best is None:
        raise ValueError(f"Your manual squad has no legal XI with verified projections for GW{gameweek}.")
    objective, lineup, ranked, points, counts = best
    return dict(gameweek=gameweek, starting_xi=[dict(player, xpts=player["weekly_points"][gameweek]) for player in lineup],
                captain_id=ranked[0]["id"], vice_captain_id=ranked[1]["id"],
                formation=f"{counts['DEF']}-{counts['MID']}-{counts['FWD']}", xi_xpts=points,
                captain_adjusted_xpts=objective, status="Manual fixed squad; model-optimized legal XI and captain",
                all_players=[dict(id=player["id"], xpts=player["weekly_points"].get(gameweek)) for player in cards])


def build_manual_plan(namespace: dict[str, Any], chip: str, selected_ids: list[int]) -> dict[str, Any]:
    from wildcard_slide3 import build_bench_boost_path, prepare_pool
    from free_hit_plan import calculate_free_hit_player_score, get_next_gw_fixtures, validate_free_hit_plan

    engine = namespace["VX_WC_ENGINE"]
    bootstrap = namespace["_bootstrap"]
    teams = {int(team["id"]): team for team in bootstrap["teams"]}
    players = {int(player["id"]): player for player in bootstrap["elements"]}
    models = {int(row["player_id"]): row for row in engine.pool.to_dict("records")}
    weekly = engine.scores.to_dict("records")
    lookup = {(int(row["player_id"]), int(row["gameweek"])): row for row in weekly}
    weeks = list(engine.primary) if chip == "Wildcard" else [int(namespace["VX_CURRENT_GW"])]
    multiplier = int(bootstrap["game_settings"]["ui_currency_multiplier"])
    fixture_map = get_next_gw_fixtures(engine.fixtures, weeks[0])
    cards = []
    for player_id in selected_ids:
        raw = players[player_id]
        model = models.get(player_id, {})
        first = lookup.get((player_id, weeks[0]), {})
        projected = {gameweek: finite(lookup.get((player_id, gameweek), {}).get("gameweek_expected_points")) for gameweek in weeks}
        total = sum(projected.values()) if all(value is not None for value in projected.values()) else None
        position = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}[int(raw["element_type"])]
        team = teams[int(raw["team"])]
        start = finite(model.get("p_start"))
        minutes = finite(model.get("expected_minutes"))
        if start is None:
            start = finite(first.get("p_start"))
        if minutes is None:
            match_minutes = finite(first.get("expected_minutes"))
            minutes = match_minutes / max(1, int(first.get("fixture_count") or 1)) if match_minutes is not None else None
        secure = bool(start is not None and start >= 0.8 and minutes is not None and minutes >= 65 and raw.get("status") == "a")
        fixtures = [dict(fixture, opponent=teams[fixture["opponent_id"]]["short_name"])
                    for fixture in fixture_map.get(int(raw["team"]), [])]
        card = dict(id=player_id, name=raw["web_name"], full_name=(raw.get("first_name", "")+" "+raw.get("second_name", "")).strip(),
            position=position, team_id=int(raw["team"]), club=team["short_name"], club_name=team["name"], photo_code=raw.get("code"),
            price=int(raw["now_cost"])/multiplier, cost_units=int(raw["now_cost"]), secure=secure,
            nailed=bool(secure and position == "GK" and start >= .9 and minutes >= 80),
            viable=bool(start is not None and start >= .35 and minutes is not None and minutes >= 25),
            minutes=minutes, starting_probability=start, projected_points=total, first_projection=projected[weeks[0]],
            weekly_points=projected, score=total or 0.0, availability=raw.get("status", "unknown"),
            reason="Manually selected; current model projection and team news apply", fixtures=fixtures,
            fixture_text=", ".join(f"{fixture['opponent']} ({'H' if fixture['home'] else 'A'})" for fixture in fixtures),
            latest_news_checked=False, risk_note=None if secure else "Recheck minutes and availability",
            analysis=dict(role="Manually selected", xgi90=finite(raw.get("expected_goal_involvements_per_90")),
                          xg90=finite(raw.get("expected_goals_per_90")), xa90=finite(raw.get("expected_assists_per_90"))))
        if chip == "Free Hit":
            card.update(calculate_free_hit_player_score(raw, dict(first, news_monitor=model.get("news_monitor", False)),
                fixtures, sum(bool(event.get("finished")) for event in bootstrap["events"])))
        cards.append(card)
    total_cost = sum(player["price"] for player in cards)
    budget = float(engine.budget)
    if chip == "Free Hit" and total_cost > budget:
        budget = total_cost
    if total_cost > budget + 1e-8:
        raise ValueError(f"Manual {chip} costs £{total_cost:.1f}m, above the model budget £{budget:.1f}m.")
    plans = []
    keeper_id = None
    for gameweek in weeks:
        row = choose_lineup(cards, gameweek, keeper_id=keeper_id)
        if chip == "Wildcard" and keeper_id is None:
            keeper_id = next(player["id"] for player in row["starting_xi"] if player["position"] == "GK")
        plans.append(row)
    by_id = {player["id"]: player for player in cards}
    opening = plans[0]
    xi_ids = {player["id"] for player in opening["starting_xi"]}
    bench = sorted([player for player in cards if player["id"] not in xi_ids],
                   key=lambda player: (player["position"] != "GK", -(player["first_projection"] or 0), player["id"]))
    plan = dict(chip="wildcard" if chip == "Wildcard" else "freehit", version="wc-fh-manual-v1", manual_selection=True,
        squad=cards, starting_xi=[by_id[player["id"]] for player in opening["starting_xi"]], bench=bench,
        formation=opening["formation"], total_cost=round(total_cost, 1), bank=round(budget-total_cost, 1), budget=budget,
        next_gw=weeks[0], end_gw=weeks[-1], analysis_gws=weeks, gameweek_plans=plans,
        captain=by_id[opening["captain_id"]], vice_captain=by_id[opening["vice_captain_id"]],
        projected_points_total=opening["xi_xpts"], captain_adjusted_xpts=opening["captain_adjusted_xpts"],
        selection_summary=dict(manual=True, secure_starters=sum(player["secure"] for player in opening["starting_xi"]),
                               default_bench_boost=False, bench_policy="Manager-selected reserves"),
        data_health=dict(valid=True, optimality_proven=True, solver_status=0, solver_gap=0.0,
            spend_optimality_proven=False, points_optimality_proven=True,
            objective="Fixed manual squad; optimize the legal XI and captain only", warnings=[]),
        player_reasons={str(player["id"]): player["reason"] for player in cards})
    if chip == "Wildcard":
        context = dict(models=engine.pool.to_dict("records"), weekly_models=weekly, currency_multiplier=multiplier)
        pool = prepare_pool(bootstrap["elements"], bootstrap["teams"], context, weeks)
        plan["bench_boost_path"] = build_bench_boost_path(plan, pool, engine.fixtures, engine.bb)
        plan["bench_boost_upgrade_target"] = next(iter(plan["bench_boost_path"]["targets"]), None)
        engine.slide3_plan = plan
    else:
        previous = namespace.get("VX_FREEHIT_SQUAD_PLAN", {})
        plan.update(next_gw_fixtures=fixture_map, subtitle="Your manually selected one-Gameweek squad",
                    press_conference_status=copy.deepcopy(previous.get("press_conference_status", {})))
        validate_free_hit_plan(plan)
        namespace["VX_FREEHIT_SQUAD_PLAN"] = plan
        namespace["_VX_WC_FH_MANUAL_FREEHIT_PLAN"] = plan
    if {player["id"] for player in plan["squad"]} != set(selected_ids):
        raise RuntimeError("The manual plan changed the selected fifteen players.")
    return plan


def install_manual_narration() -> None:
    import wildcard_slide3

    original = wildcard_slide3.generate_wildcard_slide3_narration
    if getattr(original, "wc_fh_manual", False):
        return
    def narrate(plan, *args, **kwargs):
        segments = original(plan, *args, **kwargs)
        if not plan.get("manual_selection"):
            return segments
        by_id = {player["id"]: player for player in plan["squad"]}
        for segment in segments:
            key = segment.get("segment_id", "")
            if key == "intro":
                segment["narration_text"] = (f"This is your manually selected Wildcard squad for Gameweeks {plan['next_gw']} "
                    f"to {plan['end_gw']}. The model selects the starting eleven and captain from these fifteen players. "
                    f"The opening formation is {plan['formation']}. Recheck team news before the deadline.")
            elif key == "bench_intro":
                segment["narration_text"] = "These are your selected reserves. Review their minutes and availability before confirming the squad."
            elif key.startswith("bench_") and segment.get("player_id") in by_id:
                player = by_id[segment["player_id"]]
                segment["narration_text"] = f"{player['full_name'] or player['name']} costs £{player['price']:.1f} million. This is your selected reserve; playing time is not guaranteed."
        return segments
    narrate.wc_fh_manual = True
    wildcard_slide3.generate_wildcard_slide3_narration = narrate
