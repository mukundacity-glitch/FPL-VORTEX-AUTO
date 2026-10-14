"""Presentation-only transfer lists; never alter Phase-11 squad decisions."""

import math


# A blank is at most three points per completed gameweek. Retention is an
# estimate from official rounded ownership and event transfers, not a count
# of distinct managers. These gates apply only to the presentation's HOLD list.
HOLD_MAX_RECENT_POINTS = 3.0
HOLD_MIN_OWNERSHIP = 8.0
HOLD_MIN_RETAINED_PERCENT = 80.0
HOLD_PREFERRED_RETAINED_PERCENT = 95.0


def select_transfer_desk(players, *, total_managers, recent_performance,
                         effective_ownership=None, count=5):
    """Rank gross event transfers within direction; rank retained blanks by EO.

    Positive net transfers qualify for BUY; negative net transfers qualify for
    SELL. This gives each player one direction even when both volumes are high.
    HOLD excludes those leaders and requires evidence of poor recent returns,
    substantial ownership, and low selling relative to the estimated owner base.
    HOLD may contain fewer than count players (including none); count is a
    presentation maximum, not evidence that five suitable holds always exist.
    Never fill a short list with unrelated model picks.
    """
    import pandas as pd

    required = {"id", "selected_by_percent", "transfers_in_event", "transfers_out_event"}
    missing = sorted(required - set(players.columns))
    if missing:
        raise RuntimeError(f"Transfer Desk missing official fields: {missing}")
    if not math.isfinite(float(total_managers)) or float(total_managers) <= 0:
        raise RuntimeError("Transfer Desk requires the official total manager count")
    frame = players.copy().rename(columns={"id": "player_id"})
    for col in ("player_id", "selected_by_percent", "transfers_in_event", "transfers_out_event"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
        if frame[col].isna().any() or not frame[col].map(math.isfinite).all() or frame[col].lt(0).any():
            raise RuntimeError(f"Transfer Desk invalid official field: {col}")
    if frame.player_id.le(0).any() or frame.player_id.mod(1).ne(0).any() or frame.player_id.duplicated().any():
        raise RuntimeError("Transfer Desk requires unique positive integer player IDs")
    if frame.selected_by_percent.gt(100).any():
        raise RuntimeError("Transfer Desk ownership exceeds 100 percent")
    frame["player_id"] = frame.player_id.astype(int)
    frame["net_transfers"] = frame.transfers_in_event - frame.transfers_out_event
    eo = pd.to_numeric(frame.player_id.map(effective_ownership or {}), errors="coerce")
    valid_eo = eo.notna() & eo.map(lambda value: math.isfinite(value) and value >= 0)
    frame["ownership_score"] = eo.where(valid_eo, frame.selected_by_percent)
    frame["ownership_source"] = valid_eo.map({True: "TOP_10K_EO", False: "OFFICIAL_OWNERSHIP"})

    # Approximate the owner base at the beginning of the current transfer event.
    owners = float(total_managers) * frame.selected_by_percent / 100
    starting_owners = owners - frame.transfers_in_event + frame.transfers_out_event
    frame["retained_percent_estimate"] = (
        100 * (1 - frame.transfers_out_event / starting_owners.where(starting_owners.gt(0)))
    ).clip(0, 100)

    def recent_average(pid):
        evidence = recent_performance.get(int(pid), {})
        points, games = evidence.get("points"), evidence.get("gameweeks")
        if points is not None and games and int(games) > 0:
            average = float(points) / int(games)
        elif evidence.get("form") is not None:
            # Unused players with form=0 have no evidence of a recent blank.
            row = frame.loc[frame.player_id.eq(pid)].iloc[0]
            minutes = pd.to_numeric(row.get("minutes"), errors="coerce")
            if pd.isna(minutes) or minutes <= 0:
                return float("nan")
            average = float(evidence["form"])
        else:
            return float("nan")
        return average if math.isfinite(average) else float("nan")

    frame["recent_average_points"] = frame.player_id.map(recent_average)
    buy = frame.loc[frame.net_transfers.gt(0)].sort_values(
        ["transfers_in_event", "net_transfers", "player_id"], ascending=[False, False, True]
    ).head(count)
    sell = frame.loc[frame.net_transfers.lt(0)].sort_values(
        ["transfers_out_event", "net_transfers", "player_id"], ascending=[False, True, True]
    ).head(count)
    used = set(buy.player_id) | set(sell.player_id)
    hold_candidates = frame.loc[
        ~frame.player_id.isin(used)
        & frame.ownership_score.ge(HOLD_MIN_OWNERSHIP)
        & frame.recent_average_points.le(HOLD_MAX_RECENT_POINTS)
        & frame.retained_percent_estimate.ge(HOLD_MIN_RETAINED_PERCENT)
    ].copy()
    # Prefer strongly retained blanks, then rank qualified near-misses by ownership.
    hold_candidates["preferred_retention"] = hold_candidates.retained_percent_estimate.ge(
        HOLD_PREFERRED_RETAINED_PERCENT
    )
    hold = hold_candidates.sort_values(
        ["preferred_retention", "ownership_score", "retained_percent_estimate", "player_id"],
        ascending=[False, False, False, True],
    ).head(count)
    groups = {"BUY": buy, "HOLD": hold, "SELL": sell}
    if any(len(groups[key]) != count for key in ("BUY", "SELL")):
        sizes = ", ".join(f"{key}={len(value)}" for key, value in groups.items())
        raise RuntimeError(f"Transfer Desk needs {count} qualifying BUY/SELL players ({sizes}); "
                           "refresh transfer/ownership/recent-points data; no unrelated fallback picks")
    return {key: group.to_dict("records") for key, group in groups.items()}


def _replace(source, old, new):
    if source.count(old) != 1:
        raise RuntimeError(f"Transfer Desk notebook contract changed: {old[:80]!r}")
    return source.replace(old, new, 1)


def patch_transfer_desk_source(source):
    """Replace just desk selection/copy; preserve the existing animation contract."""
    for anchor in ("# Current squad is used for HOLD/SELL",
                   "def _d3_desk_record(action, row, index):",
                   "def _d3_desk_recent_performance(player_id):"):
        if source.count(anchor) != 1:
            raise RuntimeError(f"Transfer Desk notebook contract changed: {anchor!r}")
    start = source.index("# Current squad is used for HOLD/SELL")
    end = source.index("def _d3_desk_record(action, row, index):", start)
    recent_start = source.index("def _d3_desk_recent_performance(player_id):", start)
    recent_end = source.index("\n\ndef _d3_desk_record", recent_start)
    recent = source[recent_start:recent_end]
    replacement = recent + '''

from day3.transfer_desk import select_transfer_desk
DAY3_TRANSFER_DESK_SELECTION_MODE = "EVENT_TRANSFERS_AND_RETAINED_BLANKS_V1"
_d3_desk_selection = select_transfer_desk(
    players,
    total_managers=data["bootstrap"]["total_players"],
    recent_performance={int(pid): _d3_desk_recent_performance(int(pid))
                        for pid in _d3_players_by_id.index},
    effective_ownership=_d3_bench_eo_map,
)
_d3_buy_rows = _d3_desk_selection["BUY"]
_d3_hold_rows = _d3_desk_selection["HOLD"]
_d3_sell_rows = _d3_desk_selection["SELL"]
DAY3_TRANSFER_DESK = {"BUY": [], "HOLD": [], "SELL": []}
DAY3_TRANSFER_DESK_ORDER = []
DAY3_TRANSFER_DESK_SEQUENCE = []

'''
    source = source[:start] + replacement + source[end:]
    source = _replace(source, '''if [len(DAY3_TRANSFER_DESK[x]) for x in ("BUY","HOLD","SELL")] != [5,5,5]:
    raise RuntimeError("Transfer Desk must contain exactly five BUY, five HOLD and five SELL players")
if len({r["player_id"] for g in DAY3_TRANSFER_DESK.values() for r in g}) != 15:''', '''DAY3_TRANSFER_DESK_COUNTS = {action: len(records) for action, records in DAY3_TRANSFER_DESK.items()}
DAY3_TRANSFER_DESK_COUNT = sum(DAY3_TRANSFER_DESK_COUNTS.values())
print(f"Transfer Desk qualified counts: {DAY3_TRANSFER_DESK_COUNTS}; HOLD slots are never padded")
if DAY3_TRANSFER_DESK_COUNTS["BUY"] != 5 or DAY3_TRANSFER_DESK_COUNTS["SELL"] != 5 or not 0 <= DAY3_TRANSFER_DESK_COUNTS["HOLD"] <= 5:
    raise RuntimeError("Transfer Desk requires five BUY/SELL and up to five qualified HOLD players")
if len({r["player_id"] for g in DAY3_TRANSFER_DESK.values() for r in g}) != DAY3_TRANSFER_DESK_COUNT:''')
    source = _replace(source, '''"market_source": "OFFICIAL_FPL_BOOTSTRAP_STATIC",''', '''"market_source": "OFFICIAL_FPL_BOOTSTRAP_STATIC",
    "counts": DAY3_TRANSFER_DESK_COUNTS,
    "hold_shortfall": 5 - DAY3_TRANSFER_DESK_COUNTS["HOLD"],''')
    source = _replace(source, '''or not 0 <= index < 5:''', '''or not 0 <= index < len(DAY3_TRANSFER_DESK[role]):''')
    source = patch_transfer_desk_narration_source(source)
    source = _replace(source, ''''<div class="d3DeskOpen"><strong>FIFTEEN PLAYERS</strong>'\n''', '''f'<div class="d3DeskOpen"><strong>{DAY3_TRANSFER_DESK_COUNT} PLAYERS</strong>'\n''')
    source = _replace(source, '''    _slots = "".join(_d3_desk_slot(r) for r in DAY3_TRANSFER_DESK[_action])''', '''    _slots = "".join(_d3_desk_slot(r) for r in DAY3_TRANSFER_DESK[_action])
    if not DAY3_TRANSFER_DESK[_action]:
        _slots = '<div style="padding:40px;font-size:38px;font-weight:700">NO QUALIFYING HOLDS THIS WEEK</div>' ''')
    for kind in ("popup", "settled"):
        cls = "d3DeskPop" if kind == "popup" else "d3DeskSlot"
        source = _replace(source, f'''if _d3_desk_html.count('class="{cls} ') != 15:
    print("Transfer Desk {kind}-card count is not fifteen")''', f'''if _d3_desk_html.count('class="{cls} ') != DAY3_TRANSFER_DESK_COUNT:
    raise RuntimeError("Transfer Desk {kind}-card count does not match qualified selection")''')
    # Injured and blank-GW players must still appear among heavily sold assets.
    source = _replace(source, '''_d3_desk_model = _d3_desk_model.merge(
    _d3_market,
    on="player_id",
    how="left",''', '''_d3_desk_model = _d3_desk_model.merge(
    _d3_market,
    on="player_id",
    how="outer",''')
    source = _replace(source, '''        "selection_mode": DAY3_TRANSFER_DESK_SELECTION_MODE,
        "recent_performance":''', '''        "selection_mode": DAY3_TRANSFER_DESK_SELECTION_MODE,
        "ownership_score": float(row["ownership_score"]),
        "ownership_source": str(row["ownership_source"]),
        "retained_percent_estimate": _d3_number(row["retained_percent_estimate"]),
        "recent_average_points": _d3_number(row["recent_average_points"]),
        "has_projection": bool(pd.notna(m.get("xpts"))),
        "has_horizon": bool(pd.notna(m.get("next_5_xpts"))),
        "has_minutes": bool(pd.notna(m.get("xmins"))),
        "has_start_probability": bool(pd.notna(m.get("start_probability"))),
        "recent_performance":''')
    source = _replace(source, '''"name": str(m.get("player_name") or p.get("web_name") or f"PLAYER {pid}"),''',
                      '''"name": str(m.get("player_name") if pd.notna(m.get("player_name")) else p.get("web_name") or f"PLAYER {pid}"),''')
    # These are descriptive lists, so the old secure-minutes requirement is
    # irrelevant (and particularly wrong for injury-driven selling).
    secure_start = source.index("_d3_insecure_emergency_fills = [")
    secure_end = source.index("# Persist the exact dynamic selection", secure_start)
    source = source[:secure_start] + source[secure_end:]
    source = _replace(source, '"model_source": "PHASE11_TRANSFER_MATH_WITH_PHASE10_CURRENT_GW_XPTS"',
                      '"model_source": "PHASE10_PROJECTIONS_FOR_DISPLAY_ONLY"')
    rules_start = source.index('    "rules": {', source.index("_d3_desk_manifest = {"))
    rules_end = source.index('    "players": [', rules_start)
    source = source[:rules_start] + '''    "rules": {
        "buy": "Largest event transfers in among positive net-transfer players",
        "sell": "Largest event transfers out among negative net-transfer players",
        "hold": "Highest deadline-safe Top-10K EO (official ownership fallback); recent average <=3 points; ownership >=8%; prefer retention >=95%, accept >=80% for thin pools; exclude BUY/SELL leaders",
    },
''' + source[rules_end:]
    reason_start = source.index("def _d3_desk_short_reason(r):")
    reason_end = source.index("def _d3_desk_narr(action, index):", reason_start)
    source = source[:reason_start] + '''def _d3_desk_short_reason(r):
    if r["action"] == "BUY":
        return f'{r["transfers_in"]:,.0f} TRANSFERS IN'
    if r["action"] == "SELL":
        return f'{r["transfers_out"]:,.0f} TRANSFERS OUT'
    return f'{r["recent_average_points"]:.1f} RECENT AVG PTS • {r["retained_percent_estimate"]:.0f}% KEPT'


''' + source[reason_end:]
    source = _replace(source, '    if role == "BUY":\n        horizon =',
                      '    if not record["has_projection"]:\n        outlook = f"The next fixture for {name} is {fixture}; a fresh points projection is unavailable."\n    if role == "BUY":\n        horizon =')
    source = _replace(source, 'if pd.notna(horizon) and np.isfinite(horizon):',
                      'if record["has_horizon"] and pd.notna(horizon) and np.isfinite(horizon):')
    decision_start = source.index('    elif role == "HOLD":', source.index("def _d3_desk_narr"))
    decision_end = source.index('    return f"{leads[role][index]}', decision_start)
    source = source[:decision_start] + '''    elif role == "HOLD":
        decision = ("Recent returns have been quiet. I would give him another Gameweek before using a transfer.",
                    "The points have disappointed, but I would avoid a rushed move. Check the fixture and latest team news first.",
                    "I would reassess after the next fixture rather than chase last week's points.")[index]
    else:
        decision = ("I would check his availability and the replacement's fixtures before making the switch.",
                    "Consider the alternatives, but make sure the move suits your squad and budget.",
                    "Before selling, check the latest team news and the upside of your replacement.")[index]
''' + source[decision_end:]
    source = _replace(source, '"This is driven by the numbers and projections, with the eye test as the final check. Starting with the buys."',
                      '"Here are the players to consider for your next move. Starting with the buys."')
    source = _replace(source, 'subtitle=f"GW{int(NEXT_GW)} • FIFTEEN PLAYERS • SECURE MINUTES • NEXT FDR"',
                      'subtitle=f"GW{int(NEXT_GW)} • BUY TARGETS • PATIENT HOLDS • SELL CANDIDATES"')
    source = _replace(source, '''["O","OWNERSHIP",f'{r["ownership"]:.1f}%',""],["M","EXPECTED MINUTES",f'{r["xmins"]:.0f}',""],''',
                      '''["O", "TOP 10K EO" if r["action"] == "HOLD" and r["ownership_source"] == "TOP_10K_EO" else "OWNERSHIP",
                      f'{r["ownership_score"] if r["action"] == "HOLD" else r["ownership"]:.1f}%',""],
                     ["M","EXPECTED MINUTES",f'{r["xmins"]:.0f}' if r["has_minutes"] else "—",""],''')
    for key, flag, format_spec in (("xpts", "has_projection", ".1f"), ("next_5_xpts", "has_horizon", ".1f"), ("start_probability", "has_start_probability", ".0f")):
        old = f'''"value":f'{{r["{key}"]:{format_spec}}}''' + ("%'" if key == "start_probability" else "'")
        source = _replace(source, old, old + f''' if r["{flag}"] else "—"''')
    source = _replace(source, '''{"icon":"boot","label":"NET TRANSFERS","value":_d3_desk_momentum_text(r)}''',
                      '''{"icon":"boot","label":"TRANSFERS IN" if r["action"] == "BUY" else "TRANSFERS OUT" if r["action"] == "SELL" else "RECENT AVG PTS",
                          "value":f'{r["transfers_in"]:,.0f}' if r["action"] == "BUY" else f'{r["transfers_out"]:,.0f}' if r["action"] == "SELL" else f'{r["recent_average_points"]:.1f}'}''')
    return source


def patch_transfer_desk_narration_source(source):
    """Speak only available players and explain any missing HOLD candidates."""
    source = _replace(source, '''        if len(records) != 5:
            raise RuntimeError(f"Transfer Desk requires five {action} cards")''', '''        if not records:
            intros[action] += " No players meet the retained-blank criteria this week."
        elif action == "HOLD" and len(records) < 5:
            intros[action] += f" Only {len(records)} players meet the retained-blank criteria this week."''')
    source = _replace(source, '''for index in range(3)])''', '''for index in range(min(3, len(records)))])''')
    source = _replace(source, "DESK_3_SPOKEN_2_SILENT_V1", "DESK_UP_TO_3_SPOKEN_V2")
    # Keep the timing cue independent of the dynamic shortfall explanation.
    source = _replace(source, '''else intros[action].rstrip(".")''', '''else ("Now the holds" if action == "HOLD" else "Now the sells")''')
    return source


def patch_transfer_desk_audio_source(source):
    """Derive spoken/silent card counts from the qualified presentation list."""
    replacements = [
        ("DESK_3_SPOKEN_2_SILENT_V1", "DESK_UP_TO_3_SPOKEN_V2"),
        ('for _desk_index in range(3):', 'for _desk_index in range(min(3, len(DAY3_TRANSFER_DESK[_desk_action]))):'),
        ('if len(_desk_player_narrations) != 9 or len(set(_desk_player_narrations)) != 9:',
         'if len(_desk_player_narrations) != sum(min(3, len(rows)) for rows in DAY3_TRANSFER_DESK.values()) or len(set(_desk_player_narrations)) != len(_desk_player_narrations):'),
        ('Transfer Decision Desk must contain 9 unique spoken player narrations.',
         'Transfer Decision Desk must narrate each selected leading player exactly once.'),
        ('''                if len(players) != 3 or len(section["silent_cards"]) != 2:
                    raise RuntimeError(f"Transfer Desk {action} must contain three narrated and two silent cards")''',
         '''                expected = len(DAY3_TRANSFER_DESK[action])
                if len(players) != min(3, expected) or len(section["silent_cards"]) != max(0, expected - 3):
                    raise RuntimeError(f"Transfer Desk {action} audio cards differ from qualified selection")
                if not players:
                    continue'''),
        ('len(set(starts)) != 3', 'len(set(starts)) != len(players)'),
        ('len(windows) != 15 or len(silent_intervals) != 6',
         'len(windows) != DAY3_TRANSFER_DESK_COUNT or len(silent_intervals) != sum(max(0, len(rows) - 3) for rows in DAY3_TRANSFER_DESK.values())'),
    ]
    for old, new in replacements:
        source = _replace(source, old, new)
    return source


def patch_transfer_desk_animation_source(source):
    """Validate card identity, order and timing against the same selection."""
    source = _replace(source, "DESK_3_SPOKEN_2_SILENT_V1", "DESK_UP_TO_3_SPOKEN_V2")
    source = _replace(source, '''            if len(windows) != 15 or [int(row["i"]) for row in windows] != list(range(15)):
                raise RuntimeError("Transfer Desk timeline must contain fifteen cards in selection order")
            if [bool(row["narrated"]) for row in windows] != [True, True, True, False, False] * 3:''', '''            expected = [record for action in ("BUY", "HOLD", "SELL") for record in DAY3_TRANSFER_DESK[action]]
            if len(windows) != len(expected) or [(int(row["i"]), int(row["player_id"]), row["action"]) for row in windows] != [(int(row["index"]), int(row["player_id"]), row["action"]) for row in expected]:
                raise RuntimeError("Transfer Desk timeline differs from qualified cards in selection order")
            if [bool(row["narrated"]) for row in windows] != [index < 3 for action in ("BUY", "HOLD", "SELL") for index in range(len(DAY3_TRANSFER_DESK[action]))]:''')
    source = _replace(source, '''                if index and abs(float(window["start"]) - float(windows[index-1]["settle"])) > .001:
                    raise RuntimeError("Transfer Desk card timeline has a gap or overlap")''', '''                if index:
                    gap = float(window["start"]) - float(windows[index-1]["settle"])
                    empty_hold_intro = (not DAY3_TRANSFER_DESK["HOLD"] and windows[index-1]["action"] == "BUY" and window["action"] == "SELL")
                    if gap < -.001 or (abs(gap) > .001 and not empty_hold_intro):
                        raise RuntimeError("Transfer Desk card timeline has a gap or overlap")''')
    source = _replace(source, '''if len(vx["transfer_desk_windows"]) != 15:''', '''if len(vx["transfer_desk_windows"]) != DAY3_TRANSFER_DESK_COUNT:''')
    source = _replace(source, '''f"Transfer Desk animation requires 15 card-flight windows; "''', '''f"Transfer Desk animation requires {DAY3_TRANSFER_DESK_COUNT} card-flight windows; "''')
    source = _replace(source, "Transfer Desk requires three narrated cards followed by two silent cards per action",
                      "Transfer Desk spoken/silent card order differs from qualified selection")
    return source
