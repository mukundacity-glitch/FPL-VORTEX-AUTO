from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
from fractions import Fraction
from pathlib import Path
from typing import Any

from .common import required_file, write_json_atomic


DEFAULT_OUTPUT_ROOT = Path("/content/drive/MyDrive/FPL_VORTEX/Day_2")
OPENING_NAME = "opening.mp4"
BACKGROUND_NAME = "Background_music.mp3"
OUTRO_NAME = "outro.mp3"

# Locked to the existing Day 1 media contract. Do not tune these independently.
NARRATION_LEVEL = 255
MUSIC_LEVEL = 12
MUSIC_GAIN = MUSIC_LEVEL / NARRATION_LEVEL
OUTRO_SECONDS = 6.0

PROGRAM_NAME = "FPL_VORTEX_DAY2_FINAL.mp4"
FINAL_NAME = "FPL_VORTEX_DAY2_PRIVATE_REVIEW.mp4"
FINAL_AUDIO_NAME = "FPL_VORTEX_DAY2_PRIVATE_REVIEW.mp3"
REPORT_NAME = "day2_media_qa.json"


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "unknown command failure").strip()
        raise RuntimeError(
            f"Command failed ({command[0]}): {detail[-5000:]}"
        ) from exc


def _probe(path: Path) -> dict[str, Any]:
    return json.loads(
        _run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_format",
                "-show_streams",
                "-of",
                "json",
                str(path),
            ]
        ).stdout
    )


def _stream(payload: dict[str, Any], codec_type: str) -> dict[str, Any]:
    stream = next(
        (
            item
            for item in payload.get("streams", [])
            if item.get("codec_type") == codec_type
        ),
        None,
    )
    if stream is None:
        raise RuntimeError(f"Media is missing a {codec_type} stream")
    return stream


def _duration(payload: dict[str, Any]) -> float:
    candidates = [payload.get("format", {}).get("duration")]
    candidates.extend(item.get("duration") for item in payload.get("streams", []))
    for value in candidates:
        try:
            duration = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(duration) and duration > 0:
            return duration
    raise RuntimeError("Media duration is missing or invalid")


def _fps(stream: dict[str, Any]) -> Fraction:
    for key in ("avg_frame_rate", "r_frame_rate"):
        value = str(stream.get(key) or "")
        try:
            rate = Fraction(value)
        except (ValueError, ZeroDivisionError):
            continue
        if rate > 0:
            return rate
    raise RuntimeError("Video frame rate is missing or invalid")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _filter_graph(
    *,
    width: int,
    height: int,
    fps: Fraction,
    opening_duration: float,
    program_duration: float,
) -> str:
    background_duration = program_duration - OUTRO_SECONDS
    if background_duration <= 0:
        raise RuntimeError(
            f"Day 2 program must be longer than {OUTRO_SECONDS:g} seconds"
        )

    fps_text = f"{fps.numerator}/{fps.denominator}"
    opening_text = f"{opening_duration:.6f}"
    program_text = f"{program_duration:.6f}"
    background_text = f"{background_duration:.6f}"
    gain_text = f"{MUSIC_GAIN:.15f}"
    outro_delay_ms = int(round(background_duration * 1000.0))

    background_fade = min(0.25, background_duration / 2.0)
    background_fade_start = max(0.0, background_duration - background_fade)
    outro_fade_in = 0.25
    outro_fade_out = 0.50
    outro_fade_out_start = OUTRO_SECONDS - outro_fade_out

    video_chain = (
        f"scale=w={width}:h={height}:force_original_aspect_ratio=decrease:"
        "force_divisible_by=2,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
        "setsar=1,"
        f"fps={fps_text},format=yuv420p,settb=AVTB"
    )
    audio_format = (
        "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
    )

    return ";".join(
        (
            f"[0:v]trim=duration={opening_text},setpts=PTS-STARTPTS,"
            f"{video_chain}[opening_video]",
            f"[0:a]{audio_format},apad=pad_dur={opening_text},"
            f"atrim=duration={opening_text},asetpts=PTS-STARTPTS[opening_audio]",
            f"[1:v]trim=duration={program_text},setpts=PTS-STARTPTS,"
            f"{video_chain}[program_video]",
            f"[1:a]{audio_format},apad=pad_dur={program_text},"
            f"atrim=duration={program_text},asetpts=PTS-STARTPTS[narration]",
            f"[2:a]{audio_format},atrim=duration={background_text},"
            "asetpts=PTS-STARTPTS,"
            f"volume={gain_text},"
            f"afade=t=out:st={background_fade_start:.6f}:d={background_fade:.6f}"
            "[background]",
            f"[3:a]{audio_format},atrim=duration={OUTRO_SECONDS:.6f},"
            "asetpts=PTS-STARTPTS,"
            f"volume={gain_text},afade=t=in:st=0:d={outro_fade_in:.6f},"
            f"afade=t=out:st={outro_fade_out_start:.6f}:d={outro_fade_out:.6f},"
            f"adelay={outro_delay_ms}|{outro_delay_ms}[outro_music]",
            "[narration][background][outro_music]"
            "amix=inputs=3:duration=first:dropout_transition=0:normalize=0,"
            "alimiter=limit=0.99:level=0[program_audio]",
            "[opening_video][opening_audio][program_video][program_audio]"
            "concat=n=2:v=1:a=1[final_video][final_audio]",
        )
    )


def _integrate_media_stream_copy(
    output_root: Path,
    asset_dir: Path,
) -> dict[str, Any]:
    """Apply the Day 1 audio/opening contract without re-encoding the 10-minute 4K program."""
    output_root = output_root.resolve()
    asset_dir = asset_dir.resolve()

    mp4_dir = output_root / "MP4"
    mp3_dir = output_root / "MP3"
    data_dir = output_root / "Data"
    for folder in (mp4_dir, mp3_dir, data_dir):
        if not folder.is_dir():
            raise FileNotFoundError(
                f"Required Day 2 output directory is missing: {folder}"
            )

    program = required_file(mp4_dir / PROGRAM_NAME, "Day 2 program MP4")
    opening = required_file(asset_dir / OPENING_NAME, "shared opening MP4")
    background = required_file(asset_dir / BACKGROUND_NAME, "shared background music")
    outro = required_file(asset_dir / OUTRO_NAME, "shared outro music")

    program_probe = _probe(program)
    opening_probe = _probe(opening)
    background_probe = _probe(background)
    outro_probe = _probe(outro)

    program_video = _stream(program_probe, "video")
    _stream(program_probe, "audio")
    _stream(opening_probe, "video")
    _stream(opening_probe, "audio")
    _stream(background_probe, "audio")
    _stream(outro_probe, "audio")

    if str(program_video.get("codec_name") or "").lower() != "h264":
        raise RuntimeError(
            "Day 2 stream-copy media path requires the verified H.264 program"
        )

    program_duration = _duration(program_probe)
    opening_duration = _duration(opening_probe)
    background_source_duration = _duration(background_probe)
    outro_source_duration = _duration(outro_probe)

    if opening_duration <= 0.5:
        raise RuntimeError("opening.mp4 is too short")
    if program_duration <= OUTRO_SECONDS + 0.5:
        raise RuntimeError(
            "Day 2 program is too short for the six-second outro contract"
        )
    if background_source_duration <= 0.5:
        raise RuntimeError("Background_music.mp3 is too short")
    if outro_source_duration + 0.02 < OUTRO_SECONDS:
        raise RuntimeError(
            f"outro.mp3 must be at least {OUTRO_SECONDS:g} seconds; "
            f"found {outro_source_duration:.3f}"
        )

    width = int(program_video.get("width") or 0)
    height = int(program_video.get("height") or 0)
    fps = _fps(program_video)
    if width <= 0 or height <= 0:
        raise RuntimeError("Day 2 program resolution is invalid")

    background_duration = program_duration - OUTRO_SECONDS
    background_fade = min(0.25, background_duration / 2.0)
    background_fade_start = max(0.0, background_duration - background_fade)
    outro_delay_ms = int(round(background_duration * 1000.0))
    gain_text = f"{MUSIC_GAIN:.15f}"

    audio_format = (
        "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
    )
    audio_graph = ";".join(
        (
            f"[0:a]{audio_format},apad=pad_dur={program_duration:.6f},"
            f"atrim=duration={program_duration:.6f},"
            "asetpts=PTS-STARTPTS[narration]",
            f"[1:a]{audio_format},atrim=duration={background_duration:.6f},"
            "asetpts=PTS-STARTPTS,"
            f"volume={gain_text},"
            f"afade=t=out:st={background_fade_start:.6f}:"
            f"d={background_fade:.6f}[background]",
            f"[2:a]{audio_format},atrim=duration={OUTRO_SECONDS:.6f},"
            "asetpts=PTS-STARTPTS,"
            f"volume={gain_text},"
            "afade=t=in:st=0:d=0.250000,"
            f"afade=t=out:st={OUTRO_SECONDS - 0.5:.6f}:d=0.500000,"
            f"adelay={outro_delay_ms}|{outro_delay_ms}[outro_music]",
            "[narration][background][outro_music]"
            "amix=inputs=3:duration=first:dropout_transition=0:normalize=0,"
            "alimiter=limit=0.99:level=0[program_audio]",
        )
    )

    final_mp4 = mp4_dir / FINAL_NAME
    final_mp3 = mp3_dir / FINAL_AUDIO_NAME
    report_path = data_dir / REPORT_NAME

    staging_dir = Path(
        tempfile.mkdtemp(prefix=".day2-media-fast-", dir=str(mp4_dir))
    )
    opening_ts = staging_dir / "opening.ts"
    program_ts = staging_dir / "program.ts"
    concat_list = staging_dir / "concat.txt"
    staged_mp4 = staging_dir / FINAL_NAME
    staged_mp3 = staging_dir / FINAL_AUDIO_NAME

    try:
        fps_text = f"{fps.numerator}/{fps.denominator}"
        opening_video_filter = (
            f"scale=w={width}:h={height}:force_original_aspect_ratio=decrease:"
            "force_divisible_by=2,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"setsar=1,fps={fps_text},format=yuv420p"
        )
        _run(
            [
                "ffmpeg", "-y", "-v", "error",
                "-i", str(opening),
                "-t", f"{opening_duration:.6f}",
                "-vf", opening_video_filter,
                "-af", "aformat=sample_rates=48000:channel_layouts=stereo",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "18",
                "-pix_fmt", "yuv420p",
                "-r", fps_text,
                "-c:a", "aac",
                "-b:a", "192k",
                "-ar", "48000",
                "-ac", "2",
                "-f", "mpegts",
                str(opening_ts),
            ]
        )

        _run(
            [
                "ffmpeg", "-y", "-v", "error",
                "-i", str(program),
                "-stream_loop", "-1",
                "-i", str(background),
                "-i", str(outro),
                "-filter_complex", audio_graph,
                "-map", "0:v:0",
                "-map", "[program_audio]",
                "-c:v", "copy",
                "-bsf:v", "h264_mp4toannexb",
                "-c:a", "aac",
                "-b:a", "192k",
                "-ar", "48000",
                "-ac", "2",
                "-f", "mpegts",
                str(program_ts),
            ]
        )

        concat_list.write_text(
            "file '" + str(opening_ts).replace("'", "'\\''") + "'\n"
            "file '" + str(program_ts).replace("'", "'\\''") + "'\n",
            encoding="utf-8",
        )
        _run(
            [
                "ffmpeg", "-y", "-v", "error",
                "-f", "concat",
                "-safe", "0",
                "-i", str(concat_list),
                "-map", "0:v:0",
                "-map", "0:a:0",
                "-c:v", "copy",
                "-c:a", "copy",
                "-bsf:a", "aac_adtstoasc",
                "-movflags", "+faststart",
                str(staged_mp4),
            ]
        )

        staged_probe = _probe(staged_mp4)
        staged_video = _stream(staged_probe, "video")
        _stream(staged_probe, "audio")
        staged_duration = _duration(staged_probe)
        expected_duration = opening_duration + program_duration
        if abs(staged_duration - expected_duration) > 0.40:
            raise RuntimeError(
                "Integrated Day 2 duration mismatch: "
                f"expected={expected_duration:.3f}, actual={staged_duration:.3f}"
            )
        if (
            int(staged_video.get("width") or 0) != width
            or int(staged_video.get("height") or 0) != height
        ):
            raise RuntimeError(
                "Day 2 stream-copy integration changed the program resolution"
            )

        _run(
            [
                "ffmpeg", "-y", "-v", "error",
                "-i", str(staged_mp4),
                "-map", "0:a:0",
                "-vn",
                "-c:a", "libmp3lame",
                "-b:a", "192k",
                str(staged_mp3),
            ]
        )
        mp3_duration = _duration(_probe(staged_mp3))
        if abs(mp3_duration - staged_duration) > 0.25:
            raise RuntimeError(
                "Integrated Day 2 MP3 duration does not match the final video"
            )

        os.replace(staged_mp4, final_mp4)
        os.replace(staged_mp3, final_mp3)
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)

    final_probe = _probe(final_mp4)
    final_duration = _duration(final_probe)
    final_video = _stream(final_probe, "video")
    _stream(final_probe, "audio")

    background_end = opening_duration + program_duration - OUTRO_SECONDS
    report: dict[str, Any] = {
        "version": "FPL-VORTEX-DAY2-EXTERNAL-MEDIA-V2-STREAM-COPY",
        "passed": True,
        "isolation": {
            "day": 2,
            "output_root": str(output_root),
            "day1_output_written": False,
        },
        "program": {
            "file": str(program),
            "sha256": _sha256(program),
            "duration": program_duration,
            "video_reencoded": False,
        },
        "opening": {
            "name": opening.name,
            "sha256": _sha256(opening),
            "duration": opening_duration,
            "original_audio_preserved": True,
            "timeline_start": 0.0,
            "timeline_end": opening_duration,
        },
        "background_music": {
            "name": background.name,
            "sha256": _sha256(background),
            "source_duration": background_source_duration,
            "looped_as_needed": True,
            "timeline_anchor": "slide_00_intro_start",
            "timeline_start": opening_duration,
            "timeline_end": background_end,
        },
        "outro_music": {
            "name": outro.name,
            "sha256": _sha256(outro),
            "source_duration": outro_source_duration,
            "excerpt_start": 0.0,
            "excerpt_duration": OUTRO_SECONDS,
            "timeline_start": background_end,
            "timeline_end": final_duration,
        },
        "mix": {
            "narration_level": NARRATION_LEVEL,
            "music_level": MUSIC_LEVEL,
            "music_gain": MUSIC_GAIN,
        },
        "visual_contract": {
            "canonical_vortex_logo_and_lion_head":
                "owned by Day 2 notebook scene assets",
            "subscribe_cta": "owned by Day 2 notebook scene_07_outro",
        },
        "final": {
            "file": str(final_mp4),
            "audio_file": str(final_mp3),
            "sha256": _sha256(final_mp4),
            "duration": final_duration,
            "resolution": [
                int(final_video.get("width") or 0),
                int(final_video.get("height") or 0),
            ],
            "fps": float(_fps(final_video)),
            "program_video_stream_copy": True,
        },
    }
    write_json_atomic(report_path, report)

    print("[DAY 2 MEDIA] PASS")
    print(
        "[DAY 2 MEDIA] 10-minute 4K program video stream copied; "
        "only audio/opening were encoded"
    )
    print(f"[DAY 2 MEDIA] Opening: {opening.name} ({opening_duration:.3f}s)")
    print(
        "[DAY 2 MEDIA] Background starts at slide_00 after the opening: "
        f"narration {NARRATION_LEVEL} / music {MUSIC_LEVEL} "
        f"(gain {MUSIC_GAIN:.8f})"
    )
    print(f"[DAY 2 MEDIA] Outro music: final {OUTRO_SECONDS:.3f}s only")
    print(f"[DAY 2 MEDIA] Final private-review video: {final_mp4}")
    return report


def integrate_media(output_root: Path, asset_dir: Path) -> dict[str, Any]:
    return _integrate_media_stream_copy(output_root, asset_dir)

    output_root = output_root.resolve()
    asset_dir = asset_dir.resolve()

    mp4_dir = output_root / "MP4"
    mp3_dir = output_root / "MP3"
    data_dir = output_root / "Data"
    for folder in (mp4_dir, mp3_dir, data_dir):
        if not folder.is_dir():
            raise FileNotFoundError(f"Required Day 2 output directory is missing: {folder}")

    program = required_file(mp4_dir / PROGRAM_NAME, "Day 2 program MP4")
    opening = required_file(asset_dir / OPENING_NAME, "shared opening MP4")
    background = required_file(asset_dir / BACKGROUND_NAME, "shared background music")
    outro = required_file(asset_dir / OUTRO_NAME, "shared outro music")

    program_probe = _probe(program)
    opening_probe = _probe(opening)
    background_probe = _probe(background)
    outro_probe = _probe(outro)

    program_video = _stream(program_probe, "video")
    _stream(program_probe, "audio")
    _stream(opening_probe, "video")
    _stream(opening_probe, "audio")
    _stream(background_probe, "audio")
    _stream(outro_probe, "audio")

    program_duration = _duration(program_probe)
    opening_duration = _duration(opening_probe)
    background_source_duration = _duration(background_probe)
    outro_source_duration = _duration(outro_probe)

    if opening_duration <= 0.5:
        raise RuntimeError("opening.mp4 is too short")
    if program_duration <= OUTRO_SECONDS + 0.5:
        raise RuntimeError("Day 2 program is too short for the six-second outro contract")
    if background_source_duration <= 0.5:
        raise RuntimeError("Background_music.mp3 is too short")
    if outro_source_duration + 0.02 < OUTRO_SECONDS:
        raise RuntimeError(
            f"outro.mp3 must be at least {OUTRO_SECONDS:g} seconds; "
            f"found {outro_source_duration:.3f}"
        )

    width = int(program_video.get("width") or 0)
    height = int(program_video.get("height") or 0)
    fps = _fps(program_video)
    if width <= 0 or height <= 0:
        raise RuntimeError("Day 2 program resolution is invalid")

    final_mp4 = mp4_dir / FINAL_NAME
    final_mp3 = mp3_dir / FINAL_AUDIO_NAME
    report_path = data_dir / REPORT_NAME
    graph = _filter_graph(
        width=width,
        height=height,
        fps=fps,
        opening_duration=opening_duration,
        program_duration=program_duration,
    )

    staging_dir = Path(
        tempfile.mkdtemp(prefix=".day2-media-", dir=str(mp4_dir))
    )
    staged_mp4 = staging_dir / FINAL_NAME
    staged_mp3 = staging_dir / FINAL_AUDIO_NAME
    try:
        _run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-i",
                str(opening),
                "-i",
                str(program),
                "-stream_loop",
                "-1",
                "-i",
                str(background),
                "-i",
                str(outro),
                "-filter_complex",
                graph,
                "-map",
                "[final_video]",
                "-map",
                "[final_audio]",
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                "-r",
                f"{fps.numerator}/{fps.denominator}",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-ar",
                "48000",
                "-movflags",
                "+faststart",
                "-shortest",
                str(staged_mp4),
            ]
        )

        staged_probe = _probe(staged_mp4)
        staged_video = _stream(staged_probe, "video")
        _stream(staged_probe, "audio")
        staged_duration = _duration(staged_probe)
        expected_duration = opening_duration + program_duration
        duration_tolerance = max(0.15, 2.0 / float(fps))
        if abs(staged_duration - expected_duration) > duration_tolerance:
            raise RuntimeError(
                "Integrated Day 2 duration mismatch: "
                f"expected={expected_duration:.3f}, actual={staged_duration:.3f}"
            )
        if (
            int(staged_video.get("width") or 0) != width
            or int(staged_video.get("height") or 0) != height
        ):
            raise RuntimeError("Day 2 media integration changed the program resolution")

        _run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-i",
                str(staged_mp4),
                "-map",
                "0:a:0",
                "-vn",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "192k",
                str(staged_mp3),
            ]
        )
        os.replace(staged_mp4, final_mp4)
        os.replace(staged_mp3, final_mp3)
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)

    final_probe = _probe(final_mp4)
    final_duration = _duration(final_probe)
    final_video = _stream(final_probe, "video")
    _stream(final_probe, "audio")

    background_end = opening_duration + program_duration - OUTRO_SECONDS
    report: dict[str, Any] = {
        "version": "FPL-VORTEX-DAY2-EXTERNAL-MEDIA-V1",
        "passed": True,
        "isolation": {
            "day": 2,
            "output_root": str(output_root),
            "day1_output_written": False,
        },
        "program": {
            "file": str(program),
            "sha256": _sha256(program),
            "duration": program_duration,
        },
        "opening": {
            "name": opening.name,
            "sha256": _sha256(opening),
            "duration": opening_duration,
            "original_audio_preserved": True,
            "timeline_start": 0.0,
            "timeline_end": opening_duration,
        },
        "background_music": {
            "name": background.name,
            "sha256": _sha256(background),
            "source_duration": background_source_duration,
            "looped_as_needed": True,
            "timeline_anchor": "slide_00_intro_start",
            "timeline_start": opening_duration,
            "timeline_end": background_end,
        },
        "outro_music": {
            "name": outro.name,
            "sha256": _sha256(outro),
            "source_duration": outro_source_duration,
            "excerpt_start": 0.0,
            "excerpt_duration": OUTRO_SECONDS,
            "timeline_start": background_end,
            "timeline_end": final_duration,
        },
        "mix": {
            "narration_level": NARRATION_LEVEL,
            "music_level": MUSIC_LEVEL,
            "music_gain": MUSIC_GAIN,
        },
        "visual_contract": {
            "canonical_vortex_logo_and_lion_head": "owned by Day 2 notebook scene assets",
            "subscribe_cta": "owned by Day 2 notebook scene_07_outro",
        },
        "final": {
            "file": str(final_mp4),
            "audio_file": str(final_mp3),
            "sha256": _sha256(final_mp4),
            "duration": final_duration,
            "resolution": [
                int(final_video.get("width") or 0),
                int(final_video.get("height") or 0),
            ],
            "fps": float(_fps(final_video)),
        },
    }
    write_json_atomic(report_path, report)

    print("[DAY 2 MEDIA] PASS")
    print(f"[DAY 2 MEDIA] Opening: {opening.name} ({opening_duration:.3f}s)")
    print(
        "[DAY 2 MEDIA] Background starts at slide_00 after the opening: "
        f"narration {NARRATION_LEVEL} / music {MUSIC_LEVEL} "
        f"(gain {MUSIC_GAIN:.8f})"
    )
    print(f"[DAY 2 MEDIA] Outro music: final {OUTRO_SECONDS:.3f}s only")
    print(f"[DAY 2 MEDIA] Final private-review video: {final_mp4}")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Apply the exact Day 1 opening/background/outro timing contract "
            "to the isolated Day 2 master"
        )
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path(os.environ.get("DAY2_OUTPUT_ROOT", DEFAULT_OUTPUT_ROOT)),
    )
    parser.add_argument("--asset-dir", type=Path, required=True)
    args = parser.parse_args()
    integrate_media(args.output_root, args.asset_dir)


if __name__ == "__main__":
    main()
