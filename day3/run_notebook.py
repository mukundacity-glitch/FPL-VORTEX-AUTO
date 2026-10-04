from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
import types
from pathlib import Path
from typing import Iterable

# Direct workflow invocation must also resolve shared repository modules inside IPython.
if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from IPython.core.interactiveshell import InteractiveShell
from IPython.core.inputtransformer2 import TransformerManager


PLAN_VALUES = {"NEITHER", "ROLL", "TRANSFER"}
TRANSFER_MODE_VALUES = {"AUTO", "MANUAL"}
NOTEBOOK_CONTROL_CELL_INDEX = 1
VISUAL_ENRICHMENT_CELL_INDEX = 5
PREVIEW_CELL_MARKER = "CELL 14C — ALL SELECTED SLIDES • LIVE HTML PREVIEW IN COLAB"
PITCH_ANIMATION_CELL_INDEX = 30
PITCH_SOURCE_CELL_INDEX = 21
INTRO_OUTRO_SOURCE_CELL_INDEX = 22
SCENE_DESIGN_CELL_INDEX = 24
SCENE_ANIMATION_CELL_INDEX = 33
TTS_PROFILE_CELL_INDEX = 10
PLAYER_CARD_DESIGN_CELL_INDEX = 23
FULL_WORD_NARRATION_CELL_INDEX = 28
SHARED_NARRATION_CELL_INDEX = 32
FINAL_ASSEMBLY_CELL_INDEX = 35


def _replace_exact(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return source.replace(old, new, 1)


def _patch_scene_polish(source: str) -> str:
    # Leave the recap unmanaged so it is fully visible even at seek(0).
    # The right agenda retains its existing narration-synchronised entrances.
    source = _replace_exact(
        source,
        '                {"sel":".leftAgendaHeader","at":left_header_at,"dur":0.55,"stagger":0,"fx":"rise"},\n                *left_events,\n',
        '                # Left recap header and cards persist from frame zero.\n',
        "intro recap at frame zero",
    )
    return _replace_exact(
        source,
        '            close_at = float(cues["closing_at"])',
        '            close_at = max(float(cues["closing_at"]), float(windows[-1]["settle"]))',
        "desk closing visual after final cards",
    )


def _patch_desk_closing_audio(source: str) -> str:
    # Play the unchanged closing speech over the two unspoken SELL popups.
    # BUY/HOLD timing and all player explanations remain unchanged.
    source = _replace_exact(
        source,
        '                if action == "CLOSE":\n                    closing_at = offset\n                    continue',
        '''                if action == "CLOSE":
                    closing_at = offset
                    final_section = plan["sections"][-1]
                    cards = final_section["silent_cards"]
                    seconds = min(float(final_section["silent_card_seconds"]), (end-offset)/len(cards))
                    if not math.isfinite(seconds) or seconds <= 0:
                        raise RuntimeError("Transfer Desk closing-card duration is invalid")
                    for index, player in enumerate(cards):
                        start = offset + index * seconds
                        settle = start + seconds
                        windows.append(dict(player, action="SELL", start=start, settle=settle,
                                            exit_at=settle-min(.34, seconds*.2), narrated=False))
                    continue''',
        "desk closing speech over final sell cards",
    )
    source = _replace_exact(
        source,
        '                seconds = float(section["silent_card_seconds"])',
        '''                if action == "SELL":
                    # CLOSE supplies the soundtrack and measured windows for these cards.
                    continue
                seconds = float(section["silent_card_seconds"])''',
        "remove final sell silence",
    )
    return _replace_exact(
        source,
        'len(silent_intervals) != 6',
        'len(silent_intervals) != 4',
        "desk silence count",
    )


def _patch_transition_title_bounds(source: str) -> str:
    # Pillow's textbbox includes a font-dependent origin offset. Compensate
    # before drawing into the measured mask so the lower glyphs are retained.
    return _replace_exact(
        source,
        '            md.text((stroke*2,yy), line, font=chosen_font, fill=255, stroke_width=stroke, stroke_fill=255)',
        '''            b=draw.textbbox((0,0), line, font=chosen_font, stroke_width=stroke)
            md.text((stroke*2-b[0],yy-b[1]), line, font=chosen_font, fill=255, stroke_width=stroke, stroke_fill=255)''',
        "transition title glyph bounds",
    )


def _replace_once(source: str, pattern: str, replacement: str, label: str) -> str:
    updated, count = re.subn(pattern, replacement, source, count=1, flags=re.MULTILINE)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return updated


def _parse_player_choice(value: str) -> int | None:
    value = str(value or "").strip()
    if not value or value.upper() == "NONE":
        return None
    match = re.match(r"^\s*(\d+)\b", value)
    if not match:
        raise ValueError(f"Invalid player choice: {value!r}")
    player_id = int(match.group(1))
    if player_id <= 0:
        raise ValueError(f"Invalid player id: {player_id}")
    return player_id


def _manual_pairs_from_env() -> tuple[list[int], list[int]]:
    outs: list[int] = []
    ins: list[int] = []
    found_gap = False

    for index in range(1, 6):
        out_id = _parse_player_choice(os.getenv(f"DAY3_OUT_{index}", "NONE"))
        in_id = _parse_player_choice(os.getenv(f"DAY3_IN_{index}", "NONE"))

        if out_id is None and in_id is None:
            found_gap = True
            continue
        if out_id is None or in_id is None:
            raise ValueError(
                f"Manual transfer pair {index} is incomplete. Select both Player OUT {index} "
                f"and Player IN {index}, or leave both as NONE."
            )
        if found_gap:
            raise ValueError(
                "Manual transfer pairs must be filled in order without gaps. "
                "Use pair 1 first, then pair 2, and so on."
            )
        outs.append(out_id)
        ins.append(in_id)

    if not 1 <= len(outs) <= 5:
        raise ValueError("Transfer → Manual requires between 1 and 5 complete OUT/IN pairs.")
    if len(set(outs)) != len(outs):
        raise ValueError("The same Player OUT cannot be selected more than once.")
    if len(set(ins)) != len(ins):
        raise ValueError("The same Player IN cannot be selected more than once.")
    if set(outs) & set(ins):
        raise ValueError("A player cannot appear in both the OUT and IN selections.")
    return outs, ins


def _patch_control_cell(source: str) -> str:
    plan = str(os.environ.get("DAY3_PLAN", "NEITHER")).strip().upper()
    transfer_mode = str(os.environ.get("DAY3_TRANSFER_MODE", "AUTO")).strip().upper()

    if plan not in PLAN_VALUES:
        raise ValueError(f"DAY3_PLAN must be one of {sorted(PLAN_VALUES)}, got {plan!r}")
    if transfer_mode not in TRANSFER_MODE_VALUES:
        raise ValueError(
            f"DAY3_TRANSFER_MODE must be one of {sorted(TRANSFER_MODE_VALUES)}, "
            f"got {transfer_mode!r}"
        )

    # Production workflow is intentionally fixed to FINAL + FULL_VIDEO.
    source = _replace_once(
        source,
        r'^RENDER_MODE\s*=\s*"[^"]+"\s*# @param \["DRAFT", "FINAL"\]\s*$',
        'RENDER_MODE = "FINAL"  # @param ["DRAFT", "FINAL"]',
        "render mode",
    )
    source = _replace_once(
        source,
        r'^OUTPUT_MODE\s*=\s*"[^"]+"\s*# @param \["SLIDES_ONLY", "FULL_VIDEO"\]\s*$',
        'OUTPUT_MODE = "FULL_VIDEO"  # @param ["SLIDES_ONLY", "FULL_VIDEO"]',
        "output mode",
    )

    fixed_true_flags = (
        "INTRO",
        "PROJECTED_GOALS",
        "DEFENSIVE_CONTRIBUTION",
        "FDR_PLAYER_RECOMMENDATIONS",
        "TRANSFER_DECISION",
        "GAMEWEEK_REVIEW",
        "ELITE_OWNERSHIP_TRENDS",
        "BENCHING_DECISION",
        "OUTRO",
    )
    for flag in fixed_true_flags:
        source = _replace_once(
            source,
            rf"^{re.escape(flag)}\s*=\s*(?:True|False)\s*# @param \{{type:\"boolean\"\}}\s*$",
            f'{flag} = True  # @param {{type:"boolean"}}',
            flag,
        )

    roll_selected = plan == "ROLL"
    transfer_selected = plan == "TRANSFER"
    source = _replace_once(
        source,
        r'^GAMEWEEK_PLAN_ROLL\s*=\s*(?:True|False)\s*# @param \{type:"boolean"\}\s*$',
        f'GAMEWEEK_PLAN_ROLL = {roll_selected}  # @param {{type:"boolean"}}',
        "roll scene selector",
    )
    source = _replace_once(
        source,
        r'^TRANSFER_PLAN\s*=\s*(?:True|False)\s*# @param \{type:"boolean"\}\s*$',
        f'TRANSFER_PLAN = {transfer_selected}  # @param {{type:"boolean"}}',
        "transfer scene selector",
    )

    internal_plan = "B • TRANSFER 1–5" if transfer_selected else "A • ROLL"
    source = _replace_once(
        source,
        r'^PLAN\s*=\s*"[^"]+"\s*# @param \["A • ROLL", "B • TRANSFER 1–5"\]\s*$',
        f'PLAN = "{internal_plan}"  # @param ["A • ROLL", "B • TRANSFER 1–5"]',
        "plan",
    )
    source = _replace_once(
        source,
        r'^ACCOUNT\s*=\s*"[^"]+"\s*# @param \["AUTO", "MANUAL"\]\s*$',
        'ACCOUNT = "AUTO"  # @param ["AUTO", "MANUAL"]',
        "account mode",
    )

    # Always bypass the interactive Colab picker. Manual GitHub choices are injected
    # immediately after the picker call, then the notebook's existing transfer
    # validation applies them to the final squad.
    source = _replace_once(
        source,
        r'^PLAYERS\s*=\s*"[^"]+"\s*# @param \["AUTO MODEL", "MANUAL PICKER"\]\s*$',
        'PLAYERS = "AUTO MODEL"  # @param ["AUTO MODEL", "MANUAL PICKER"]',
        "player selection mode",
    )

    if plan == "TRANSFER" and transfer_mode == "MANUAL":
        outs, ins = _manual_pairs_from_env()
        manual_block = f'''
_vx_build_my_plan_player_picker()

# GitHub Actions manual-transfer bridge.
MY_PLAN_SELECTION_MODE = "MANUAL PICKER"
MY_PLAN_TRANSFER_COUNT = "{len(outs)}"
MY_PLAN_TRANSFER_SELECTION = "MANUAL OUT / IN IDS"
MY_PLAN_MANUAL_OUT_PLAYER_IDS = "{",".join(map(str, outs))}"
MY_PLAN_MANUAL_IN_PLAYER_IDS = "{",".join(map(str, ins))}"
VX_MY_PLAN_SELECTION_READY = True
VX_MY_PLAN_PICKER_STATE = {{
    "resolver_version": "GITHUB_ACTIONS_MANUAL_V1",
    "team_id": int(FPL_TEAM_ID),
    "scenario_key": str(MY_PLAN_SCENARIO_KEY),
    "scenario": str(MY_PLAN_SCENARIO),
    "count": {len(outs)},
    "outs": {outs!r},
    "ins": {ins!r},
    "ready": True,
}}
print("✅ GitHub Manual Transfer selection loaded: "
      + " • ".join(
          f"{{index}}. OUT {{out_id}} → IN {{in_id}}"
          for index, (out_id, in_id) in enumerate(zip({outs!r}, {ins!r}), 1)
      ))
'''
        source = source.replace(
            "_vx_build_my_plan_player_picker()\n\n\nMY_PLAN_SCENARIOS",
            manual_block + "\n\nMY_PLAN_SCENARIOS",
            1,
        )
        if "GITHUB_ACTIONS_MANUAL_V1" not in source:
            raise RuntimeError("Could not inject GitHub manual-transfer bridge.")
    elif plan == "TRANSFER":
        # Transfer Auto: notebook chooses the best legal 1–5 transfers.
        pass
    else:
        # Neither and Roll require no transfer picker. Neither omits both personal-plan
        # scenes; Roll keeps only 5A.
        transfer_mode = "AUTO"

    return source


def _patch_pitch_player_names(source: str) -> str:
    old = ".playerName{top:194px!important;height:70px!important;font-size:40px!important;"
    new = old + "font-family:var(--vx-dense)!important;"
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"Pitch player names: expected exactly one match, found {count}")
    # Use the existing condensed name font so long names fit without falling below readable type sizes.
    source = source.replace(old, new, 1)
    print("✅ Compact pitch labels use the shared condensed font for full player names")
    return source


def _patch_pitch_card_entrance(source: str) -> str:
    old = "el.style.transform=`translate(-50%,-50%) scale(${.70+.30*p})`;"
    new = "el.style.transform=`translate(-50%,-50%) scale(${.94+.06*p})`;"
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"Pitch card entrance: expected exactly one match, found {count}")
    # The pitch's 0.90 scale compounds with this entrance; allow margin for fitted long names above the text QA floor.
    source = source.replace(old, new, 1)
    print("✅ Pitch card entrance preserves readable text throughout the animation")
    return source


def _patch_outro_social_icon(source: str) -> str:
    replacements = {
        ".xIcon{font-size:64px;line-height:.82;color:#fff;font-weight:900}": ".xIcon{font-size:64px;line-height:1.2;color:#fff;font-weight:900}",
        ".ctaAction.follow .ctaX{font:900 78px/.8 Arial,sans-serif;": ".ctaAction.follow .ctaX{font:900 78px/1.2 Arial,sans-serif;",
    }
    for old, new in replacements.items():
        count = source.count(old)
        if count != 1:
            raise RuntimeError(f"Outro social icon line height: expected one match, found {count}")
        source = source.replace(old, new, 1)
    # The X glyph extends beyond the earlier .8em line boxes at the CTA's larger sizes.
    print("✅ Outro social icons reserve enough height for their full glyphs")
    return source


def _patch_official_player_portraits(source: str) -> str:
    """Embed verified official portraits so capture does not depend on browser CDN access."""
    helper = r'''
_VX_OFFICIAL_PORTRAIT_URIS = {}

def _vx_official_portrait_uri(url):
    import base64
    from io import BytesIO
    from PIL import Image

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

'''
    replacements = {
        "def resolve_player_photo(fpl_player, prefer_cutout=True):": helper + "def resolve_player_photo(fpl_player, prefer_cutout=True):",
        "        return official\n": "        embedded = _vx_official_portrait_uri(official)\n        if embedded:\n            return embedded\n",
        "        return core_photo\n": "        embedded = _vx_official_portrait_uri(core_photo)\n        if embedded:\n            return embedded\n",
    }
    for old, new in replacements.items():
        count = source.count(old)
        if count != 1:
            raise RuntimeError(f"Official portrait embedding: expected one match, found {count}")
        source = source.replace(old, new, 1)
    print("✅ Official player portraits are verified and embedded before browser capture")
    return source


def _patch_shared_card_scene_checks(source: str) -> str:
    old = "const lanes=[...body.children];"
    new = "const lanes=[...body.children].filter(el=>el.getClientRects().length && el.clientWidth>0 && el.clientHeight>0).sort((left,right)=>left.getBoundingClientRect().top-right.getBoundingClientRect().top);"
    count = source.count(old)
    if count != 2:
        raise RuntimeError(f"Shared card lane checks: expected two matches, found {count}")
    # Hidden footer/legend nodes have no box; CSS order determines the visible row sequence.
    source = source.replace(old, new)
    replacements = {
        "    cards.forEach(card=>{": "    await Promise.all(cards.map(async card=>{",
        "window.VXPlayerCard.mount(card.querySelector('.d3DefHeroHost'),data);": "window.VXPlayerCard.mount(card.querySelector('.d3DefHeroHost'),data);\n      await document.fonts.ready;\n      window.VXPlayerCard.fitText(card.querySelector('.d3DefHeroHost'));",
        "    });\n  })();\n  const assertDefconBoardFit": "    }));\n  })();\n  const assertDefconBoardFit",
        "    document.querySelectorAll('.d3DeskPop').forEach(pop => {": "    await Promise.all([...document.querySelectorAll('.d3DeskPop')].map(async pop => {",
        "window.VXPlayerCard.mount(pop.querySelector('.d3DeskHeroHost'),data);": "window.VXPlayerCard.mount(pop.querySelector('.d3DeskHeroHost'),data);\n      await document.fonts.ready;\n      window.VXPlayerCard.fitText(pop.querySelector('.d3DeskHeroHost'));",
        "    });\n    // A useful static preview": "    }));\n    // A useful static preview",
    }
    for old, new in replacements.items():
        count = source.count(old)
        if count != 1:
            raise RuntimeError(f"Shared card font readiness: expected one match, found {count}")
        source = source.replace(old, new, 1)
    print("✅ Scene card QA checks visible rows in their actual layout order")
    return source


def _patch_defcon_card_motion(source: str) -> str:
    replacements = {
        "?`translateY(${(1-playerEnter)*18}px) scale(${.99+.01*playerEnter})`": "?`translateY(${(1-playerEnter)*18}px)`",
        ':"translateY(18px) scale(.99)";': ':"translateY(18px)";',
    }
    for old, new in replacements.items():
        count = source.count(old)
        if count != 1:
            raise RuntimeError(f"DEFCON card motion: expected one match, found {count}")
        source = source.replace(old, new, 1)
    # These cards register their fitted font size as the minimum; translate without shrinking it.
    print("✅ DEFCON card entrances preserve their fitted text size")
    return source



def _patch_tts_profile(source: str) -> str:
    """Use a slightly quicker, brighter Ryan delivery without changing the voice."""
    old = 'RYAN_BASE_PROFILE = {"rate": "+12%", "pitch": "-5Hz", "volume": "+0%"}'
    new = 'RYAN_BASE_PROFILE = {"rate": "+16%", "pitch": "+4Hz", "volume": "+0%"}'
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"TTS profile: expected exactly one match, found {count}")
    source = source.replace(old, new, 1)
    print("✅ GitHub runner TTS polish: Ryan +16% rate / +4Hz pitch")
    return source


def _patch_narration_tone(source: str) -> str:
    """Keep full-word speech guarantees while making stock transitions less robotic."""
    replacements = (
        (
            '(r"\\bThe practical point is simple\\s*:\\s*", "Here is the practical takeaway: "),',
            '(r"\\bThe practical point is simple\\s*:\\s*", "The takeaway here is: "),',
        ),
        (
            '(r"\\bThe key point is\\s*:\\s*", "The key point is this: "),',
            '(r"\\bThe key point is\\s*:\\s*", "What matters here is: "),',
        ),
        (
            '(r"\\bThat tells us\\b", "That shows us"),',
            '(r"\\bThat tells us\\b", "That means"),',
        ),
        (
            '(r"\\bThe model says\\b", "The model leans toward"),',
            '(r"\\bThe model says\\b", "The numbers point toward"),',
        ),
        (
            '(r"\\bIn conclusion\\b", "To wrap up"),',
            '(r"\\bIn conclusion\\b", "To close this out"),',
        ),
    )
    for old, new in replacements:
        count = source.count(old)
        if count != 1:
            raise RuntimeError(f"Narration tone patch expected one match for {old!r}, found {count}")
        source = source.replace(old, new, 1)
    print("✅ GitHub runner narration polish: creator-style transitions + full-word speech retained")
    return source


def _patch_player_card_sample(source: str) -> str:
    """Restyle the shared card to the supplied blue sample and use Assets/4.png."""
    if "VX_GITHUB_SAMPLE_CARD_V6" in source:
        return source

    # The sample shows five fixtures, not six.
    source = source.replace(
        "const fixtures = Array.isArray(p?.fixtures) ? p.fixtures.slice(0,6) : [];",
        "const fixtures = Array.isArray(p?.fixtures) ? p.fixtures.slice(0,5) : [];",
        1,
    )
    source = source.replace(
        "while (fixtures.length < 6) fixtures.push(null);",
        "while (fixtures.length < 5) fixtures.push(null);",
        1,
    )

    # Match the sample's top badges when the renderer uses its fallback rows.
    source = source.replace('["P", "POSITION",', '["🎯", "POSITION",', 1)
    source = source.replace('["£", "PRICE",', '["🪙", "PRICE",', 1)
    source = source.replace('["O", "OWNERSHIP",', '["👤", "SELECTED BY",', 1)

    marker = 'print("✅ CELL 15A PLAYER CARD DESIGN READY")'
    if source.count(marker) != 1:
        raise RuntimeError("Player-card patch marker was not found exactly once.")

    override = r'''
# VX_GITHUB_SAMPLE_CARD_V6
# Production-only visual override. The Drive notebook remains the data source.
import base64 as _vx_pc_b64
import mimetypes as _vx_pc_mime
import os as _vx_pc_os
import re as _vx_pc_re
from pathlib import Path as _VxPcPath

_vx_pc_bg_path = _VxPcPath(
    _vx_pc_os.environ.get("DAY1_ASSET_DIR", "/content/drive/MyDrive/FPL_VORTEX/Assets")
) / "4.png"
if not _vx_pc_bg_path.is_file():
    raise FileNotFoundError(f"Player-card background 4.png is missing: {_vx_pc_bg_path}")
_vx_pc_bg_mime = _vx_pc_mime.guess_type(_vx_pc_bg_path.name)[0] or "image/png"
_vx_pc_bg_uri = (
    f"data:{_vx_pc_bg_mime};base64,"
    + _vx_pc_b64.b64encode(_vx_pc_bg_path.read_bytes()).decode("ascii")
)

PLAYER_CARD_DESIGN_VERSION = "V6.0_SAMPLE_BLUE_4PNG"

_vx_pc_sample_css = r"""
<style id="vx-github-sample-card-v6">
.vx-player-card.vx-player-card{
  --pc-sample-line:rgba(53,145,255,.88);
  --pc-sample-dark:#03133f;
  --pc-sample-deep:#061d57;
  position:relative!important;
  overflow:hidden!important;
  padding:24px!important;
  border:3px solid #2e8dff!important;
  border-radius:34px!important;
  background:
    linear-gradient(180deg,rgba(2,61,175,.16),rgba(2,19,67,.58)),
    url("__VX_PC_BG__") center/cover no-repeat!important;
  box-shadow:
    0 0 0 4px rgba(46,141,255,.16),
    0 28px 70px rgba(0,0,0,.50),
    inset 0 0 46px rgba(30,133,255,.18)!important;
}
.vx-player-card::after{
  content:"";position:absolute;inset:0;z-index:0;pointer-events:none;
  background:
    radial-gradient(circle at 50% 22%,rgba(45,190,255,.22),transparent 34%),
    linear-gradient(180deg,transparent 42%,rgba(0,9,38,.70));
}
.vx-player-card.vx-player-card .pc-card-grid,.vx-player-card.vx-player-card .pc-card-sheen{display:none!important}
.vx-player-card.vx-player-card .pc-card-body{
  position:relative!important;z-index:2!important;
  display:flex!important;flex-direction:column!important;
  width:100%!important;height:100%!important;min-height:0!important;
  gap:14px!important;
}
.vx-player-card.vx-player-card .pc-photo-info{
  order:1!important;position:relative!important;display:block!important;
  flex:1 1 43%!important;min-height:0!important;overflow:hidden!important;
  border-radius:24px!important;
}
.vx-player-card.vx-player-card .pc-portrait-box{
  position:absolute!important;inset:0!important;width:100%!important;height:100%!important;
  max-height:none!important;overflow:visible!important;border:0!important;border-radius:0!important;
  background:transparent!important;box-shadow:none!important;
}
.vx-player-card.vx-player-card .pc-portrait-box::after{display:none!important}
.vx-player-card.vx-player-card .pc-portrait{
  position:absolute!important;left:50%!important;bottom:-2%!important;
  transform:translateX(-50%)!important;
  width:auto!important;height:92%!important;max-width:74%!important;max-height:96%!important;
  object-fit:contain!important;object-position:center bottom!important;
  filter:drop-shadow(0 20px 28px rgba(0,0,0,.52))!important;
}
.vx-player-card.vx-player-card .pc-portrait-fallback{
  position:absolute!important;left:50%!important;bottom:15%!important;transform:translateX(-50%)!important;
  width:54%!important;height:52%!important;place-items:center!important;
  border:2px solid rgba(74,170,255,.62)!important;border-radius:24px!important;
  background:rgba(4,30,92,.72)!important;color:#fff!important;
}
.vx-player-card.vx-player-card .pc-info-stack{
  position:absolute!important;left:18px!important;top:18px!important;z-index:5!important;
  width:min(39%,430px)!important;display:grid!important;gap:12px!important;
  height:auto!important;grid-template-rows:none!important;grid-auto-rows:auto!important;
}
.vx-player-card.vx-player-card .pc-info-item{
  display:grid!important;grid-template-columns:62px minmax(0,1fr)!important;gap:10px!important;
  align-items:center!important;min-width:0!important;
  padding:12px 15px!important;border-radius:17px!important;
  border:2px solid rgba(46,141,255,.82)!important;
  background:linear-gradient(145deg,rgba(5,31,94,.95),rgba(3,17,61,.96))!important;
  box-shadow:0 8px 22px rgba(0,0,0,.30),inset 0 0 20px rgba(43,139,255,.10)!important;
}
.vx-player-card.vx-player-card .pc-info-item:nth-child(2){order:1!important}
.vx-player-card.vx-player-card .pc-info-item:nth-child(3){order:2!important;border-color:#21df78!important;background:linear-gradient(145deg,rgba(7,105,62,.96),rgba(4,70,47,.96))!important}
.vx-player-card.vx-player-card .pc-info-item:nth-child(1){order:3!important;border-color:#b22cff!important;background:linear-gradient(145deg,rgba(90,12,164,.96),rgba(62,7,117,.96))!important}
.vx-player-card.vx-player-card .pc-info-item:nth-child(n+4){display:none!important}
.vx-player-card.vx-player-card .pc-info-icon{font-size:40px!important;line-height:1!important;text-align:center!important;color:#fff!important}
.vx-player-card.vx-player-card .pc-info-label{font-size:24px!important;line-height:1.2!important;font-weight:900!important;color:#eaf6ff!important;white-space:nowrap!important}
.vx-player-card.vx-player-card .pc-info-value{font-size:48px!important;line-height:1.2!important;font-weight:1000!important;color:#fff!important;white-space:nowrap!important}
.vx-player-card.vx-player-card .pc-identity{
  order:2!important;position:relative!important;min-height:0!important;height:auto!important;
  flex-shrink:0!important;
  padding:12px 165px 12px 18px!important;overflow:visible!important;text-align:center!important;
  border:2px solid var(--pc-sample-line)!important;border-radius:22px!important;
  background:linear-gradient(180deg,var(--pc-sample-deep),var(--pc-sample-dark))!important;
  box-shadow:0 9px 24px rgba(0,0,0,.38)!important;
}
.vx-player-card.vx-player-card .pc-name-first{
  display:block!important;margin:0!important;font-size:34px!important;line-height:1.2!important;
  font-weight:900!important;letter-spacing:1px!important;color:#d9ecff!important;
}
.vx-player-card.vx-player-card .pc-name-last{
  display:block!important;margin:3px 0 0!important;
  font-family:var(--vx-dense,Arial,sans-serif)!important;
  font-size:82px!important;line-height:1.2!important;font-weight:1000!important;
  letter-spacing:.2px!important;color:#fff!important;background:none!important;
  -webkit-text-fill-color:#fff!important;text-shadow:0 4px 12px rgba(0,0,0,.58)!important;
  white-space:nowrap!important;overflow:hidden!important;text-overflow:ellipsis!important;
}
.vx-player-card.vx-player-card .pc-club{display:none!important}
.vx-player-card.vx-player-card .pc-club-crest{
  display:block!important;position:absolute!important;right:20px!important;top:50%!important;transform:translateY(-50%)!important;
  width:128px!important;height:128px!important;object-fit:contain!important;
  filter:drop-shadow(0 8px 14px rgba(0,0,0,.42))!important;
}
.vx-player-card.vx-player-card .pc-stat-rows{
  order:3!important;display:grid!important;grid-template-columns:repeat(3,minmax(0,1fr))!important;
  grid-template-rows:none!important;
  flex-shrink:0!important;
  grid-auto-rows:minmax(92px,1fr)!important;gap:12px!important;min-height:0!important;
}
.vx-player-card.vx-player-card .pc-stat-row{
  min-width:0!important;display:grid!important;
  grid-template-columns:52px minmax(0,1fr)!important;grid-template-rows:auto auto!important;
  column-gap:9px!important;align-content:center!important;
  padding:10px 12px!important;border:2px solid rgba(47,135,255,.80)!important;border-radius:17px!important;
  background:linear-gradient(145deg,rgba(4,29,91,.95),rgba(3,16,59,.96))!important;
  box-shadow:0 7px 18px rgba(0,0,0,.24),inset 0 0 18px rgba(35,135,255,.08)!important;
}
.vx-player-card.vx-player-card .pc-stat-icon{grid-row:1/3!important;align-self:center!important;font-size:38px!important;line-height:1!important}
.vx-player-card.vx-player-card .pc-stat-label{align-self:end!important;font-family:var(--vx-dense,Arial,sans-serif)!important;font-size:20px!important;line-height:1.2!important;font-weight:900!important;color:#dbeaff!important;white-space:nowrap!important;overflow:hidden!important;text-overflow:ellipsis!important}
.vx-player-card.vx-player-card .pc-stat-value{align-self:start!important;font-size:38px!important;line-height:1.2!important;font-weight:1000!important;color:#fff!important;white-space:nowrap!important;overflow:hidden!important;text-overflow:ellipsis!important}
.vx-player-card.vx-player-card .pc-fixture-row{
  order:5!important;position:relative!important;display:grid!important;
  flex-shrink:0!important;
  grid-template-columns:repeat(5,minmax(0,1fr))!important;gap:10px!important;
  padding-top:48px!important;min-height:0!important;
}
.vx-player-card.vx-player-card .pc-fixture-row::before{
  content:"NEXT 5 FIXTURES";position:absolute;left:0;right:0;top:7px;
  color:#fff;font-size:25px;font-weight:1000;letter-spacing:1px;text-align:center;
}
.vx-player-card.vx-player-card .pc-fixture-card{
  min-width:0!important;display:flex!important;flex-direction:column!important;justify-content:center!important;
  padding:9px 7px!important;border:2px solid rgba(255,255,255,.34)!important;border-radius:16px!important;
  box-shadow:0 7px 18px rgba(0,0,0,.24)!important;text-align:center!important;
}
.vx-player-card.vx-player-card .pc-fixture-card.fdr-1{background:linear-gradient(180deg,#27d66a,#0da74f)!important;color:#052614!important}
.vx-player-card.vx-player-card .pc-fixture-card.fdr-2{background:linear-gradient(180deg,#3094ff,#1172de)!important;color:#fff!important}
.vx-player-card.vx-player-card .pc-fixture-card.fdr-3{background:linear-gradient(180deg,#e8edf5,#cbd3de)!important;color:#081226!important}
.vx-player-card.vx-player-card .pc-fixture-card.fdr-5{background:linear-gradient(180deg,#ffd91f,#f2bb00)!important;color:#171100!important}
.vx-player-card.vx-player-card .pc-fixture-card.fdr-none{background:linear-gradient(180deg,#354a70,#243655)!important;color:#fff!important}
.vx-player-card.vx-player-card .pc-fixture-main{display:grid!important;grid-template-columns:1fr!important;justify-items:center!important;gap:2px!important;flex-shrink:0!important}
.vx-player-card.vx-player-card .pc-fixture-crest{width:40px!important;height:40px!important;object-fit:contain!important}
.vx-player-card.vx-player-card .pc-fixture-team{font-size:23px!important;line-height:1.2!important;font-weight:1000!important;white-space:nowrap!important;overflow:hidden!important;text-overflow:ellipsis!important;max-width:100%!important}
.vx-player-card.vx-player-card .pc-fixture-venue{font-size:18px!important;line-height:1.2!important;font-weight:900!important}
.vx-player-card.vx-player-card .pc-fixture-meta{display:grid!important;gap:2px!important;flex-shrink:0!important}
.vx-player-card.vx-player-card .pc-fixture-gw{font-size:18px!important;line-height:1.2!important;font-weight:900!important}
.vx-player-card.vx-player-card .pc-fixture-fdr{font-size:28px!important;line-height:1.2!important;font-weight:1000!important}
.vx-player-card.vx-player-card .pc-legend,.vx-player-card.vx-player-card .pc-card-footer{display:none!important}
@container (max-height:1100px){
  .vx-player-card.vx-player-card .pc-info-stack{gap:8px!important}
  .vx-player-card.vx-player-card .pc-info-item{padding:8px 10px!important}
  .vx-player-card.vx-player-card .pc-info-label{font-size:19px!important}
  .vx-player-card.vx-player-card .pc-info-value{font-size:38px!important}
  .vx-player-card.vx-player-card .pc-name-first{font-size:27px!important}
  .vx-player-card.vx-player-card .pc-name-last{font-size:64px!important}
  .vx-player-card.vx-player-card .pc-club-crest{width:104px!important;height:104px!important}
  .vx-player-card.vx-player-card .pc-stat-rows{grid-auto-rows:minmax(72px,1fr)!important}
  .vx-player-card.vx-player-card .pc-stat-label{font-size:16px!important}
  .vx-player-card.vx-player-card .pc-stat-value{font-size:30px!important}
  .vx-player-card.vx-player-card .pc-fixture-row{padding-top:38px!important}
  .vx-player-card.vx-player-card .pc-fixture-row::before{font-size:20px!important}
  .vx-player-card.vx-player-card .pc-fixture-team{font-size:18px!important}
  .vx-player-card.vx-player-card .pc-fixture-fdr{font-size:22px!important}
}
</style>
""".replace("__VX_PC_BG__", _vx_pc_bg_uri)
# Scene-specific rules describe the earlier card layout. Keep the shared sample
# layout authoritative even when those styles are appended after this stylesheet.
PLAYER_CARD_CSS += _vx_pc_sample_css.replace(
    ".vx-player-card.vx-player-card",
    ".vx-player-card.vx-player-card.vx-player-card.vx-player-card",
)

_vx_pc_sample_fit = r"""  function fitOne(el){
    if (!el) return;
    const requestedMax = Number(el.dataset.pcMax || 40);
    const requestedMin = Number(el.dataset.pcMin || 16);
    if (!Number.isFinite(requestedMax) || !Number.isFinite(requestedMin)) return;
    el.style.removeProperty("font-size");
    const cssMax = parseFloat(getComputedStyle(el).fontSize) || requestedMax;
    const max = Math.max(8, Math.min(requestedMax, cssMax));
    const min = Math.min(max, Math.max(12, Math.min(requestedMin, max * .68)));
    let lo = min;
    let hi = max;
    let best = lo;
    // Match the sample's CSS cap while letting fitted text override its !important rules.
    el.style.setProperty("font-size", `${hi}px`, "important");
    if (elementFits(el)) return;
    for (let i=0; i<12; i++){
      const mid = (lo + hi) / 2;
      el.style.setProperty("font-size", `${mid}px`, "important");
      if (elementFits(el)){
        best = mid;
        lo = mid;
      } else {
        hi = mid;
      }
    }
    el.style.setProperty("font-size", `${best}px`, "important");
  }"""
_vx_pc_sample_element_fit = r"""  function elementFits(el){
    if (!el || el.clientWidth <= 0 || el.clientHeight <= 0) return true;
    // DEFCON and Transfer Desk require text to stay within one pixel of its box.
    return el.scrollWidth <= el.clientWidth + 1
      && el.scrollHeight <= el.clientHeight + 1;
  }"""
PLAYER_CARD_JS, _vx_pc_element_fit_count = _vx_pc_re.subn(
    r"  function elementFits\(el\)\{.*?\n  \}",
    lambda match: _vx_pc_sample_element_fit,
    PLAYER_CARD_JS,
    count=1,
    flags=_vx_pc_re.DOTALL,
)
if _vx_pc_element_fit_count != 1:
    raise RuntimeError("The shared player-card fit predicate was not found exactly once.")
PLAYER_CARD_JS, _vx_pc_fit_count = _vx_pc_re.subn(
    r"  function fitOne\(el\)\{.*?\n  \}",
    lambda match: _vx_pc_sample_fit,
    PLAYER_CARD_JS,
    count=1,
    flags=_vx_pc_re.DOTALL,
)
if _vx_pc_fit_count != 1:
    raise RuntimeError("The shared player-card text fitter was not found exactly once.")

# One shared responsive layout and live badge registry for every hero host.
from day3.player_cards import readable_player_cards as _vx_readable_cards
PLAYER_CARD_CSS, PLAYER_CARD_JS = _vx_readable_cards(
    PLAYER_CARD_CSS, PLAYER_CARD_JS, team_meta_by_id,
)
PLAYER_CARD_DESIGN_VERSION = "V7.0_READABLE_SHARED_CARDS"

print("✅ Player card switched to supplied blue sample format")
print("✅ Player card background: Assets/4.png")
print("✅ Bottom card footer removed; five-fixture strip retained")
'''
    return source.replace(marker, override + "\n" + marker, 1)



def _install_colab_compatibility() -> None:
    google_pkg = sys.modules.get("google")
    if google_pkg is None:
        google_pkg = types.ModuleType("google")
        google_pkg.__path__ = []
        sys.modules["google"] = google_pkg

    colab_pkg = types.ModuleType("google.colab")
    drive_mod = types.ModuleType("google.colab.drive")
    output_mod = types.ModuleType("google.colab.output")
    userdata_mod = types.ModuleType("google.colab.userdata")

    def mount(path: str, **_: object) -> None:
        Path(path).mkdir(parents=True, exist_ok=True)
        (Path(path) / "MyDrive").mkdir(parents=True, exist_ok=True)
        print(f"✅ GitHub runner Drive compatibility active at {path}")

    def enable_custom_widget_manager() -> None:
        return None

    def serve_kernel_port_as_iframe(*_: object, **__: object) -> None:
        print("ℹ️ Colab live preview iframe skipped on GitHub Actions.")

    def get_secret(name: str) -> str:
        value = str(os.environ.get(name, "") or "").strip()
        if not value:
            raise KeyError(name)
        return value

    drive_mod.mount = mount  # type: ignore[attr-defined]
    output_mod.enable_custom_widget_manager = enable_custom_widget_manager  # type: ignore[attr-defined]
    output_mod.serve_kernel_port_as_iframe = serve_kernel_port_as_iframe  # type: ignore[attr-defined]
    userdata_mod.get = get_secret  # type: ignore[attr-defined]

    colab_pkg.drive = drive_mod  # type: ignore[attr-defined]
    colab_pkg.output = output_mod  # type: ignore[attr-defined]
    colab_pkg.userdata = userdata_mod  # type: ignore[attr-defined]
    google_pkg.colab = colab_pkg  # type: ignore[attr-defined]

    sys.modules["google.colab"] = colab_pkg
    sys.modules["google.colab.drive"] = drive_mod
    sys.modules["google.colab.output"] = output_mod
    sys.modules["google.colab.userdata"] = userdata_mod


def _iter_code_cells(notebook: dict[str, object]) -> Iterable[tuple[int, str]]:
    cells = notebook.get("cells")
    if not isinstance(cells, list):
        raise ValueError("Notebook JSON does not contain a cells list.")
    for index, cell in enumerate(cells):
        if not isinstance(cell, dict) or cell.get("cell_type") != "code":
            continue
        source = cell.get("source", "")
        if isinstance(source, list):
            source_text = "".join(str(part) for part in source)
        else:
            source_text = str(source)
        if source_text.strip():
            yield index, source_text


def _prepare_code_cells(notebook_path: Path) -> list[tuple[int, str]]:
    notebook = json.loads(notebook_path.read_text(encoding="utf-8"))
    cells = list(_iter_code_cells(notebook))
    if len(cells) < 30:
        raise RuntimeError(f"Unexpected Day 3 notebook shape: only {len(cells)} code cells.")

    prepared_cells: list[tuple[int, str]] = []
    transformer = TransformerManager()
    for cell_index, source in cells:
        if cell_index == NOTEBOOK_CONTROL_CELL_INDEX:
            source = _patch_control_cell(source)
        if cell_index == VISUAL_ENRICHMENT_CELL_INDEX:
            source = _patch_official_player_portraits(source)
        if cell_index == TTS_PROFILE_CELL_INDEX:
            source = _patch_tts_profile(source)
        if cell_index == PLAYER_CARD_DESIGN_CELL_INDEX:
            source = _patch_player_card_sample(source)
        if cell_index == FULL_WORD_NARRATION_CELL_INDEX:
            source = _patch_narration_tone(source)
        if cell_index == PITCH_SOURCE_CELL_INDEX:
            source = _patch_pitch_player_names(source)
        if cell_index == INTRO_OUTRO_SOURCE_CELL_INDEX:
            source = _patch_outro_social_icon(source)
        if cell_index == PITCH_ANIMATION_CELL_INDEX:
            source = _patch_pitch_card_entrance(source)
        if cell_index == SCENE_DESIGN_CELL_INDEX:
            source = _patch_shared_card_scene_checks(source)
        if cell_index == SCENE_ANIMATION_CELL_INDEX:
            source = _patch_defcon_card_motion(source)
            source = _patch_scene_polish(source)
        if cell_index == SHARED_NARRATION_CELL_INDEX:
            source = _patch_desk_closing_audio(source)
        if cell_index == FINAL_ASSEMBLY_CELL_INDEX:
            source = _patch_transition_title_bounds(source)
        if PREVIEW_CELL_MARKER in source:
            print(f"⏭️ Skipping Colab-only live preview cell {cell_index}.")
            continue

        compile(
            transformer.transform_cell(source),
            f"{notebook_path}:cell-{cell_index}",
            "exec",
            flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT,
        )
        prepared_cells.append((cell_index, source))
    return prepared_cells


def run_notebook(notebook_path: Path) -> None:
    cells = _prepare_code_cells(notebook_path)
    _install_colab_compatibility()
    shell = InteractiveShell.instance()
    shell.autoawait = True
    shell.user_ns["__name__"] = "__main__"
    shell.user_ns["__file__"] = str(notebook_path)

    for cell_index, source in cells:
        print(f"\n{'=' * 76}\n▶ DAY 3 NOTEBOOK CELL {cell_index}\n{'=' * 76}", flush=True)
        result = shell.run_cell(source, store_history=False, silent=False)
        error = result.error_before_exec or result.error_in_exec
        if error is not None:
            raise RuntimeError(f"Day 3 notebook cell {cell_index} failed") from error

    print("✅ Final Day 3 notebook execution completed.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the final Day 3 Colab notebook in GitHub Actions.")
    parser.add_argument("notebook", type=Path)
    parser.add_argument("--validate-only", action="store_true", help="Validate every patched cell without executing the notebook.")
    args = parser.parse_args()

    notebook_path = args.notebook.resolve()
    if not notebook_path.is_file():
        raise FileNotFoundError(notebook_path)
    if args.validate_only:
        cells = _prepare_code_cells(notebook_path)
        print(f"✅ Day 3 preflight passed: {len(cells)} patched code cells compile")
    else:
        run_notebook(notebook_path)


if __name__ == "__main__":
    main()
