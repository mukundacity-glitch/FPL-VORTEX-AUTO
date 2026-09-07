from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
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

# Day 2 voice-first loudness/ducking contract.
# Keep the shared opening/outro timing, but do not reuse Day 1's static 0-255 mix.
NARRATION_TARGET_LUFS = -18.0
NARRATION_TARGET_TRUE_PEAK_DBTP = -1.5
MUSIC_BASE_TARGET_LUFS = -27.5
MUSIC_TARGET_TRUE_PEAK_DBTP = -3.0
MUSIC_SPEECH_NOMINAL_TARGET_LUFS = -33.0
OUTRO_TARGET_LUFS = -26.0
OUTRO_TARGET_TRUE_PEAK_DBTP = -3.0

DUCK_NOMINAL_DB = 5.5
DUCK_THRESHOLD_LINEAR = 0.055
DUCK_RATIO = 4.0
DUCK_ATTACK_MS = 30.0
DUCK_RELEASE_MS = 350.0
DUCK_KNEE = 3.0

PRESENCE_DIP_HZ = 2500.0
PRESENCE_DIP_Q = 0.8
PRESENCE_DIP_DB = -2.0

LOUDNESS_LRA_TARGET = 7.0
FINAL_TRUE_PEAK_CEILING_DBTP = -1.0
TRUE_PEAK_REPAIR_TARGET_DBTP = -1.3
PREENCODE_LIMIT_LINEAR = 0.80
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


def _measurement_value(payload: dict[str, Any], key: str, label: str) -> float:
    try:
        value = float(payload[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(f"{label} loudness measurement is missing {key}") from exc
    if not math.isfinite(value):
        raise RuntimeError(f"{label} loudness measurement {key} is not finite")
    return value


def _measure_loudness(
    path: Path,
    label: str,
    *,
    prefilter: str = "",
    target_lufs: float = NARRATION_TARGET_LUFS,
    target_lra: float = LOUDNESS_LRA_TARGET,
    target_true_peak_dbtp: float = FINAL_TRUE_PEAK_CEILING_DBTP,
) -> dict[str, float]:
    """Measure one stable audio stream with target-consistent FFmpeg loudnorm."""
    analysis = (
        f"loudnorm=I={target_lufs:.2f}:LRA={target_lra:.2f}:"
        f"TP={target_true_peak_dbtp:.2f}:print_format=json"
    )
    if prefilter:
        analysis = f"{prefilter},{analysis}"
    result = _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-af",
            analysis,
            "-f",
            "null",
            "-",
        ]
    )
    candidates = re.findall(r'\{\s*"input_i"[\s\S]*?\}', result.stderr)
    if not candidates:
        raise RuntimeError(f"Could not parse {label} loudness measurement")
    if len(candidates) != 1:
        raise RuntimeError(
            f"{label} changed audio format during loudness measurement "
            f"({len(candidates)} analyzer segments); stabilize it before measuring"
        )
    try:
        payload = json.loads(candidates[-1])
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Could not decode {label} loudness measurement") from exc
    return {
        "integrated_lufs": _measurement_value(payload, "input_i", label),
        "true_peak_dbtp": _measurement_value(payload, "input_tp", label),
        "lra_lu": _measurement_value(payload, "input_lra", label),
        "threshold_lufs": _measurement_value(payload, "input_thresh", label),
        "target_offset_db": _measurement_value(payload, "target_offset", label),
    }


def _loudnorm_filter(
    measurement: dict[str, float],
    *,
    target_lufs: float,
    target_true_peak_dbtp: float,
) -> str:
    """Build a measured second-pass EBU R128 loudnorm filter."""
    return (
        f"loudnorm=I={target_lufs:.2f}:LRA={LOUDNESS_LRA_TARGET:.2f}:"
        f"TP={target_true_peak_dbtp:.2f}:"
        f"measured_I={measurement['integrated_lufs']:.2f}:"
        f"measured_LRA={measurement['lra_lu']:.2f}:"
        f"measured_TP={measurement['true_peak_dbtp']:.2f}:"
        f"measured_thresh={measurement['threshold_lufs']:.2f}:"
        f"offset={measurement['target_offset_db']:.2f}:"
        "linear=true:print_format=none"
    )


def _assert_loudness_target(
    *,
    actual: float,
    target: float,
    tolerance: float,
    label: str,
) -> None:
    if abs(actual - target) > tolerance:
        raise RuntimeError(
            f"{label} loudness missed target: "
            f"target={target:.2f} LUFS actual={actual:.2f} LUFS"
        )


def _integrate_media_stream_copy(
    output_root: Path,
    asset_dir: Path,
) -> dict[str, Any]:
    """Apply Day 2 voice-anchored ducking while stream-copying the 4K program video."""
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

    prep_root = Path(
        os.environ.get("RUNNER_TEMP", tempfile.gettempdir())
    ) / "day2-audio-prep"
    shutil.rmtree(prep_root, ignore_errors=True)
    prep_root.mkdir(parents=True, exist_ok=True)
    narration_pcm = prep_root / "day2_narration_stereo_48k.wav"
    _run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-i", str(program),
            "-map", "0:a:0",
            "-vn",
            "-ar", "48000",
            "-ac", "2",
            "-c:a", "pcm_s24le",
            str(narration_pcm),
        ]
    )
    narration_pcm_probe = _probe(narration_pcm)
    _stream(narration_pcm_probe, "audio")
    narration_pcm_duration = _duration(narration_pcm_probe)
    if abs(narration_pcm_duration - program_duration) > 0.50:
        raise RuntimeError(
            "Day 2 stabilized narration duration mismatch: "
            f"program={program_duration:.3f}s narration={narration_pcm_duration:.3f}s"
        )

    narration_input_loudness = _measure_loudness(
        narration_pcm,
        "Day 2 narration",
        target_lufs=NARRATION_TARGET_LUFS,
        target_lra=LOUDNESS_LRA_TARGET,
        target_true_peak_dbtp=NARRATION_TARGET_TRUE_PEAK_DBTP,
    )
    music_input_loudness = _measure_loudness(
        background,
        "Day 2 background music",
        target_lufs=MUSIC_BASE_TARGET_LUFS,
        target_lra=LOUDNESS_LRA_TARGET,
        target_true_peak_dbtp=MUSIC_TARGET_TRUE_PEAK_DBTP,
    )
    outro_input_loudness = _measure_loudness(
        outro,
        "Day 2 outro music",
        target_lufs=OUTRO_TARGET_LUFS,
        target_lra=LOUDNESS_LRA_TARGET,
        target_true_peak_dbtp=OUTRO_TARGET_TRUE_PEAK_DBTP,
    )

    narration_loudnorm = _loudnorm_filter(
        narration_input_loudness,
        target_lufs=NARRATION_TARGET_LUFS,
        target_true_peak_dbtp=NARRATION_TARGET_TRUE_PEAK_DBTP,
    )
    music_loudnorm = _loudnorm_filter(
        music_input_loudness,
        target_lufs=MUSIC_BASE_TARGET_LUFS,
        target_true_peak_dbtp=MUSIC_TARGET_TRUE_PEAK_DBTP,
    )
    outro_loudnorm = _loudnorm_filter(
        outro_input_loudness,
        target_lufs=OUTRO_TARGET_LUFS,
        target_true_peak_dbtp=OUTRO_TARGET_TRUE_PEAK_DBTP,
    )

    presence_filter = (
        f"equalizer=f={PRESENCE_DIP_HZ:.1f}:t=q:w={PRESENCE_DIP_Q:.2f}:"
        f"g={PRESENCE_DIP_DB:.2f}"
    )
    narration_post_loudness = _measure_loudness(
        narration_pcm,
        "normalized Day 2 narration",
        prefilter=f"{narration_loudnorm},aresample=48000",
        target_lufs=NARRATION_TARGET_LUFS,
        target_lra=LOUDNESS_LRA_TARGET,
        target_true_peak_dbtp=NARRATION_TARGET_TRUE_PEAK_DBTP,
    )
    music_post_loudness = _measure_loudness(
        background,
        "normalized Day 2 background music",
        prefilter=f"{music_loudnorm},aresample=48000,{presence_filter}",
        target_lufs=MUSIC_BASE_TARGET_LUFS,
        target_lra=LOUDNESS_LRA_TARGET,
        target_true_peak_dbtp=MUSIC_TARGET_TRUE_PEAK_DBTP,
    )
    _assert_loudness_target(
        actual=narration_post_loudness["integrated_lufs"],
        target=NARRATION_TARGET_LUFS,
        tolerance=0.8,
        label="Day 2 narration",
    )
    _assert_loudness_target(
        actual=music_post_loudness["integrated_lufs"],
        target=MUSIC_BASE_TARGET_LUFS,
        tolerance=1.5,
        label="Day 2 background music",
    )

    background_duration = program_duration - OUTRO_SECONDS
    background_fade = min(0.25, background_duration / 2.0)
    background_fade_start = max(0.0, background_duration - background_fade)
    outro_delay_ms = int(round(background_duration * 1000.0))

    audio_format = "aformat=sample_fmts=fltp:channel_layouts=stereo"
    duck_filter = (
        f"sidechaincompress=threshold={DUCK_THRESHOLD_LINEAR:.6f}:"
        f"ratio={DUCK_RATIO:.2f}:attack={DUCK_ATTACK_MS:.1f}:"
        f"release={DUCK_RELEASE_MS:.1f}:knee={DUCK_KNEE:.2f}:"
        "link=maximum:detection=rms:makeup=1:mix=1"
    )
    audio_graph = ";".join(
        (
            f"[1:a]apad=pad_dur={program_duration:.6f},"
            f"atrim=duration={program_duration:.6f},"
            "asetpts=PTS-STARTPTS,"
            f"{narration_loudnorm},aresample=48000,{audio_format},"
            "asplit=3[narration_mix][narration_sc_bg][narration_sc_outro]",
            f"[2:a]atrim=duration={background_duration:.6f},"
            "asetpts=PTS-STARTPTS,"
            f"{music_loudnorm},aresample=48000,{presence_filter},{audio_format},"
            f"afade=t=out:st={background_fade_start:.6f}:"
            f"d={background_fade:.6f}[music_base]",
            f"[music_base][narration_sc_bg]{duck_filter}[background]",
            f"[3:a]atrim=duration={OUTRO_SECONDS:.6f},"
            "asetpts=PTS-STARTPTS,"
            f"{outro_loudnorm},aresample=48000,{audio_format},"
            "afade=t=in:st=0:d=0.250000,"
            f"afade=t=out:st={OUTRO_SECONDS - 0.5:.6f}:d=0.500000,"
            f"adelay={outro_delay_ms}|{outro_delay_ms}[outro_base]",
            f"[outro_base][narration_sc_outro]{duck_filter}[outro_music]",
            "[narration_mix][background][outro_music]"
            "amix=inputs=3:duration=first:dropout_transition=0:normalize=0,"
            f"alimiter=limit={PREENCODE_LIMIT_LINEAR:.6f}:level=0[program_audio]",
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
                "-af", (
                    "aresample=48000,"
                    "aformat=channel_layouts=stereo,"
                    f"alimiter=limit={PREENCODE_LIMIT_LINEAR:.6f}:level=0"
                ),
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
                "-i", str(narration_pcm),
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

        peak_repair_db = 0.0
        staged_loudness = _measure_loudness(staged_mp4, "encoded Day 2 master")
        if staged_loudness["true_peak_dbtp"] > FINAL_TRUE_PEAK_CEILING_DBTP:
            peak_repair_db = (
                TRUE_PEAK_REPAIR_TARGET_DBTP
                - staged_loudness["true_peak_dbtp"]
            )
            peak_safe_mp4 = staging_dir / "day2-peak-safe.mp4"
            _run(
                [
                    "ffmpeg", "-y", "-v", "error",
                    "-i", str(staged_mp4),
                    "-map", "0:v:0",
                    "-map", "0:a:0",
                    "-c:v", "copy",
                    "-af",
                    (
                        f"volume={peak_repair_db:.3f}dB,"
                        f"alimiter=limit={PREENCODE_LIMIT_LINEAR:.6f}:level=0"
                    ),
                    "-c:a", "aac",
                    "-b:a", "192k",
                    "-ar", "48000",
                    "-ac", "2",
                    "-movflags", "+faststart",
                    str(peak_safe_mp4),
                ]
            )
            os.replace(peak_safe_mp4, staged_mp4)
            staged_loudness = _measure_loudness(
                staged_mp4, "peak-safe encoded Day 2 master"
            )
        if staged_loudness["true_peak_dbtp"] > FINAL_TRUE_PEAK_CEILING_DBTP:
            raise RuntimeError(
                "Day 2 final true peak exceeds safety ceiling: "
                f"{staged_loudness['true_peak_dbtp']:.2f} dBTP"
            )

        _run(
            [
                "ffmpeg", "-y", "-v", "error",
                "-i", str(staged_mp4),
                "-map", "0:a:0",
                "-vn",
                "-af",
                (
                    "asetpts=N/SR/TB,apad,"
                    f"atrim=duration={staged_duration:.6f}"
                ),
                "-c:a", "libmp3lame",
                "-b:a", "192k",
                str(staged_mp3),
            ]
        )
        staged_mp3_probe = _probe(staged_mp3)
        _stream(staged_mp3_probe, "audio")
        mp3_duration = _duration(staged_mp3_probe)
        if mp3_duration <= 0:
            raise RuntimeError("Integrated Day 2 MP3 is empty")
        if abs(mp3_duration - staged_duration) > 0.20:
            raise RuntimeError(
                "Integrated Day 2 MP3 duration does not match the final video: "
                f"video={staged_duration:.3f}s mp3={mp3_duration:.3f}s"
            )

        os.replace(staged_mp4, final_mp4)
        os.replace(staged_mp3, final_mp3)
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)

    final_probe = _probe(final_mp4)
    final_duration = _duration(final_probe)
    final_video = _stream(final_probe, "video")
    _stream(final_probe, "audio")
    final_loudness = _measure_loudness(final_mp4, "final Day 2 master")
    if final_loudness["true_peak_dbtp"] > FINAL_TRUE_PEAK_CEILING_DBTP:
        raise RuntimeError(
            "Day 2 final encoded true peak exceeds safety ceiling: "
            f"{final_loudness['true_peak_dbtp']:.2f} dBTP"
        )

    background_end = opening_duration + program_duration - OUTRO_SECONDS
    report: dict[str, Any] = {
        "version": "FPL-VORTEX-DAY2-VOICE-DUCK-MIX-V5",
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
            "narration_loudness": {
                "input_lufs": narration_input_loudness["integrated_lufs"],
                "input_true_peak_dbtp": narration_input_loudness["true_peak_dbtp"],
                "target_lufs": NARRATION_TARGET_LUFS,
                "normalized_lufs": narration_post_loudness["integrated_lufs"],
                "target_true_peak_dbtp": NARRATION_TARGET_TRUE_PEAK_DBTP,
                "stabilized_to_stereo_48k": True,
            },
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
            "loudness": {
                "input_lufs": music_input_loudness["integrated_lufs"],
                "input_true_peak_dbtp": music_input_loudness["true_peak_dbtp"],
                "base_target_lufs": MUSIC_BASE_TARGET_LUFS,
                "normalized_lufs": music_post_loudness["integrated_lufs"],
                "nominal_while_speaking_lufs": MUSIC_SPEECH_NOMINAL_TARGET_LUFS,
            },
        },
        "outro_music": {
            "name": outro.name,
            "sha256": _sha256(outro),
            "source_duration": outro_source_duration,
            "excerpt_start": 0.0,
            "excerpt_duration": OUTRO_SECONDS,
            "timeline_start": background_end,
            "timeline_end": final_duration,
            "target_lufs": OUTRO_TARGET_LUFS,
            "ducked_by_narration": True,
        },
        "mix": {
            "mode": "voice_anchored_dynamic_ducking",
            "narration_target_lufs": NARRATION_TARGET_LUFS,
            "music_base_target_lufs": MUSIC_BASE_TARGET_LUFS,
            "music_nominal_speech_target_lufs": MUSIC_SPEECH_NOMINAL_TARGET_LUFS,
            "nominal_duck_db": DUCK_NOMINAL_DB,
            "threshold_linear": DUCK_THRESHOLD_LINEAR,
            "ratio": DUCK_RATIO,
            "attack_ms": DUCK_ATTACK_MS,
            "release_ms": DUCK_RELEASE_MS,
            "knee": DUCK_KNEE,
            "detection": "rms",
            "link": "maximum",
            "presence_dip_hz": PRESENCE_DIP_HZ,
            "presence_dip_q": PRESENCE_DIP_Q,
            "presence_dip_db": PRESENCE_DIP_DB,
            "final_true_peak_ceiling_dbtp": FINAL_TRUE_PEAK_CEILING_DBTP,
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
            "loudness": {
                "integrated_lufs": final_loudness["integrated_lufs"],
                "true_peak_dbtp": final_loudness["true_peak_dbtp"],
                "lra_lu": final_loudness["lra_lu"],
                "peak_repair_db": peak_repair_db,
            },
        },
    }
    write_json_atomic(report_path, report)

    print("[DAY 2 MEDIA] PASS — voice-anchored dynamic ducking + 4K stream copy")
    print(
        f"[DAY 2 MEDIA] Ryan: {narration_post_loudness['integrated_lufs']:.2f} LUFS "
        f"(target {NARRATION_TARGET_LUFS:.1f}; stabilized stereo 48 kHz)"
    )
    print(
        f"[DAY 2 MEDIA] Music base: {music_post_loudness['integrated_lufs']:.2f} LUFS; "
        f"nominal speech bed {MUSIC_SPEECH_NOMINAL_TARGET_LUFS:.1f} LUFS"
    )
    print(
        f"[DAY 2 MEDIA] Duck: ~{DUCK_NOMINAL_DB:.1f} dB, "
        f"attack {DUCK_ATTACK_MS:.0f} ms, release {DUCK_RELEASE_MS:.0f} ms"
    )
    print(
        f"[DAY 2 MEDIA] Final true peak: {final_loudness['true_peak_dbtp']:.2f} dBTP "
        f"(ceiling {FINAL_TRUE_PEAK_CEILING_DBTP:.1f} dBTP)"
    )
    print(f"[DAY 2 MEDIA] Outro music: final {OUTRO_SECONDS:.3f}s only")
    print(f"[DAY 2 MEDIA] Final private-review video: {final_mp4}")
    shutil.rmtree(prep_root, ignore_errors=True)
    return report


def integrate_media(output_root: Path, asset_dir: Path) -> dict[str, Any]:
    return _integrate_media_stream_copy(output_root, asset_dir)

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Apply Day 2 voice-first normalization and dynamic music ducking "
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
