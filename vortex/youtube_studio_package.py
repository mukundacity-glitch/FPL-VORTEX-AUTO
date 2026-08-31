"""Build the manual YouTube Studio package for FPL VORTEX Video 1/3.

This module never contacts YouTube. It creates a native 4K thumbnail and a
JSON file containing the dynamic title, description, hashtags, tags, chapters,
and a short manual-upload checklist after the final media pass has completed.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import io
import json
import os
import re
import subprocess

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont


DEFAULT_OUTPUT_ROOT = Path("/content/drive/MyDrive/FPL_VORTEX/FIRST VIDEO")
YT_VIDEO_PART = 1
YT_VIDEO_TOTAL = 3
YT_THUMBNAIL_SIZE = (3840, 2160)
YT_THUMBNAIL_MAX_BYTES = 1_950_000


def _vx23_required_file(path, label):
    path = Path(path)
    if not path.is_file() or path.stat().st_size <= 0:
        raise FileNotFoundError(f"Required {label} is missing or empty: {path}")
    return path


def _vx23_single_file(folder, pattern, label):
    matches = sorted(path for path in Path(folder).glob(pattern) if path.is_file())
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected exactly one {label} matching {pattern!r} in {folder}; "
            f"found {len(matches)}"
        )
    return _vx23_required_file(matches[0], label)


def _vx23_probe_video(path):
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,codec_name,avg_frame_rate",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    streams = payload.get("streams") or []
    if len(streams) != 1:
        raise RuntimeError(f"Expected one video stream in {path}; found {len(streams)}")
    stream = streams[0]
    rate = str(stream.get("avg_frame_rate") or "0/1")
    try:
        numerator, denominator = rate.split("/", 1)
        fps = float(numerator) / float(denominator)
    except (TypeError, ValueError, ZeroDivisionError):
        fps = 0.0
    return {
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
        "codec": str(stream.get("codec_name") or ""),
        "fps": fps,
        "duration": float(payload.get("format", {}).get("duration") or 0.0),
    }


def _vx23_read_json(path, label):
    path = _vx23_required_file(path, label)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Expected {label} to be a JSON object: {path}")
    return payload


def _vx23_atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _vx23_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _vx23_font_path(condensed=False):
    candidates = (
        [
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf"),
            Path("/usr/share/fonts/opentype/urw-base35/NimbusSansNarrow-Bold.otf"),
            Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"),
        ]
        if condensed
        else [
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
            Path("/usr/share/fonts/opentype/urw-base35/NimbusSans-Bold.otf"),
            Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"),
        ]
    )
    chosen = next((path for path in candidates if path.is_file()), None)
    if chosen is None:
        raise FileNotFoundError("A supported bold system font is required")
    return chosen


def _vx23_font(size, condensed=False):
    return ImageFont.truetype(
        str(_vx23_font_path(condensed=condensed)),
        size=int(size),
    )


def _vx23_fit_font(draw, text, max_width, start, minimum, condensed=False):
    for size in range(int(start), int(minimum) - 1, -4):
        font = _vx23_font(size, condensed=condensed)
        box = draw.textbbox((0, 0), str(text), font=font)
        if box[2] - box[0] <= int(max_width):
            return font
    raise RuntimeError(f"Thumbnail text cannot fit safely: {text!r}")


def _vx23_cover(image, size):
    target_w, target_h = map(int, size)
    scale = max(target_w / image.width, target_h / image.height)
    resized = image.resize(
        (round(image.width * scale), round(image.height * scale)),
        Image.Resampling.LANCZOS,
    )
    left = max(0, (resized.width - target_w) // 2)
    top = max(0, (resized.height - target_h) // 2)
    return resized.crop((left, top, left + target_w, top + target_h))


def _vx23_horizontal_alpha(size, left, right):
    width, height = map(int, size)
    values = [
        round(left + (right - left) * (x / max(1, width - 1)))
        for x in range(width)
    ]
    strip = Image.new("L", (width, 1))
    strip.putdata(values)
    return strip.resize((width, height))


def _vx23_vertical_alpha(size, top, bottom):
    width, height = map(int, size)
    values = [
        round(top + (bottom - top) * (y / max(1, height - 1)))
        for y in range(height)
    ]
    strip = Image.new("L", (1, height))
    strip.putdata(values)
    return strip.resize((width, height))


def _vx23_rgba_layer(size, colour, alpha):
    layer = Image.new("RGBA", size, tuple(colour) + (255,))
    layer.putalpha(alpha)
    return layer


def _vx23_draw_shadow_text(
    draw,
    xy,
    text,
    font,
    fill,
    stroke_width=0,
    stroke_fill=(0, 0, 0, 255),
    shadow=14,
):
    x, y = xy
    if shadow:
        draw.text(
            (x + shadow, y + shadow),
            text,
            font=font,
            fill=(0, 0, 0, 185),
            stroke_width=stroke_width,
            stroke_fill=(0, 0, 0, 205),
        )
    draw.text(
        (x, y),
        text,
        font=font,
        fill=fill,
        stroke_width=stroke_width,
        stroke_fill=stroke_fill,
    )


def _vx23_draw_brand(draw, x, y, accent, secondary):
    box = (x, y, x + 800, y + 220)
    draw.rounded_rectangle(
        box,
        radius=46,
        fill=(3, 14, 28, 238),
        outline=accent,
        width=6,
    )
    brand_font = _vx23_font(91, condensed=True)
    draw.text((x + 62, y + 55), "FPL", font=brand_font, fill=secondary)
    draw.text((x + 292, y + 55), "VORTEX", font=brand_font, fill=accent)


def _vx23_draw_video_badge(draw, x, y, accent, ink):
    box = (x, y, x + 690, y + 238)
    draw.rounded_rectangle(
        (x + 18, y + 22, x + 708, y + 260),
        radius=52,
        fill=(0, 0, 0, 135),
    )
    draw.rounded_rectangle(
        box,
        radius=52,
        fill=accent,
        outline=(255, 255, 255, 245),
        width=8,
    )
    text = f"VIDEO {YT_VIDEO_PART}/{YT_VIDEO_TOTAL}"
    font = _vx23_fit_font(draw, text, 590, 112, 74, condensed=True)
    bounds = draw.textbbox((0, 0), text, font=font)
    text_w = bounds[2] - bounds[0]
    text_h = bounds[3] - bounds[1]
    draw.text(
        (x + (690 - text_w) / 2, y + (238 - text_h) / 2 - bounds[1]),
        text,
        font=font,
        fill=ink,
    )


def _vx23_draw_stat_card(draw, box, label, value, accent, white):
    x1, y1, x2, y2 = box
    draw.rounded_rectangle(
        (x1 + 14, y1 + 17, x2 + 14, y2 + 17),
        radius=42,
        fill=(0, 0, 0, 125),
    )
    draw.rounded_rectangle(
        box,
        radius=42,
        fill=(3, 17, 31, 235),
        outline=accent,
        width=5,
    )
    draw.rectangle((x1 + 28, y1 + 28, x1 + 44, y2 - 28), fill=accent)
    label_font = _vx23_fit_font(
        draw,
        label,
        x2 - x1 - 105,
        55,
        38,
        condensed=True,
    )
    value_font = _vx23_fit_font(
        draw,
        value,
        x2 - x1 - 105,
        82,
        50,
        condensed=True,
    )
    draw.text((x1 + 74, y1 + 38), label, font=label_font, fill=accent)
    draw.text((x1 + 74, y1 + 112), value, font=value_font, fill=white)


def _vx23_save_jpeg(image, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    for quality in (94, 92, 90, 88, 86, 84, 82, 80, 78, 76, 74, 72, 70, 68, 66):
        buffer = io.BytesIO()
        image.convert("RGB").save(
            buffer,
            format="JPEG",
            quality=quality,
            optimize=True,
            progressive=False,
            subsampling=2,
        )
        encoded = buffer.getvalue()
        if len(encoded) <= YT_THUMBNAIL_MAX_BYTES:
            temporary = path.with_name(f".{path.name}.tmp")
            temporary.write_bytes(encoded)
            os.replace(temporary, path)
            return quality
    raise RuntimeError(
        "Native 4K thumbnail could not be compressed below the safe 1.95 MB limit"
    )


def _vx23_short_name(player):
    value = str(
        player.get("name")
        or player.get("second_name")
        or player.get("full_name")
        or "PLAYER"
    ).strip()
    return value[:22].upper()


def _vx23_review_facts(review, review_gw, review_status):
    metrics = review.get("metrics") if isinstance(review.get("metrics"), dict) else {}
    players = [row for row in review.get("players", []) if isinstance(row, dict)]
    points = int(metrics.get("gw_points") or 0)
    top = max(players, key=lambda row: float(row.get("points") or 0), default={})
    captain = next((row for row in players if row.get("captain")), {})
    top_points = int(top.get("points") or 0)
    captain_points = int(captain.get("points") or 0) * max(
        1, int(captain.get("multiplier") or 1)
    )
    status_label = "LIVE REVIEW" if review_status == "LIVE/PARTIAL" else "REVIEW"
    return {
        "review_label": f"GW{review_gw} {status_label}",
        "review_value": f"{points} PTS",
        "top_label": "TOP RETURN",
        "top_value": f"{_vx23_short_name(top)} • {top_points} PTS",
        "captain_label": "CAPTAIN RETURN",
        "captain_value": f"{_vx23_short_name(captain)} • {captain_points} PTS",
    }


def _vx23_thumbnail(
    *,
    base_path,
    hero_path,
    output_path,
    preview_gw,
    review_gw,
    review_status,
    review,
):
    themes = [
        {"name": "electric-cyan", "accent": (35, 224, 255, 255), "secondary": (255, 205, 49, 255), "hot": (224, 38, 75, 255)},
        {"name": "vortex-lime", "accent": (152, 241, 36, 255), "secondary": (36, 218, 255, 255), "hot": (255, 70, 91, 255)},
        {"name": "royal-gold", "accent": (255, 190, 42, 255), "secondary": (77, 151, 255, 255), "hot": (236, 45, 94, 255)},
        {"name": "ultraviolet", "accent": (193, 82, 255, 255), "secondary": (45, 228, 247, 255), "hot": (255, 194, 41, 255)},
        {"name": "orange-flux", "accent": (255, 126, 35, 255), "secondary": (47, 225, 255, 255), "hot": (231, 43, 80, 255)},
        {"name": "mint-strike", "accent": (42, 240, 174, 255), "secondary": (255, 205, 49, 255), "hot": (255, 62, 121, 255)},
    ]
    headlines = (
        "GAME PLAN",
        "FIXTURE EDGE",
        "MODEL OUTLOOK",
        "ATTACK MAP",
        "DEFENCE WATCH",
        "GAMEWEEK MAP",
    )
    theme_index = (int(preview_gw) - 1) % len(themes)
    layout_index = (int(preview_gw) - 1) % 3
    theme = themes[theme_index]
    accent = theme["accent"]
    secondary = theme["secondary"]
    hot = theme["hot"]
    white = (250, 253, 255, 255)
    muted = (199, 220, 235, 255)
    ink = (3, 12, 23, 255)
    headline = headlines[(int(preview_gw) - 1) % len(headlines)]
    facts = _vx23_review_facts(review, review_gw, review_status)

    with Image.open(base_path) as image:
        base = _vx23_cover(image.convert("RGB"), YT_THUMBNAIL_SIZE)
    with Image.open(hero_path) as image:
        hero = _vx23_cover(image.convert("RGB"), YT_THUMBNAIL_SIZE)

    base = ImageEnhance.Color(base).enhance(1.15)
    base = ImageEnhance.Contrast(base).enhance(1.10)
    base = ImageEnhance.Brightness(base).enhance(0.36)
    base = base.filter(ImageFilter.GaussianBlur(radius=8)).convert("RGBA")
    hero = ImageEnhance.Color(hero).enhance(1.13)
    hero = ImageEnhance.Contrast(hero).enhance(1.08).convert("RGBA")
    canvas = base.copy()

    if layout_index == 0:
        hero_crop = hero.crop((1040, 0, 3840, 2160))
        hero_panel = _vx23_cover(hero_crop, (2320, 2160))
        hero_panel.putalpha(_vx23_horizontal_alpha(hero_panel.size, 0, 245))
        canvas.alpha_composite(hero_panel, (1520, 0))
        dark = _vx23_rgba_layer(
            YT_THUMBNAIL_SIZE,
            (1, 10, 23),
            _vx23_horizontal_alpha(YT_THUMBNAIL_SIZE, 252, 24),
        )
        canvas = Image.alpha_composite(canvas, dark)
        text_x = 135
        brand_xy = (130, 110)
        video_xy = (3010, 105)
        stat_boxes = ((2380, 1645, 3050, 1995), (3080, 1645, 3745, 1995))
        review_box = (145, 1740, 1370, 1985)
        diagonal = [(1710, 0), (1775, 0), (1525, 2160), (1460, 2160)]
    elif layout_index == 2:
        hero_crop = hero.crop((980, 0, 3840, 2160))
        hero_panel = _vx23_cover(hero_crop, (2320, 2160))
        hero_panel.putalpha(_vx23_horizontal_alpha(hero_panel.size, 245, 0))
        canvas.alpha_composite(hero_panel, (0, 0))
        dark = _vx23_rgba_layer(
            YT_THUMBNAIL_SIZE,
            (1, 10, 23),
            _vx23_horizontal_alpha(YT_THUMBNAIL_SIZE, 24, 252),
        )
        canvas = Image.alpha_composite(canvas, dark)
        text_x = 2010
        brand_xy = (2910, 110)
        video_xy = (130, 105)
        stat_boxes = ((100, 1645, 770, 1995), (800, 1645, 1470, 1995))
        review_box = (2010, 1740, 3680, 1985)
        diagonal = [(2070, 0), (2135, 0), (1900, 2160), (1835, 2160)]
    else:
        hero_overlay = hero.copy()
        hero_overlay.putalpha(170)
        canvas = Image.alpha_composite(canvas, hero_overlay)
        top_dark = _vx23_rgba_layer(
            YT_THUMBNAIL_SIZE,
            (1, 8, 20),
            _vx23_vertical_alpha(YT_THUMBNAIL_SIZE, 210, 55),
        )
        bottom_dark = _vx23_rgba_layer(
            YT_THUMBNAIL_SIZE,
            (1, 8, 20),
            _vx23_vertical_alpha(YT_THUMBNAIL_SIZE, 35, 245),
        )
        canvas = Image.alpha_composite(canvas, top_dark)
        canvas = Image.alpha_composite(canvas, bottom_dark)
        text_x = None
        brand_xy = (130, 110)
        video_xy = (3010, 105)
        stat_boxes = (
            (105, 1700, 1250, 2030),
            (1348, 1700, 2492, 2030),
            (2590, 1700, 3735, 2030),
        )
        review_box = None
        diagonal = None

    pattern = Image.new("RGBA", YT_THUMBNAIL_SIZE, (0, 0, 0, 0))
    pattern_draw = ImageDraw.Draw(pattern)
    for offset in (-250, 780, 1810, 2840, 3870):
        pattern_draw.polygon(
            [
                (offset, 0),
                (offset + 28, 0),
                (offset - 125, 2160),
                (offset - 153, 2160),
            ],
            fill=tuple(accent[:3]) + (30,),
        )
    canvas = Image.alpha_composite(canvas, pattern)
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 0, 3840, 28), fill=accent)
    draw.rectangle((0, 2132, 3840, 2160), fill=secondary)
    if diagonal:
        draw.polygon(diagonal, fill=accent)

    _vx23_draw_brand(draw, *brand_xy, accent, secondary)
    _vx23_draw_video_badge(draw, *video_xy, accent, ink)

    gw_text = f"GW{int(preview_gw)}"
    hook = "FIXTURES • GOALS • CLEAN SHEETS"
    if layout_index in (0, 2):
        zone_width = 1480 if layout_index == 0 else 1690
        gw_font = _vx23_fit_font(draw, gw_text, zone_width, 520, 350, condensed=True)
        _vx23_draw_shadow_text(
            draw,
            (text_x, 445),
            gw_text,
            gw_font,
            secondary,
            stroke_width=12,
            stroke_fill=(0, 0, 0, 225),
        )
        headline_font = _vx23_fit_font(
            draw,
            headline,
            zone_width,
            265,
            172,
            condensed=True,
        )
        _vx23_draw_shadow_text(
            draw,
            (text_x + 10, 1035),
            headline,
            headline_font,
            white,
            stroke_width=8,
            stroke_fill=(0, 0, 0, 225),
            shadow=11,
        )
        draw.rectangle((text_x + 10, 1370, text_x + zone_width - 55, 1390), fill=accent)
        hook_font = _vx23_fit_font(draw, hook, zone_width - 30, 82, 52, condensed=True)
        draw.text((text_x + 15, 1452), hook, font=hook_font, fill=muted)

        draw.rounded_rectangle(
            review_box,
            radius=46,
            fill=hot,
            outline=white,
            width=7,
        )
        review_text = f"{facts['review_label']} • {facts['review_value']}"
        review_font = _vx23_fit_font(
            draw,
            review_text,
            review_box[2] - review_box[0] - 90,
            86,
            55,
            condensed=True,
        )
        review_bounds = draw.textbbox((0, 0), review_text, font=review_font)
        review_y = (
            review_box[1]
            + (review_box[3] - review_box[1] - (review_bounds[3] - review_bounds[1])) / 2
            - review_bounds[1]
        )
        draw.text(
            (review_box[0] + 45, review_y),
            review_text,
            font=review_font,
            fill=white,
        )
        _vx23_draw_stat_card(
            draw,
            stat_boxes[0],
            facts["top_label"],
            facts["top_value"],
            accent,
            white,
        )
        _vx23_draw_stat_card(
            draw,
            stat_boxes[1],
            facts["captain_label"],
            facts["captain_value"],
            secondary,
            white,
        )
    else:
        panel = (545, 390, 3295, 1585)
        draw.rounded_rectangle(
            (panel[0] + 22, panel[1] + 28, panel[2] + 22, panel[3] + 28),
            radius=84,
            fill=(0, 0, 0, 130),
        )
        draw.rounded_rectangle(
            panel,
            radius=84,
            fill=(2, 13, 27, 206),
            outline=accent,
            width=7,
        )
        gw_font = _vx23_fit_font(draw, gw_text, 2350, 520, 360, condensed=True)
        gw_box = draw.textbbox((0, 0), gw_text, font=gw_font, stroke_width=12)
        gw_x = 1920 - (gw_box[2] - gw_box[0]) / 2
        _vx23_draw_shadow_text(
            draw,
            (gw_x, 440),
            gw_text,
            gw_font,
            secondary,
            stroke_width=12,
            stroke_fill=(0, 0, 0, 225),
        )
        headline_font = _vx23_fit_font(draw, headline, 2400, 250, 175, condensed=True)
        headline_box = draw.textbbox((0, 0), headline, font=headline_font)
        headline_x = 1920 - (headline_box[2] - headline_box[0]) / 2
        _vx23_draw_shadow_text(
            draw,
            (headline_x, 1035),
            headline,
            headline_font,
            white,
            stroke_width=8,
            stroke_fill=(0, 0, 0, 225),
            shadow=11,
        )
        hook_font = _vx23_fit_font(draw, hook, 2250, 88, 58, condensed=True)
        hook_box = draw.textbbox((0, 0), hook, font=hook_font)
        hook_x = 1920 - (hook_box[2] - hook_box[0]) / 2
        draw.rectangle((770, 1395, 3070, 1417), fill=accent)
        draw.text((hook_x, 1460), hook, font=hook_font, fill=muted)
        _vx23_draw_stat_card(
            draw,
            stat_boxes[0],
            facts["review_label"],
            facts["review_value"],
            hot,
            white,
        )
        _vx23_draw_stat_card(
            draw,
            stat_boxes[1],
            facts["top_label"],
            facts["top_value"],
            accent,
            white,
        )
        _vx23_draw_stat_card(
            draw,
            stat_boxes[2],
            facts["captain_label"],
            facts["captain_value"],
            secondary,
            white,
        )

    visible_text = " ".join(
        [
            "FPL VORTEX",
            f"VIDEO {YT_VIDEO_PART}/{YT_VIDEO_TOTAL}",
            gw_text,
            headline,
            hook,
            *facts.values(),
        ]
    )
    if re.search(r"\bDAY\s*\d", visible_text, flags=re.IGNORECASE):
        raise RuntimeError("YouTube thumbnail must never use Day numbering")

    quality = _vx23_save_jpeg(canvas, output_path)
    with Image.open(output_path) as saved:
        if saved.size != YT_THUMBNAIL_SIZE:
            raise RuntimeError(
                f"Thumbnail is not true 4K: expected={YT_THUMBNAIL_SIZE}, actual={saved.size}"
            )
    return {
        "file": str(output_path),
        "resolution": list(YT_THUMBNAIL_SIZE),
        "format": "JPEG",
        "size_bytes": Path(output_path).stat().st_size,
        "sha256": _vx23_sha256(output_path),
        "jpeg_quality": quality,
        "series_badge": f"VIDEO {YT_VIDEO_PART}/{YT_VIDEO_TOTAL}",
        "headline": f"{gw_text} {headline}",
        "theme": theme["name"],
        "theme_index": theme_index,
        "layout_index": layout_index,
        "design_seed": f"GW{int(preview_gw):02d}-T{theme_index}-L{layout_index}",
        "base_source": str(base_path),
        "hero_source": str(hero_path),
        "contains_day_numbering": False,
    }


def _vx23_timestamp(total_seconds):
    whole = max(0, int(float(total_seconds)))
    hours, remainder = divmod(whole, 3600)
    minutes, seconds = divmod(remainder, 60)
    return (
        f"{hours}:{minutes:02d}:{seconds:02d}"
        if hours
        else f"{minutes}:{seconds:02d}"
    )


def _vx23_chapters(qa, review_gw):
    labels = {
        "00_intro": "FPL VORTEX intro",
        "gw_review": f"GW{review_gw} review",
        "02_fdr": "Fixture difficulty",
        "03_projected_goals": "Projected goals",
        "04_clean_sheets": "Clean-sheet odds",
        "05_outro": "Next video",
    }
    integration = qa.get("external_media_integration")
    if not isinstance(integration, dict):
        integration = {}
    opening = integration.get("opening")
    if not isinstance(opening, dict):
        opening = {}
    opening_duration = (
        float(opening.get("duration") or 0.0)
        if integration.get("applied") is True
        else 0.0
    )
    elapsed = opening_duration
    rows = []
    for index, section in enumerate(qa.get("sections", [])):
        if not isinstance(section, dict):
            continue
        key = str(section.get("key") or "")
        label = labels.get(key, str(section.get("scene") or key).title())
        if index == 0 and key == "00_intro" and opening_duration > 0:
            rows.append("0:00 Opening & FPL VORTEX intro")
        else:
            rows.append(f"{_vx23_timestamp(elapsed)} {label}")
        elapsed += float(section.get("duration") or 0.0)
    if not rows or not rows[0].startswith("0:00 "):
        raise RuntimeError("YouTube chapters must begin at 0:00")
    return rows


def _vx23_metadata(preview_gw, review_gw, review_status, qa):
    title = (
        f"FPL GW{preview_gw} Tips: Fixtures, Goals & Clean Sheets | "
        f"GW{review_gw} Review (1/3) #FPL"
    )
    status_text = "live/partial" if review_status == "LIVE/PARTIAL" else "completed"
    hashtags = ["#FPL", "#FantasyPremierLeague", f"#FPLGW{preview_gw}"]
    chapters = _vx23_chapters(qa, review_gw)
    description = "\n".join(
        [
            (
                f"FPL Gameweek {preview_gw} tips and preview, plus a {status_text} "
                f"GW{review_gw} review from FPL VORTEX."
            ),
            "",
            f"VIDEO {YT_VIDEO_PART}/{YT_VIDEO_TOTAL} for Gameweek {preview_gw} covers:",
            f"• GW{review_gw} squad review and key returns",
            f"• GW{preview_gw}–GW{min(38, preview_gw + 5)} fixture difficulty",
            "• Model-projected team goals and attacking outlook",
            "• Clean-sheet probabilities and defensive outlook",
            "",
            "CHAPTERS",
            *chapters,
            "",
            "FPL VORTEX uses public FPL data and model-led analysis. Projections can change with new team news and are provided for information, not guarantees.",
            "",
            " ".join(hashtags),
        ]
    )
    tags = [
        "FPL",
        "Fantasy Premier League",
        f"FPL GW{preview_gw}",
        f"Gameweek {preview_gw}",
        f"FPL GW{preview_gw} tips",
        f"FPL GW{preview_gw} preview",
        "FPL fixtures",
        "fixture difficulty",
        "projected goals",
        "clean sheet odds",
        "FPL review",
        "FPL VORTEX",
    ]
    if len(title) > 100:
        raise RuntimeError(f"YouTube title exceeds 100 characters: {len(title)}")
    if len(description.encode("utf-8")) > 5000:
        raise RuntimeError("YouTube description exceeds 5,000 UTF-8 bytes")
    if sum(len(tag) for tag in tags) + max(0, len(tags) - 1) > 500:
        raise RuntimeError("YouTube tags exceed the 500-character limit")
    return {
        "title": title,
        "description": description,
        "hashtags": hashtags,
        "tags": tags,
        "chapters": chapters,
        "category": "Sports",
        "category_id": "17",
        "language": "en-GB",
        "recommended_visibility": "Private",
        "made_for_kids": False,
        "notify_subscribers_before_manual_publish": False,
        "series": {"part": YT_VIDEO_PART, "total": YT_VIDEO_TOTAL},
        "preview_gw": int(preview_gw),
        "review_gw": int(review_gw),
        "review_status": review_status,
    }


def build_youtube_studio_package(output_root):
    output_root = Path(output_root).resolve()
    video_dir = output_root / "MP4"
    slide_dir = output_root / "SLIDE"
    data_dir = output_root / "DATA"
    for folder in (video_dir, slide_dir, data_dir):
        if not folder.is_dir():
            raise FileNotFoundError(f"Required Day 1 output directory is missing: {folder}")

    qa = _vx23_read_json(data_dir / "final_video_qa.json", "final video QA")
    review = _vx23_read_json(
        data_dir / "gw_review_package.json",
        "Gameweek review package",
    )
    if qa.get("passed") is not True:
        raise RuntimeError("Final video QA has not passed; refusing YouTube packaging")
    if qa.get("external_media_integration", {}).get("applied") is not True:
        raise RuntimeError(
            "Opening/music integration must pass before the YouTube Studio package is built"
        )

    configured_video = str(qa.get("final_file") or "").strip()
    if configured_video:
        candidate = Path(configured_video)
        final_video = video_dir / candidate.name
        if final_video.resolve().parent != video_dir.resolve():
            raise RuntimeError(f"Final QA points outside the MP4 directory: {candidate}")
        final_video = _vx23_required_file(final_video, "final 4K MP4")
    else:
        final_video = _vx23_single_file(
            video_dir,
            "*COMBINED*.mp4",
            "combined final MP4",
        )

    video_probe = _vx23_probe_video(final_video)
    if (video_probe["width"], video_probe["height"]) != YT_THUMBNAIL_SIZE:
        raise RuntimeError(
            "YouTube Studio package requires a true 4K final MP4; "
            f"found {video_probe['width']}x{video_probe['height']}"
        )
    if list(qa.get("video_output") or []) != [3840, 2160]:
        raise RuntimeError(
            f"Final video QA is not true 4K: {qa.get('video_output')}"
        )

    match = re.search(r"(?:^|_)GW(\d{1,2})(?:_|$)", final_video.name, re.I)
    preview_gw = int(match.group(1)) if match else 0
    review_gw = int(review.get("gw") or 0)
    if not 1 <= preview_gw <= 38 or not 1 <= review_gw <= 38:
        raise RuntimeError(
            f"Invalid dynamic Gameweeks: preview={preview_gw}, review={review_gw}"
        )

    review_status = str(review.get("status") or "FINAL").upper()
    if review_status not in {"FINAL", "LIVE/PARTIAL"}:
        raise RuntimeError(f"Invalid Gameweek review status: {review_status!r}")

    source_candidates = [
        ("fixture", slide_dir / "fdr_scene_4k.png"),
        ("attack", slide_dir / "projected_goals_scene_4k.png"),
        ("defence", slide_dir / "clean_sheet_scene_4k.png"),
        ("agenda", slide_dir / "intro_scene_4k.png"),
    ]
    available = [(name, path) for name, path in source_candidates if path.is_file()]
    if not available:
        raise FileNotFoundError("No rendered 4K slide is available for the thumbnail")
    focus_name, base_path = available[(preview_gw - 1) % len(available)]
    review_path = slide_dir / "gw_review_scene_4k.png"
    hero_path = review_path if review_path.is_file() else base_path

    thumbnail_path = slide_dir / (
        f"FPL_VORTEX_GW{preview_gw}_VIDEO_{YT_VIDEO_PART}_OF_"
        f"{YT_VIDEO_TOTAL}_THUMBNAIL_4K.jpg"
    )
    thumbnail = _vx23_thumbnail(
        base_path=base_path,
        hero_path=hero_path,
        output_path=thumbnail_path,
        preview_gw=preview_gw,
        review_gw=review_gw,
        review_status=review_status,
        review=review,
    )
    thumbnail["content_focus"] = focus_name
    metadata = _vx23_metadata(preview_gw, review_gw, review_status, qa)

    package = {
        "version": "FPL-VORTEX-YOUTUBE-PRIVATE-AUTO-V3",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "manual_upload_only": False,
        "youtube_api_upload_enabled": True,
        "automatic_publication_enabled": False,
        "upload_policy": {
            "privacy_status": "private",
            "notify_subscribers": False,
            "automatic_publish": False,
            "automatic_schedule": False,
            "manual_publication_required": True,
        },
        "video": {
            "file": str(final_video),
            "resolution": [video_probe["width"], video_probe["height"]],
            "codec": video_probe["codec"],
            "fps": video_probe["fps"],
            "duration_seconds": video_probe["duration"],
        },
        "thumbnail": thumbnail,
        "metadata": metadata,
        "studio_checklist": [
            "GitHub uploads the packaged 4K MP4 to the verified channel as Private.",
            "GitHub applies this generated title, description, tags, and thumbnail.",
            "Open the video in YouTube Studio and review every field.",
            "Only change Visibility to Public manually when the video is ready.",
        ],
    }
    package_path = data_dir / "youtube_video_1_of_3_package.json"
    _vx23_atomic_json(package_path, package)

    print("\n" + "=" * 74)
    print("✅ PRIVATE YOUTUBE UPLOAD PACKAGE — VIDEO 1/3")
    print("=" * 74)
    print("✅ YouTube API upload enabled: Private only; automatic publishing disabled")
    print(f"✅ Dynamic preview/review: GW{preview_gw} / GW{review_gw} ({review_status})")
    print(
        f"✅ Thumbnail variation: {thumbnail['theme']} • "
        f"layout {thumbnail['layout_index'] + 1}/3 • focus {focus_name}"
    )
    print(f"✅ Thumbnail: {thumbnail_path}")
    print(f"✅ Copy/paste package: {package_path}")
    print("\nTITLE\n" + metadata["title"])
    print("\nDESCRIPTION\n" + metadata["description"])
    return package


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Create the dynamic 4K thumbnail and SEO copy package for manual "
            "YouTube Studio upload"
        )
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path(os.environ.get("DAY1_OUTPUT_ROOT", DEFAULT_OUTPUT_ROOT)),
    )
    args = parser.parse_args()
    build_youtube_studio_package(args.output_root)


if __name__ == "__main__":
    main()
