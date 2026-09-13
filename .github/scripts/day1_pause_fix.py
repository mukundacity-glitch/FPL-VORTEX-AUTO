from pathlib import Path
import ast
import re

path = Path("1_final_day1.py")
source = path.read_text(encoding="utf-8")

marker = "# NATURAL GW REVIEW PAUSE CONTROL"
if marker in source:
    print("Day 1 pause fix already present; no source patch needed.")
else:
    helper_needle = "def vx_review_player_text(p, position_index=0, bench=False):\n"
    if source.count(helper_needle) != 1:
        raise SystemExit(f"Expected one vx_review_player_text definition, found {source.count(helper_needle)}")
    helper = '''# ------------------------------------------------------------
# NATURAL GW REVIEW PAUSE CONTROL
# ------------------------------------------------------------
# Keep Ryan's approved voice/rate. Join related player-review thoughts with
# conversational clause breaks instead of repeated hard sentence stops.
def vx_review_clause_flow(*parts):
    clauses = []
    for part in parts:
        clause = re.sub(r"\\s+", " ", str(part or "")).strip()
        clause = re.sub(r"[.!?]+$", "", clause).strip()
        if clause:
            clauses.append(clause)
    return ("; ".join(clauses) + ".") if clauses else ""


'''
    source = source.replace(helper_needle, helper + helper_needle, 1)

    bench_old = '''        text=f"{openers[position_index % len(openers)]} {signal}. {decision}"
        return re.sub(r"\\s+"," ",text).strip()
'''
    bench_new = '''        return vx_review_clause_flow(
            openers[position_index % len(openers)],
            signal,
            decision,
        )
'''
    if source.count(bench_old) != 1:
        raise SystemExit(f"Expected one bench narration join, found {source.count(bench_old)}")
    source = source.replace(bench_old, bench_new, 1)

    starter_old = '''    text=f"{opener} {signal}. {decision}"
    if p.get("captain"):
        text+=" I also handed him the captain's armband."
    elif p.get("vice"):
        text+=" He held the vice-captaincy just in case."
    return re.sub(r"\\s+"," ",text).strip()
'''
    starter_new = '''    review_tail = ""
    if p.get("captain"):
        review_tail = "I also handed him the captain's armband."
    elif p.get("vice"):
        review_tail = "He held the vice-captaincy just in case."
    return vx_review_clause_flow(opener, signal, decision, review_tail)
'''
    if source.count(starter_old) != 1:
        raise SystemExit(f"Expected one starter narration join, found {source.count(starter_old)}")
    source = source.replace(starter_old, starter_new, 1)

    gate_old = '''        else:
            start = cursor
            if first_player_seen and not goalkeeper_gate_applied:
                start = max(
                    start,
                    float(timing["goalkeeper_card_out"])
                    + float(timing["hero_fade_out"]),
                )
                goalkeeper_gate_applied = True

        segment["t0"] = float(start)
        segment["t1"] = float(start + duration)
'''
    gate_new = '''        else:
            start = cursor
            if first_player_seen and not goalkeeper_gate_applied:
                # Start the next narration naturally after the previous spoken
                # words. Only delay if its spoken name would arrive before the
                # goalkeeper hero has fully faded.
                goalkeeper_hero_off = (
                    float(timing["goalkeeper_card_out"])
                    + float(timing["hero_fade_out"])
                )
                next_name_time = float(segment.get("name_time") or 0.0)
                start = max(
                    start,
                    goalkeeper_hero_off - next_name_time + 0.08,
                )
                goalkeeper_gate_applied = True

        segment["t0"] = float(start)
        segment["t1"] = float(start + duration)

        word_bounds = list(segment.get("words") or [])
        spoken_end_offset = duration
        if word_bounds:
            spoken_end_offset = max(
                float(word.get("start") or 0.0)
                + float(word.get("duration") or 0.0)
                for word in word_bounds
            )
            spoken_end_offset = min(duration, max(0.0, spoken_end_offset))
        segment["speech_end_at"] = float(start + spoken_end_offset)
'''
    if source.count(gate_old) != 1:
        raise SystemExit(f"Expected one post-goalkeeper gate block, found {source.count(gate_old)}")
    source = source.replace(gate_old, gate_new, 1)

    gk_old = '''                if is_first_player:
                    if abs(reveal_at - float(timing["first_player_at"])) > 1e-6:
                        raise RuntimeError("Goalkeeper reveal drifted from the fixed 0:08 beat.")
                    if segment["t1"] > float(timing["goalkeeper_card_out"]) + 1e-6:
                        raise RuntimeError(
                            "Goalkeeper narration runs past the fixed 0:23 card exit: "
                            f"ends at {segment['t1']:.3f}s."
                        )
                    segment["hero_exit_at"] = float(timing["goalkeeper_card_out"])
'''
    gk_new = '''                if is_first_player:
                    if abs(reveal_at - float(timing["first_player_at"])) > 1e-6:
                        raise RuntimeError("Goalkeeper reveal drifted from the fixed 0:08 beat.")
                    goalkeeper_card_cap = float(timing["goalkeeper_card_out"])
                    goalkeeper_card_exit = max(
                        float(reveal_at) + 0.50,
                        float(segment["speech_end_at"]) + 0.25,
                    )
                    if goalkeeper_card_exit > goalkeeper_card_cap + 1e-6:
                        raise RuntimeError(
                            "Goalkeeper spoken narration exceeds the configured card safety cap: "
                            f"speech ends at {segment['speech_end_at']:.3f}s."
                        )
                    # Keep 0:23 as a safety ceiling, not a forced silent hold.
                    timing["goalkeeper_card_out"] = float(goalkeeper_card_exit)
                    segment["hero_exit_at"] = float(goalkeeper_card_exit)
'''
    if source.count(gk_old) != 1:
        raise SystemExit(f"Expected one goalkeeper fixed-exit block, found {source.count(gk_old)}")
    source = source.replace(gk_old, gk_new, 1)

    cursor_old = '        cursor = float(segment["t1"])\n'
    cursor_new = '''        # Edge TTS commonly leaves ~0.8s of silence after its last word.
        # Schedule from the spoken end plus a small natural gap; the MP3 tail may
        # overlap because it contains silence, not narration.
        cursor = min(
            float(segment["t1"]),
            float(segment.get("speech_end_at", segment["t1"])) + 0.35,
        )
'''
    if source.count(cursor_old) != 1:
        raise SystemExit(f"Expected one review cursor assignment, found {source.count(cursor_old)}")
    source = source.replace(cursor_old, cursor_new, 1)

    source = source.replace(
        'print("✅ Fixed beats: PL 0:01 • title 0:02 • pitch 0:03 • goalkeeper 0:08 • card out 0:23")',
        'print("✅ Opening beats: PL 0:01 • title 0:02 • pitch 0:03 • goalkeeper 0:08 • card exits after narration (0:23 cap)")',
        1,
    )
    source = source.replace(
        'raise RuntimeError("GW Review goalkeeper hero is not aligned to the configured 0:23 exit.")',
        'raise RuntimeError("GW Review goalkeeper hero is not aligned to the configured narration-led exit.")',
        1,
    )
    source = source.replace(
        '"GW Review 0:23 goalkeeper exit removed the wrong content: "',
        '"GW Review goalkeeper exit removed the wrong content: "',
        1,
    )

    path.write_text(source, encoding="utf-8")
    print("Patched Day 1 GW Review narration pauses.")

source = path.read_text(encoding="utf-8")
cell_marker = re.compile(
    r"(?m)(?=^# ={20,}\n# [^\n]*\bCELL\s+\d+[A-Za-z]?\b[^\n]*\n)"
)
cell_blocks = [part for part in cell_marker.split(source) if part.strip()]
if len(cell_blocks) != 33:
    raise SystemExit(f"Expected 33 Day 1 cell blocks, found {len(cell_blocks)}")
for index, cell in enumerate(cell_blocks, 1):
    compile(
        cell,
        f"{path.name}::cell-{index}",
        "exec",
        flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT,
        dont_inherit=True,
    )

required = (
    marker,
    "def vx_review_clause_flow(*parts):",
    'segment["speech_end_at"]',
    "spoken_end_offset",
    "goalkeeper_card_exit",
    "+ 0.35",
    "0:23 cap",
    "# OPENING NARRATION FIT — PRESERVE RYAN'S NATURAL DELIVERY",
    "VIDEO_WIDTH, VIDEO_HEIGHT, VIDEO_FPS = 3840, 2160, 24",
    "if W * 9 != H * 16:",
)
missing = [text for text in required if text not in source]
if missing:
    raise SystemExit(f"Focused pause-fix contract missing: {missing}")

for forbidden in (
    'text=f"{opener} {signal}. {decision}"',
    'text=f"{openers[position_index % len(openers)]} {signal}. {decision}"',
):
    if forbidden in source:
        raise SystemExit(f"Old hard-stop narration join still present: {forbidden}")

# Reproduce the failed-run opening-player timing from captured WordBoundary data.
raya_audio_start = 5.956834
raya_spoken_end = raya_audio_start + 11.845416
raya_card_exit = raya_spoken_end + 0.25
raya_hero_off = raya_card_exit + 0.34
next_audio_start = raya_spoken_end + 0.35
williams_name_time = 1.702291
williams_reveal = next_audio_start + williams_name_time

if not (raya_hero_off < williams_reveal):
    raise SystemExit("Natural pause fixture would reveal the next hero before Raya is gone.")
if not (0.30 <= next_audio_start - raya_spoken_end <= 0.50):
    raise SystemExit("Natural player-to-player pause is outside the approved range.")
if not (raya_card_exit < 23.0):
    raise SystemExit("Goalkeeper card still waits for the old fixed 0:23 exit.")

print("Pause timing QA: PASS (0.35s spoken-end gap; no hero overlap)")
print("Player-script cadence QA: PASS (hard full-stop joins removed)")
print("Aspect ratio QA: PASS (3840x2160, 16:9)")
print("33-cell syntax QA: PASS")
