from __future__ import annotations

import argparse
import ast
import collections
import hashlib
import json
import re
import os
import subprocess
import sys
import types
from pathlib import Path

from IPython.core.interactiveshell import InteractiveShell

from .common import read_json


DAY2_OUTPUT_ROOT = "/content/drive/MyDrive/FPL_VORTEX/Day_2"
CONFIG_PATH = Path(__file__).with_name("config.json")
QUALITY_PATTERN = re.compile(
    r'(?m)^MP4_QUALITY\s*=\s*["\'](?:DRAFT|FINAL)["\'](?P<suffix>[^\n]*)$'
)


def _cell_source(cell: dict) -> str:
    source = cell.get("source", [])
    return "".join(source) if isinstance(source, list) else str(source)


def _set_cell_source(cell: dict, source: str) -> None:
    cell["source"] = source.splitlines(keepends=True)


DAY2_EO_EXPOSURE_TRAP_V2 = "DAY2_EO_EXPOSURE_TRAP_V2"
_EO_TRAP_TERMS = ("MODEL", "EO", "OWNERSHIP", "EXPOSURE", "TRAP")
_EO_TRAP_PROTECTED = (
    "FPL_VORTEX_DAY2_FINAL.MP4",
    "FFMPEG",
    "PLAYWRIGHT",
    "INTERACTIVESHELL",
    "WRITE_FFCONCAT",
)


def _eo_trap_add_contract(source: str):
    tree = ast.parse(source, filename="DAY2_FINAL.ipynb::eo-exposure-target")
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (
            func.id if isinstance(func, ast.Name)
            else func.attr if isinstance(func, ast.Attribute)
            else ""
        )
        if name != "add" or len(node.args) < 4:
            continue
        if not (
            isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            continue
        calls.append(node)
    if not calls:
        return None
    call = max(
        calls,
        key=lambda node: (
            getattr(node, "end_lineno", node.lineno),
            getattr(node, "end_col_offset", 0),
        ),
    )
    payload_keys = []
    if isinstance(call.args[3], ast.Dict):
        for key in call.args[3].keys:
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                payload_keys.append(key.value)
    return {
        "section_id": call.args[0].value,
        "template": call.args[1].value,
        "payload_keys": payload_keys,
        "start_line": call.lineno,
        "end_line": getattr(call, "end_lineno", call.lineno),
    }


def _make_eo_exposure_notebook_block(indent: str, section_id: str, fallback_template: str):
    block = r'''
# __VX_EO_TRAP_V2_BEGIN__
import html as _vx_html_lib
import inspect as _vx_inspect
import math as _vx_math

def _vx_num(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value) if _vx_math.isfinite(float(value)) else None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        cleaned = text.replace("%", "")
        return float(cleaned)
    except (TypeError, ValueError):
        return None

def _vx_pick(d, *names):
    if not isinstance(d, dict):
        return None
    lowered = {str(k).strip().lower(): v for k, v in d.items()}
    for name in names:
        key = str(name).strip().lower()
        if key in lowered and lowered[key] not in ("", None):
            return lowered[key]
    return None

def _vx_nested_pick(d, *names):
    if not isinstance(d, dict):
        return None
    direct = _vx_pick(d, *names)
    if direct not in ("", None):
        return direct
    for key in ("fixture", "next_fixture", "next", "opponent", "team", "profile", "stats"):
        child = d.get(key)
        if isinstance(child, dict):
            direct = _vx_pick(child, *names)
            if direct not in ("", None):
                return direct
    return None

def _vx_name(row):
    value = _vx_pick(row, "name", "player_name", "web_name", "full_name", "player")
    return str(value).strip() if value not in (None, "") else ""

def _vx_photo(row):
    value = _vx_pick(row, "photo", "photo_url", "image", "image_url", "headshot", "portrait", "player_image")
    return str(value).strip() if value not in (None, "") else ""

def _vx_own(row):
    return _vx_num(_vx_nested_pick(
        row,
        "ownership", "selected_by_percent", "selected_by", "own_pct",
        "ownership_pct", "ownership_percent", "sel_by_percent"
    ))

def _vx_eo(row):
    return _vx_num(_vx_nested_pick(
        row,
        "effective_ownership", "effective_ownership_pct", "effective_ownership_percent",
        "eo", "effective_own", "eo_pct"
    ))

def _vx_minutes(row):
    return _vx_num(_vx_nested_pick(
        row,
        "expected_minutes", "predicted_minutes", "xmins", "expected_mins",
        "minutes_expected", "mins", "minutes"
    ))

def _vx_fdr(row):
    return _vx_num(_vx_nested_pick(
        row,
        "fixture_rating", "fdr", "next_fdr", "fixture_difficulty", "difficulty"
    ))

def _vx_projection(row):
    return _vx_num(_vx_nested_pick(
        row,
        "projection", "projected_points", "projected_pts", "proj", "predicted_points",
        "expected_points", "xpts", "points_projection"
    ))

def _vx_xgi(row):
    return _vx_num(_vx_nested_pick(
        row,
        "xgi", "xgi_trend", "xGI", "expected_goal_involvement",
        "expected_goal_involvement_trend", "expected_goal_involvement_per90"
    ))

def _vx_transfers(row):
    return _vx_num(_vx_nested_pick(
        row,
        "transfers_in", "transfer_in", "transfers", "rising", "net_transfers",
        "transfer_delta", "ownership_change"
    ))

def _vx_role(row):
    value = _vx_nested_pick(
        row,
        "role", "position_role", "attacking_role", "set_pieces",
        "penalties", "corners", "role_description", "position"
    )
    return str(value).strip() if value not in (None, "") else ""

def _vx_warning(row):
    value = _vx_nested_pick(
        row,
        "role_concern", "minutes_risk", "warning", "status", "flag",
        "rotation_risk", "risk", "concern"
    )
    return str(value).strip() if value not in (None, "") else ""

def _vx_fixture(row):
    fixture = _vx_pick(row, "fixture", "next_fixture")
    if isinstance(fixture, dict):
        opp = _vx_pick(fixture, "opponent", "opp", "team", "name", "short_name")
        fdr = _vx_num(_vx_pick(fixture, "fdr", "difficulty", "rating"))
        if opp or fdr is not None:
            return (str(opp or "").strip(), fdr)
    if isinstance(fixture, (list, tuple)) and fixture:
        first = fixture[0]
        if isinstance(first, dict):
            opp = _vx_pick(first, "opponent", "opp", "team", "name", "short_name")
            fdr = _vx_num(_vx_pick(first, "fdr", "difficulty", "rating"))
            if opp or fdr is not None:
                return (str(opp or "").strip(), fdr)
    opp = _vx_pick(row, "opponent", "opp", "next_opponent", "next_team")
    fdr = _vx_fdr(row)
    return (str(opp or "").strip(), fdr)

def _vx_percentile(values, value, higher=True):
    nums = [x for x in values if x is not None]
    if value is None or not nums:
        return 0.5
    if len(nums) == 1:
        return 1.0
    if higher:
        below = sum(1 for x in nums if x < value)
    else:
        below = sum(1 for x in nums if x > value)
    return below / max(1, len(nums) - 1)

def _vx_case_score(item, all_items):
    minutes = _vx_percentile([x["minutes"] for x in all_items], item["minutes"], True)
    fdr = _vx_percentile([x["fdr"] for x in all_items], item["fdr"], False) if item["fdr"] is not None else 0.5
    proj_vals = [x["projection"] for x in all_items if x["projection"] is not None]
    xgi_vals = [x["xgi"] for x in all_items if x["xgi"] is not None]
    proj = _vx_percentile(proj_vals, item["projection"], True) if item["projection"] is not None and proj_vals else 0.5
    xgi = _vx_percentile(xgi_vals, item["xgi"], True) if item["xgi"] is not None and xgi_vals else 0.5
    output = (proj + xgi) / 2.0
    role_text = (item["role"] + " " + item["warning"]).lower()
    role_security = 0.75 if item["warning"] == "" else 0.25
    if any(token in role_text for token in ("secure", "starter", "nailed", "penalty", "set piece", "striker", "advanced")):
        role_security = max(role_security, 0.9)
    if any(token in role_text for token in ("rotation", "doubt", "bench", "suspended", "injur", "uncertain")):
        role_security = min(role_security, 0.2)
    return (
        0.28 * minutes
        + 0.16 * fdr
        + 0.24 * output
        + 0.12 * role_security
        + 0.20 * proj
    )

def _vx_prepare_players():
    candidate_lists = []
    preferred_names = ("player", "players", "fpl", "data", "model", "eo", "ownership")
    for var_name, value in list(globals().items()):
        dicts = []
        if isinstance(value, (list, tuple)) and len(value) >= 3:
            dicts = [x for x in value if isinstance(x, dict)]
        elif isinstance(value, dict) and len(value) >= 3:
            dict_values = list(value.values())
            if dict_values and all(isinstance(x, dict) for x in dict_values[: min(20, len(dict_values))]):
                dicts = dict_values
        elif hasattr(value, "to_dict") and callable(getattr(value, "to_dict", None)):
            try:
                candidate = value.to_dict("records")
                if isinstance(candidate, list):
                    dicts = [x for x in candidate if isinstance(x, dict)]
            except Exception:
                dicts = []
        if len(dicts) < 3:
            continue
        score = 0
        lname = str(var_name).lower()
        score += 3 if any(token in lname for token in preferred_names) else 0
        sample = dicts[: min(12, len(dicts))]
        keys = set()
        for row in sample:
            keys.update(str(k).lower() for k in row.keys())
        score += 3 if any(k in keys for k in ("name", "web_name", "player", "player_name")) else 0
        score += 2 if any(k in keys for k in ("eo", "effective_ownership", "ownership", "selected_by_percent")) else 0
        score += 2 if any(k in keys for k in ("expected_minutes", "predicted_minutes", "xmins", "minutes")) else 0
        score += 1 if any(k in keys for k in ("projection", "projected_points", "proj", "xgi", "xgi_trend")) else 0
        candidate_lists.append((score, len(dicts), var_name, dicts))
    if not candidate_lists:
        return []
    _, _, _, rows = max(candidate_lists, key=lambda x: (x[0], x[1], str(x[2])))
    prepared = []
    for row in rows:
        name = _vx_name(row)
        if not name:
            continue
        item = {
            "row": row,
            "name": name,
            "photo": _vx_photo(row),
            "own": _vx_own(row),
            "eo": _vx_eo(row),
            "minutes": _vx_minutes(row),
            "fdr": _vx_fdr(row),
            "projection": _vx_projection(row),
            "xgi": _vx_xgi(row),
            "transfers": _vx_transfers(row),
            "role": _vx_role(row),
            "warning": _vx_warning(row),
        }
        metric_count = sum(
            value is not None
            for value in (
                item["own"], item["eo"], item["minutes"],
                item["fdr"], item["projection"], item["xgi"]
            )
        )
        if metric_count >= 2:
            prepared.append(item)
    return prepared

def _vx_select(players):
    if len(players) < 3:
        raise RuntimeError("EO & Exposure Trap requires at least three player rows from the existing FPL data source")

    for item in players:
        item["case"] = _vx_case_score(item, players)

    eo_values = [x["eo"] for x in players if x["eo"] is not None]
    own_values = [x["own"] for x in players if x["own"] is not None]
    mins_values = [x["minutes"] for x in players if x["minutes"] is not None]
    fdr_values = [x["fdr"] for x in players if x["fdr"] is not None]

    transfers_values = [x["transfers"] for x in players if x["transfers"] is not None]

    def pressure(item):
        eo = _vx_percentile(eo_values, item["eo"], True) if eo_values and item["eo"] is not None else 0.5
        own = _vx_percentile(own_values, item["own"], True) if own_values and item["own"] is not None else 0.5
        transfers = _vx_percentile(transfers_values, item["transfers"], True) if transfers_values and item["transfers"] is not None else 0.5
        return max(eo, own, transfers)

    def minutes(item):
        return _vx_percentile(mins_values, item["minutes"], True) if mins_values and item["minutes"] is not None else 0.5

    def fixture(item):
        return _vx_percentile(fdr_values, item["fdr"], False) if fdr_values and item["fdr"] is not None else 0.5

    def risk(item):
        m = 1.0 - minutes(item)
        f = 1.0 - fixture(item)
        warning = item["warning"].lower()
        role_risk = 1.0 if any(t in warning for t in ("rotation", "doubt", "bench", "uncertain", "suspended", "injur")) else 0.0
        low_output = 1.0 - item["case"]
        return 0.35 * m + 0.20 * f + 0.20 * role_risk + 0.25 * low_output

    used = set()

    protect_pool = sorted(
        players,
        key=lambda x: (
            0.40 * pressure(x)
            + 0.60 * x["case"]
            + 0.05 * fixture(x)
        ),
        reverse=True,
    )
    protect = next(
        (x for x in protect_pool if pressure(x) >= 0.55 and x["case"] >= 0.55),
        protect_pool[0],
    )
    used.add(protect["name"])

    attack_pool = sorted(
        [x for x in players if x["name"] not in used],
        key=lambda x: (
            0.28 * (1.0 - pressure(x))
            + 0.57 * x["case"]
            + 0.15 * fixture(x)
        ),
        reverse=True,
    )
    attack = next(
        (x for x in attack_pool if pressure(x) <= 0.65 and x["case"] >= 0.55),
        attack_pool[0] if attack_pool else protect_pool[1],
    )
    used.add(attack["name"])

    avoid_pool = sorted(
        [x for x in players if x["name"] not in used],
        key=lambda x: (
            0.34 * pressure(x)
            + 0.36 * risk(x)
            + 0.30 * (1.0 - x["case"])
        ),
        reverse=True,
    )
    avoid = next(
        (x for x in avoid_pool if pressure(x) >= 0.45 and risk(x) >= 0.45),
        avoid_pool[0] if avoid_pool else protect_pool[-1],
    )
    return protect, attack, avoid

def _vx_fmt_pct(value):
    if value is None:
        return "—"
    return f"{value:.1f}%"

def _vx_fmt_num(value):
    if value is None:
        return "—"
    return f"{value:.2f}".rstrip("0").rstrip(".")

def _vx_fixture_label(item):
    opp, fdr = _vx_fixture(item["row"])
    if opp and fdr is not None:
        return f"{opp} · FDR {_vx_fmt_num(fdr)}"
    if opp:
        return opp
    if fdr is not None:
        return f"FDR {_vx_fmt_num(fdr)}"
    return "Fixture data"

def _vx_role_label(item, fallback):
    role = item["role"] or ""
    if role:
        return role
    if item["warning"]:
        return item["warning"]
    return fallback

def _vx_metric_lines(item, verdict):
    fixture = _vx_fixture_label(item)
    role = _vx_role_label(item, "Role data")
    if verdict == "Protect":
        lines = [
            ("EO", _vx_fmt_pct(item["eo"])),
            ("Expected minutes", _vx_fmt_num(item["minutes"])),
            ("Fixture", fixture),
            ("Role", role),
            ("Projection", _vx_fmt_num(item["projection"])),
        ]
    elif verdict == "Attack":
        own = item["own"] if item["own"] is not None else item["eo"]
        xgi = item["xgi"] if item["xgi"] is not None else item["projection"]
        lines = [
            ("Ownership", _vx_fmt_pct(own)),
            ("Expected minutes", _vx_fmt_num(item["minutes"])),
            ("Fixture", fixture),
            ("Role", role),
            ("xGI trend", _vx_fmt_num(xgi)),
        ]
    else:
        lines = [
            ("EO", _vx_fmt_pct(item["eo"])),
            ("Expected minutes", _vx_fmt_num(item["minutes"])),
            ("Fixture", fixture),
            ("Role concern", item["warning"] or role),
            ("Projection", _vx_fmt_num(item["projection"])),
        ]
    return lines

def _vx_card_copy(verdict, item):
    if verdict == "Protect":
        return "Own because the case is strong"
    if verdict == "Attack":
        return "Attack with evidence, not hope"
    return "Do not chase the crowd"

def _vx_card_html(verdict, label, item, index):
    portrait = item["photo"]
    safe_name = _vx_html_lib.escape(item["name"])
    if portrait:
        safe_photo = _vx_html_lib.escape(portrait, quote=True)
        visual = f'<img src="{safe_photo}" alt="{safe_name}" style="width:100%;height:100%;object-fit:cover;display:block;border-radius:18px 18px 0 0;">'
    else:
        visual = f'<div style="width:100%;height:100%;display:flex;align-items:center;justify-content:center;font-size:56px;font-weight:800;letter-spacing:-2px;">{_vx_html_lib.escape(item["name"][:2].upper())}</div>'

    accents = {
        "Protect": ("#2f7d5a", "#14392d"),
        "Attack": ("#3c83a8", "#173042"),
        "Avoid": ("#b56d39", "#402515"),
    }
    accent, accent_soft = accents[verdict]
    metric_html = "".join(
        f'<div style="display:flex;justify-content:space-between;align-items:center;gap:12px;padding:7px 0;border-bottom:1px solid rgba(255,255,255,.10);font-size:17px;line-height:1.18;">'
        f'<span style="opacity:.70;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{_vx_html_lib.escape(str(k))}</span>'
        f'<strong style="font-size:18px;font-weight:750;text-align:right;white-space:nowrap;">{_vx_html_lib.escape(str(v))}</strong></div>'
        for k, v in _vx_metric_lines(item, verdict)
    )
    fixture = _vx_fixture_label(item)
    support = (
        f"{'High effective ownership' if verdict == 'Protect' else 'Lower ownership' if verdict == 'Attack' else 'Popular ownership or rising activity'} · "
        f"{'strong minutes and case' if verdict == 'Protect' else 'real upside case' if verdict == 'Attack' else 'evidence weaker than the pressure'}"
    )
    delay = "5.2s" if index == 0 else "5.7s" if index == 1 else "6.2s"
    transform = "translateX(-24px)" if index == 0 else "translateY(24px)" if index == 1 else "translateX(24px)"
    eo_value = item["eo"] if item["eo"] is not None else item["own"]
    try:
        eo_width = max(0, min(100, float(eo_value or 0)))
    except (TypeError, ValueError):
        eo_width = 0
    return f"""
<div style="flex:1 1 0;min-width:0;height:100%;display:flex;flex-direction:column;box-sizing:border-box;border:1px solid {accent};border-radius:20px;background:linear-gradient(180deg,{accent_soft} 0%,rgba(10,16,22,.92) 28%,rgba(8,12,18,.97) 100%);overflow:hidden;opacity:0;transform:{transform};animation:vxCardIn .55s cubic-bezier(.22,.61,.36,1) {delay} both;">
  <div style="height:148px;flex:0 0 148px;position:relative;overflow:hidden;background:rgba(255,255,255,.03);">
    {visual}
    <div style="position:absolute;top:12px;left:12px;background:{accent};padding:6px 10px;border-radius:999px;font-size:13px;font-weight:800;letter-spacing:.8px;">{label}</div>
  </div>
  <div style="padding:15px 17px 16px;display:flex;flex-direction:column;gap:10px;min-height:0;flex:1;box-sizing:border-box;">
    <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;">
      <div style="font-size:26px;line-height:1;font-weight:900;letter-spacing:-.7px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{safe_name}</div>
      <div style="font-size:12px;text-transform:uppercase;letter-spacing:1px;opacity:.58;white-space:nowrap;">{_vx_html_lib.escape(verdict)}</div>
    </div>
    <div style="height:7px;border-radius:99px;background:rgba(255,255,255,.10);overflow:hidden;">
      <div style="height:100%;width:0;background:{accent};border-radius:99px;--vx-w:{eo_width:.1f}%;animation:vxGrow .70s cubic-bezier(.22,.61,.36,1) 8.05s both;"></div>
    </div>
    <div style="min-height:0;overflow:hidden;">{metric_html}</div>
    <div style="font-size:14px;line-height:1.25;opacity:.72;min-height:35px;">{_vx_html_lib.escape(support)}</div>
    <div style="margin-top:auto;padding:9px 11px;border-radius:12px;background:rgba(255,255,255,.055);font-size:15px;font-weight:800;line-height:1.15;text-align:center;letter-spacing:.1px;opacity:0;transform:translateY(6px);animation:vxVerdict .4s ease 11.1s both;">{_vx_html_lib.escape(_vx_card_copy(verdict,item))}</div>
  </div>
</div>"""

def _vx_build_html(gw, protect, attack, avoid, inherited_style=""):
    cards = (
        _vx_card_html("Protect", "EO SHIELD", protect, 0)
        + _vx_card_html("Attack", "CALCULATED DIFFERENTIAL", attack, 1)
        + _vx_card_html("Avoid", "EXPOSURE TRAP", avoid, 2)
    )
    subtitle = "Where rank is protected, where upside is created, and where the crowd is wrong."
    takeaway = "Ownership tells you the risk. Minutes, role, fixture, and projection tell you the decision."
    lock = "Protect the floor. Attack the ceiling. Avoid the noise."
    css = f"""
@keyframes vxFadeUp {{ from {{ opacity:0; transform:translateY(12px); }} to {{ opacity:1; transform:translateY(0); }} }}
@keyframes vxCardIn {{ from {{ opacity:0; }} to {{ opacity:1; }} }}
@keyframes vxGrow {{ from {{ width:0; }} to {{ width:var(--vx-w); }} }}
@keyframes vxVerdict {{ from {{ opacity:0; transform:translateY(6px) scale(.98); }} to {{ opacity:1; transform:translateY(0) scale(1); }} }}
@keyframes vxMetrics {{ from {{ opacity:.15; transform:translateY(7px); }} to {{ opacity:1; transform:translateY(0); }} }}
"""
    return f"""
<div style="width:100%;height:100%;box-sizing:border-box;display:flex;flex-direction:column;gap:11px;padding:4px 4px 6px;overflow:hidden;font-family:inherit;color:inherit;">
  <div style="animation:vxFadeUp .45s ease 0s both;font-size:45px;line-height:.98;font-weight:950;letter-spacing:-1.8px;white-space:nowrap;">EO &amp; Exposure Trap</div>
  <div style="animation:vxFadeUp .45s ease .75s both;font-size:21px;line-height:1.18;font-weight:650;opacity:.83;max-width:95%;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{_vx_html_lib.escape(subtitle)}</div>
  <div style="animation:vxFadeUp .45s ease 2.3s both;display:flex;align-items:center;gap:14px;height:34px;flex:0 0 34px;">
    <div style="font-size:13px;letter-spacing:1.1px;font-weight:800;opacity:.6;white-space:nowrap;">OWNERSHIP PRESSURE</div>
    <div style="height:8px;flex:1;border-radius:99px;background:rgba(255,255,255,.10);overflow:hidden;">
      <div style="width:0;height:100%;border-radius:99px;background:rgba(255,255,255,.72);animation:vxGrow .9s cubic-bezier(.22,.61,.36,1) 3.1s both;--vx-w:86%;"></div>
    </div>
    <div style="font-size:16px;font-weight:900;white-space:nowrap;">EO → DECISION</div>
  </div>
  <div style="display:flex;gap:13px;flex:1;min-height:0;overflow:hidden;">{cards}</div>
  <div style="display:flex;gap:12px;align-items:stretch;min-height:78px;flex:0 0 78px;">
    <div style="flex:1 1 0;min-width:0;display:flex;align-items:center;padding:12px 15px;border-radius:16px;background:rgba(255,255,255,.055);border:1px solid rgba(255,255,255,.10);font-size:18px;line-height:1.16;font-weight:760;opacity:0;animation:vxFadeUp .45s ease 12.9s both;">{_vx_html_lib.escape(takeaway)}</div>
    <div style="flex:0 0 31%;min-width:0;display:flex;align-items:center;justify-content:center;padding:10px 14px;border-radius:16px;background:rgba(255,255,255,.08);font-size:20px;line-height:1.08;font-weight:900;text-align:center;letter-spacing:-.2px;opacity:0;animation:vxFadeUp .45s ease 13.35s both;">{_vx_html_lib.escape(lock)}</div>
  </div>
</div>
<style>{css}{inherited_style}</style>
"""

def _vx_template_registry():
    candidates = []
    for name, value in list(globals().items()):
        if not isinstance(value, dict) or len(value) < 2:
            continue
        lname = str(name).lower()
        key_text = " ".join(str(k) for k in value.keys()).lower()
        value_text = " ".join(str(v)[:300] for v in value.values() if isinstance(v, str)).lower()
        score = 0
        score += 4 if "template" in lname else 0
        score += 3 if "ownership" in key_text or "pressure" in key_text else 0
        score += 2 if "ownership pressure" in value_text else 0
        if score:
            candidates.append((score, value))
    return max(candidates, key=lambda x: x[0])[1] if candidates else None

def _vx_ownership_template(registry):
    if not isinstance(registry, dict):
        return None, None
    ranked = []
    for key, value in registry.items():
        text_key = str(key).lower()
        text_value = str(value)[:4000].lower() if isinstance(value, str) else ""
        score = 0
        score += 4 if "ownership" in text_key else 0
        score += 4 if "pressure" in text_key else 0
        score += 5 if "ownership pressure" in text_value else 0
        if score:
            ranked.append((score, key, value))
    if not ranked:
        return None, None
    _, key, value = max(ranked, key=lambda x: x[0])
    return key, value

def _vx_register_template(html_body, fallback_template):
    registry = _vx_template_registry()
    key = "__EO_EXPOSURE_TRAP_V2__"
    if registry is None:
        return fallback_template
    _, base = _vx_ownership_template(registry)
    if isinstance(base, str):
        placeholder = None
        for token in ("{content}", "{body}", "{html}", "{main}", "{inner}", "{{content}}", "{{body}}", "{{html}}", "{{main}}", "{{inner}}"):
            if token in base:
                placeholder = token
                break
        if placeholder:
            replacement = "{html}" if not placeholder.startswith("{{") else "{{html}}"
            registry[key] = base.replace(placeholder, replacement, 1)
        else:
            styles = ""
            style_blocks = __import__("re").findall(r"<style[^>]*>.*?</style>", base, flags=__import__("re").S | __import__("re").I)
            if style_blocks:
                styles = "".join(style_blocks)
            registry[key] = f"<div style=\"width:100%;height:100%;overflow:hidden;\">{{html}}</div>{styles}"
    elif callable(base):
        def _vx_dynamic_template(*args, **kwargs):
            return html_body
        registry[key] = _vx_dynamic_template
    else:
        registry[key] = "<div style=\"width:100%;height:100%;overflow:hidden;\">{html}</div>"
    return key

_vx_players = _vx_prepare_players()
_vx_protect, _vx_attack, _vx_avoid = _vx_select(_vx_players)
_vx_gw = 0
for _vx_gw_name in ("GW0", "GW", "NEXT_GW", "TARGET_GW", "CURRENT_GW", "GW_NUM"):
    try:
        _vx_candidate = int(globals().get(_vx_gw_name) or 0)
        if _vx_candidate > 0:
            _vx_gw = _vx_candidate
            break
    except (TypeError, ValueError):
        pass

_vx_html = _vx_build_html(_vx_gw, _vx_protect, _vx_attack, _vx_avoid)
_vx_template_key = _vx_register_template(_vx_html, __FALLBACK_TEMPLATE__)

b = SB()
if hasattr(b, "pause") and callable(getattr(b, "pause")):
    b.pause(14.0)
elif hasattr(b, "wait") and callable(getattr(b, "wait")):
    b.wait(14.0)

b.s("Now we come to the final EO and exposure check.")
b.s("This is where ownership becomes a decision, not just a number.")
b.s("The question is simple: is the rank pressure justified by the player’s actual case?")
b.s("We are looking at three outcomes: Protect, Attack, and Avoid.")
b.s(f"First is the EO Shield: {_vx_protect['name']}.")
b.s("Their effective ownership creates real rank pressure, but the ownership is justified. Expected minutes are strong, the role is secure, the fixture offers a clear route to points, and the projection supports both the price and the popularity.")
b.s("This is a player I would rather own than hope fails.")
b.s(f"Next is the Calculated Differential: {_vx_attack['name']}.")
b.s("The ownership is lower, but the opportunity is real. The minutes are there, the role creates attacking potential, the fixture gives them upside, and the underlying numbers support a genuine route to returns.")
b.s("This is not a random punt. It is a controlled way to gain rank without turning the whole squad into a gamble.")
b.s(f"Finally, the Exposure Trap: {_vx_avoid['name']}.")
b.s("The ownership or transfer activity may make this player difficult to ignore, but the case underneath is weaker. Minutes may be uncertain, the role may have changed, the fixture may be difficult, or the recent returns may not be sustainable.")
b.s("That does not mean the player cannot return. It means the evidence does not support bringing them in simply because the crowd is moving.")
b.s("So the final rule is simple.")
b.s("Protect yourself from justified ownership.")
b.s("Attack through one strong, evidence-backed differential.")
b.s("Avoid exposure when ownership is running ahead of the player’s actual case.")
b.s("Ownership tells you the risk.")
b.s("Minutes, role, fixture, and projection tell you the decision.")
b.s("Protect the floor. Attack the ceiling. Avoid the noise.")

_final_payload = {
    "title": "EO & Exposure Trap",
    "subtitle": "Where rank is protected, where upside is created, and where the crowd is wrong.",
    "html": _vx_html,
    "content": _vx_html,
    "body": _vx_html,
    "summary": _vx_html,
    "name": "EO & Exposure Trap",
    "gw": _vx_gw,
    "players": [_vx_protect["name"], _vx_attack["name"], _vx_avoid["name"]],
}
add(__SECTION_ID__, _vx_template_key, "EO & Exposure Trap", _final_payload, b)
# __VX_EO_TRAP_V2_END__
'''.strip("\n")
    block = block.replace("__SECTION_ID__", repr(section_id)).replace("__FALLBACK_TEMPLATE__", repr(fallback_template)).replace("\n", "\n" + indent)
    return indent + block

def _replace_last_data_slide(payload: dict) -> bool:
    cells = payload.get("cells") or []
    if not cells:
        raise RuntimeError("Day 2 notebook has no cells")

    for cell in cells:
        if DAY2_EO_EXPOSURE_TRAP_V2 in _cell_source(cell):
            print("[DAY 2] EO & Exposure Trap replacement: already applied")
            return False

    candidates = []
    for index, cell in enumerate(cells):
        if cell.get("cell_type") != "code" or index <= 13:
            continue
        source = _cell_source(cell)
        upper = source.upper()
        if any(marker in upper for marker in _EO_TRAP_PROTECTED):
            continue
        hits = sum(upper.count(term) for term in _EO_TRAP_TERMS)
        # Do not depend on literal "SCENE"/"SLIDE" labels. The notebook's
        # existing final block is identified by its data semantics plus the
        # stable SB() -> add() presentation contract.
        if hits < 2:
            continue
        contract = _eo_trap_add_contract(source)
        if not contract:
            continue
        try:
            tree = ast.parse(source, filename=f"DAY2_FINAL.ipynb::cell-{index}")
        except SyntaxError:
            continue
        sb_nodes = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = getattr(node, "value", None)
            if not isinstance(value, ast.Call):
                continue
            func = value.func
            func_name = (
                func.id if isinstance(func, ast.Name)
                else func.attr if isinstance(func, ast.Attribute)
                else ""
            )
            if func_name == "SB":
                sb_nodes.append(node)
        if not sb_nodes:
            continue
        sb_node = max(sb_nodes, key=lambda n: (getattr(n, "end_lineno", n.lineno), getattr(n, "end_col_offset", 0)))
        if getattr(sb_node, "lineno", 0) >= contract["start_line"]:
            continue
        score = hits
        candidates.append((index, score, contract, sb_node))

    if not candidates:
        raise RuntimeError(
            "Could not safely locate the existing final MODEL/EO/EXPOSURE/TRAP slide block"
        )

    index, _, contract, sb_node = max(candidates, key=lambda x: (x[0], x[1]))
    source = _cell_source(cells[index])
    lines = source.splitlines()

    start_line = sb_node.lineno
    end_line = contract["end_line"]
    indent_line = lines[start_line - 1]
    indent = indent_line[: len(indent_line) - len(indent_line.lstrip())]

    new_block = _make_eo_exposure_notebook_block(indent, contract["section_id"], contract["template"])
    new_source = "\n".join(
        lines[:start_line - 1] + [new_block] + lines[end_line:]
    ) + "\n"
    compile(new_source, f"DAY2_FINAL.ipynb::cell-{index}", "exec")
    _set_cell_source(cells[index], new_source)
    print(
        "[DAY 2] EO & Exposure Trap replacement: PASS "
        f"(cell={index}, section={contract['section_id']!r}, template={contract['template']!r})"
    )
    print("[DAY 2] Previous final exposure presentation block fully replaced")
    return True



def _repair_python311_fstrings(payload: dict) -> bool:
    """Repair every legacy Scene 5 nested f-string unsupported by Python 3.11."""
    cells = payload.get("cells") or []
    if len(cells) <= 13 or cells[13].get("cell_type") != "code":
        raise RuntimeError("Day 2 scene cell 13 is missing")

    source = _cell_source(cells[13])
    if (
        "_badge_html =" in source
        and "_hero_html =" in source
        and "_summary_visual_html =" in source
        and "_summary_badge_html =" in source
    ):
        return False

    lines = source.splitlines()

    try:
        featured_anchor = next(
            index
            for index, line in enumerate(lines)
            if line.strip()
            == '_warning = _s5e(str(player.get("warning") or "").upper())'
        )
    except StopIteration as exc:
        raise RuntimeError("Scene 5 featured-card repair anchor is missing") from exc

    featured_helpers = [
        '    _badge_html = f\'<img src="{_badge}" alt="{_club}">\' if _badge else _club',
        '    _hero_html = (',
        '        \'<img class="vxPlayerVisual {}" src="{}" alt="{}" \'',
        '        \'onerror="if(this.dataset.fallback!==\\\'1\\\'){{this.dataset.fallback=\\\'1\\\';\'',
        '        \'this.src=\\\'{}\\\';this.classList.add(\\\'jerseyFallback\\\')}}\'',
        '        \'else{{this.style.display=\\\'none\\\'}}">\'',
        '    ).format(_img_kind, _img, _name, _img_fallback) if _img else (',
        '        f\'<div class="heroInitials">{_name[:2]}</div>\'',
        '    )',
        '    _variance_html = (',
        '        \'<div class="varianceTag">HIGHER VARIANCE</div>\' if rank == 3 else ""',
        '    )',
    ]
    lines[featured_anchor + 1:featured_anchor + 1] = featured_helpers

    try:
        summary_start = next(
            index
            for index, line in enumerate(lines)
            if line.startswith("def _s5_summary_row")
        )
        summary_anchor = next(
            index
            for index in range(summary_start, len(lines))
            if lines[index].strip()
            == '_purpose = _s5e(str(player.get("purpose") or "DIFFERENTIAL").upper())'
        )
    except StopIteration as exc:
        raise RuntimeError("Scene 5 summary-card repair anchor is missing") from exc

    summary_helpers = [
        '    _summary_visual_html = (',
        '        \'<img class="photo vxPlayerVisual {}" src="{}" alt="{}" \'',
        '        \'onerror="if(this.dataset.fallback!==\\\'1\\\'){{this.dataset.fallback=\\\'1\\\';\'',
        '        \'this.src=\\\'{}\\\';this.classList.add(\\\'jerseyFallback\\\')}}\'',
        '        \'else{{this.style.display=\\\'none\\\'}}">\'',
        '    ).format(_img_kind, _img, _name, _img_fallback) if _img else (',
        '        f\'<div class="initials">{_name[:2]}</div>\'',
        '    )',
        '    _summary_badge_html = f\'<img src="{_badge}" alt="{_club}">\' if _badge else _club',
    ]
    lines[summary_anchor + 1:summary_anchor + 1] = summary_helpers

    replaced = {
        "featured_badge": False,
        "featured_visual": False,
        "variance": False,
        "summary_visual": False,
        "summary_badge": False,
    }
    for index, line in enumerate(lines):
        if '<div class="teamBadge">{f\'<img src=' in line:
            lines[index] = '      <div class="teamBadge">{_badge_html}</div>'
            replaced["featured_badge"] = True
        elif '<div class="heroVisual">{f\'<img class=' in line:
            lines[index] = '      <div class="heroVisual">{_hero_html}</div>'
            replaced["featured_visual"] = True
        elif '{f\'<div class="varianceTag">HIGHER VARIANCE</div>\'' in line:
            lines[index] = '      {_variance_html}'
            replaced["variance"] = True
        elif '<div class="visual">{f\'<img class="photo' in line:
            lines[index] = '      <div class="visual">{_summary_visual_html}</div>'
            replaced["summary_visual"] = True
        elif '<div class="summaryBadge">{f\'<img src=' in line:
            lines[index] = '      <div class="summaryBadge">{_summary_badge_html}</div>'
            replaced["summary_badge"] = True

    missing = [name for name, ok in replaced.items() if not ok]
    if missing:
        raise RuntimeError(
            "Scene 5 Python 3.11 repair did not replace: " + ", ".join(missing)
        )

    repaired_source = "\n".join(lines) + "\n"
    compile(repaired_source, "DAY2_FINAL.ipynb::cell-13", "exec")
    _set_cell_source(cells[13], repaired_source)
    print("[DAY 2] Repaired all five Python 3.11-safe Scene 5 HTML expressions")
    return True

def _sync_repaired_notebook(payload: dict, notebook_path: Path) -> None:
    """Persist only the source repair back to the same Drive notebook."""
    if not os.environ.get("RCLONE_CONFIG"):
        print("[DAY 2] Drive sync skipped outside GitHub Actions")
        return

    cfg = read_json(CONFIG_PATH, "Day 2 config")
    drive_path = str(cfg["notebook"]["drive_path"])
    notebook_path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    subprocess.run(
        [
            "rclone",
            "copyto",
            str(notebook_path),
            f"vortex-drive:{drive_path}",
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    print(f"[DAY 2] Repaired source synced to My Drive/{drive_path}")


def _audit_notebook(payload: dict) -> None:
    cells = payload.get("cells") or []
    code_cells = [
        (index, _cell_source(cell))
        for index, cell in enumerate(cells)
        if cell.get("cell_type") == "code"
    ]
    if len(code_cells) != 21:
        raise RuntimeError(f"Expected 21 Day 2 code cells, found {len(code_cells)}")

    definition_locations: dict[str, list[int]] = collections.defaultdict(list)
    normalized_hashes: dict[str, list[int]] = collections.defaultdict(list)
    for index, source in code_cells:
        compile(source, f"DAY2_FINAL.ipynb::cell-{index}", "exec")
        tree = ast.parse(source, filename=f"DAY2_FINAL.ipynb::cell-{index}")
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                definition_locations[node.name].append(index)

        normalized = "\n".join(
            line.strip()
            for line in source.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
        if len(normalized) >= 160:
            normalized_hashes[
                hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            ].append(index)

    duplicate_defs = {
        name: indexes
        for name, indexes in definition_locations.items()
        if len(indexes) > 1
    }
    if duplicate_defs:
        raise RuntimeError(f"Duplicate top-level definitions found: {duplicate_defs}")

    duplicate_cells = [
        indexes for indexes in normalized_hashes.values() if len(indexes) > 1
    ]
    if duplicate_cells:
        raise RuntimeError(f"Duplicate whole-code cells found: {duplicate_cells}")

    all_source = "\n".join(source for _, source in code_cells)
    if DAY2_OUTPUT_ROOT not in all_source:
        raise RuntimeError("Day 2 output root contract is missing from the notebook")
    if "FPL_VORTEX_DAY2_FINAL.mp4" not in all_source:
        raise RuntimeError("Day 2 final MP4 contract is missing from the notebook")


def _patch_quality(payload: dict, quality: str, notebook_path: Path) -> None:
    quality = quality.upper()
    if quality not in {"DRAFT", "FINAL"}:
        raise ValueError(f"Unsupported render quality: {quality}")

    cells = payload.get("cells") or []
    if not cells or cells[0].get("cell_type") != "code":
        raise RuntimeError("Day 2 Cell 0 is missing")

    source = _cell_source(cells[0])
    replacement = f'MP4_QUALITY = "{quality}"\\g<suffix>'
    patched, count = QUALITY_PATTERN.subn(replacement, source, count=1)
    if count != 1:
        raise RuntimeError(
            "Could not apply the isolated GitHub Actions MP4_QUALITY override"
        )
    notebook_pattern = re.compile(
        r"(?m)^NOTEBOOK_JSON_PATH\s*=\s*['\"][^'\"]+['\"](?P<suffix>[^\n]*)$"
    )
    notebook_replacement = (
        f"NOTEBOOK_JSON_PATH = {str(notebook_path)!r}\\g<suffix>"
    )
    patched, notebook_count = notebook_pattern.subn(
        notebook_replacement, patched, count=1
    )
    if notebook_count != 1:
        raise RuntimeError(
            "Could not point the Day 2 self-audit at the ephemeral GitHub Actions notebook"
        )
    _set_cell_source(cells[0], patched)


def _install_colab_compat() -> None:
    """Provide only the Colab APIs used by the saved notebook."""
    try:
        import google  # type: ignore
    except ImportError:
        google = types.ModuleType("google")
        google.__path__ = []  # type: ignore[attr-defined]
        sys.modules["google"] = google

    colab = types.ModuleType("google.colab")
    drive = types.ModuleType("google.colab.drive")
    output = types.ModuleType("google.colab.output")
    userdata = types.ModuleType("google.colab.userdata")

    def mount(mountpoint, **kwargs):
        (Path(mountpoint) / "MyDrive").mkdir(parents=True, exist_ok=True)
        return None

    drive.mount = mount
    drive.flush_and_unmount = lambda: None
    output.serve_kernel_port_as_iframe = lambda *a, **k: None
    output.serve_kernel_port_as_window = lambda *a, **k: None
    userdata.get = lambda name: os.environ.get(str(name))
    colab.drive, colab.output, colab.userdata = drive, output, userdata
    sys.modules.update({
        "google.colab": colab,
        "google.colab.drive": drive,
        "google.colab.output": output,
        "google.colab.userdata": userdata,
    })


def execute_notebook(notebook_path: Path, quality: str) -> None:
    notebook_path = notebook_path.resolve()
    if not notebook_path.is_file():
        raise FileNotFoundError(f"Day 2 notebook is missing: {notebook_path}")

    payload = json.loads(notebook_path.read_text(encoding="utf-8"))
    repaired = _repair_python311_fstrings(payload)
    replaced = _replace_last_data_slide(payload)
    _audit_notebook(payload)
    if repaired or replaced:
        _sync_repaired_notebook(payload, notebook_path)
    _patch_quality(payload, quality, notebook_path)

    # The execution copy is ephemeral. Only the Python 3.11 source repair above
    # is synced back to Drive; workflow-only quality/path overrides stay local.
    notebook_path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    _audit_notebook(payload)

    code_cells = [
        (index, _cell_source(cell))
        for index, cell in enumerate(payload.get("cells") or [])
        if cell.get("cell_type") == "code"
    ]

    _install_colab_compat()
    shell = InteractiveShell.instance()
    shell.autoawait = True
    shell.user_ns["__name__"] = "__main__"

    print("[DAY 2] Notebook preflight: PASS")
    print(f"[DAY 2] Notebook: {notebook_path}")
    print(f"[DAY 2] Render quality override: {quality.upper()}")
    print(f"[DAY 2] Isolated output root: {DAY2_OUTPUT_ROOT}")

    for position, (cell_index, source) in enumerate(code_cells, start=1):
        label = next(
            (
                line.removeprefix("# ").strip()
                for line in source.splitlines()
                if "CELL " in line.upper()
            ),
            f"code cell {cell_index}",
        )
        print(
            f"::group::Day 2 cell {position:02d}/{len(code_cells)} — {label}",
            flush=True,
        )
        try:
            result = shell.run_cell(source, store_history=False, silent=False)
            if not result.success:
                raise RuntimeError(f"Day 2 execution failed in notebook cell {cell_index}")
        finally:
            print("::endgroup::", flush=True)

    print("[DAY 2] Notebook execution: PASS")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Execute the isolated Day 2 Colab notebook in GitHub Actions"
    )
    parser.add_argument("--notebook", type=Path, required=True)
    parser.add_argument("--quality", choices=("DRAFT", "FINAL"), default="FINAL")
    args = parser.parse_args()
    execute_notebook(args.notebook, args.quality)


if __name__ == "__main__":
    main()
