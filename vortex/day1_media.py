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


DEFAULT_OUTPUT_ROOT = Path("/content/drive/MyDrive/FPL_VORTEX/FIRST VIDEO")
DEFAULT_ASSET_DIR = Path("/content/drive/MyDrive/FPL_VORTEX/Assets")

OPENING_NAME = "opening.mp4"
BACKGROUND_NAME = "Background_music.mp3"
OUTRO_NAME = "outro.mp3"

NARRATION_LEVEL = 255
MUSIC_LEVEL = 12
MUSIC_GAIN = MUSIC_LEVEL / NARRATION_LEVEL
OUTRO_SECONDS = 6.0


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
    result = _run(
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
    )
    return json.loads(result.stdout)


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


def _single_file(folder: Path, pattern: str, label: str) -> Path:
    matches = sorted(path for path in folder.glob(pattern) if path.is_file())
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected exactly one {label} matching {pattern!r} in {folder}; "
            f"found {len(matches)}"
        )
    return matches[0]


def _qa_output_file(
    qa: dict[str, Any],
    key: str,
    folder: Path,
    pattern: str,
    label: str,
) -> Path:
    configured = str(qa.get(key) or "").strip()
    if not configured:
        return _single_file(folder, pattern, label)

    candidate = Path(configured)
    if not candidate.is_absolute():
        candidate = folder / candidate.name
    candidate = candidate.resolve()
    if candidate.parent != folder.resolve():
        raise RuntimeError(
            f"Final QA points outside the expected {label} folder: {candidate}"
        )
    if not candidate.match(pattern):
        raise RuntimeError(
            f"Final QA {key!r} does not match {pattern!r}: {candidate.name}"
        )
    return _required_file(candidate)


def _remove_stale_combined_files(folder: Path, pattern: str, keep: Path) -> None:
    stale = sorted(
        path
        for path in folder.glob(pattern)
        if path.is_file() and path.resolve() != keep.resolve()
    )
    for path in stale:
        path.unlink()
        print(f"[DAY 1 MEDIA] Removed stale combined output: {path.name}")


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _required_file(path: Path) -> Path:
    if not path.is_file() or path.stat().st_size <= 0:
        raise FileNotFoundError(f"Required Day 1 asset is missing or empty: {path}")
    return path


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
            f"Program must be longer than {OUTRO_SECONDS:g} seconds"
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


def integrate_media(output_root: Path, asset_dir: Path) -> dict[str, Any]:
    output_root = output_root.resolve()
    asset_dir = asset_dir.resolve()

    mp4_dir = output_root / "MP4"
    mp3_dir = output_root / "MP3"
    data_dir = output_root / "DATA"
    for folder in (mp4_dir, mp3_dir, data_dir):
        if not folder.is_dir():
            raise FileNotFoundError(f"Required output directory is missing: {folder}")

    qa_path = data_dir / "final_video_qa.json"
    if not qa_path.is_file():
        raise FileNotFoundError(f"Final video QA is missing: {qa_path}")

    existing_qa = json.loads(qa_path.read_text(encoding="utf-8"))
    final_mp4 = _qa_output_file(
        existing_qa,
        "final_file",
        mp4_dir,
        "*COMBINED*.mp4",
        "combined MP4",
    )
    combined_mp3 = _qa_output_file(
        existing_qa,
        "combined_mp3",
        mp3_dir,
        "*COMBINED*.mp3",
        "combined MP3",
    )
    existing_integration = existing_qa.get("external_media_integration", {})
    if existing_integration.get("applied") is True:
        expected_hash = str(existing_integration.get("final_sha256") or "")
        if expected_hash and _sha256(final_mp4) == expected_hash:
            _remove_stale_combined_files(
                mp4_dir, "*COMBINED*.mp4", final_mp4
            )
            _remove_stale_combined_files(
                mp3_dir, "*COMBINED*.mp3", combined_mp3
            )
            print(
                "[DAY 1 MEDIA] Opening and music are already integrated; "
                "reusing output."
            )
            return existing_integration
        raise RuntimeError(
            "Day 1 QA says media was integrated, but the final MP4 hash changed"
        )

    opening = _required_file(asset_dir / OPENING_NAME)
    background = _required_file(asset_dir / BACKGROUND_NAME)
    outro = _required_file(asset_dir / OUTRO_NAME)

    opening_probe = _probe(opening)
    program_probe = _probe(final_mp4)
    background_probe = _probe(background)
    outro_probe = _probe(outro)

    opening_video = _stream(opening_probe, "video")
    _stream(opening_probe, "audio")
    program_video = _stream(program_probe, "video")
    _stream(program_probe, "audio")
    _stream(background_probe, "audio")
    _stream(outro_probe, "audio")

    opening_duration = _duration(opening_probe)
    program_duration = _duration(program_probe)
    background_source_duration = _duration(background_probe)
    outro_source_duration = _duration(outro_probe)

    if opening_duration <= 0.5:
        raise RuntimeError("opening.mp4 is too short")
    if program_duration <= OUTRO_SECONDS + 0.5:
        raise RuntimeError("The rendered program is too short for a six-second outro")
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
        raise RuntimeError("The final program resolution is invalid")

    original_program_sha256 = _sha256(final_mp4)
    graph = _filter_graph(
        width=width,
        height=height,
        fps=fps,
        opening_duration=opening_duration,
        program_duration=program_duration,
    )

    staging_dir = Path(
        tempfile.mkdtemp(prefix=".day1-media-", dir=str(mp4_dir))
    )
    staged_mp4 = staging_dir / final_mp4.name
    staged_mp3 = staging_dir / combined_mp3.name
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
                str(final_mp4),
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
                "Integrated video duration mismatch: "
                f"expected={expected_duration:.3f}, actual={staged_duration:.3f}"
            )
        if (
            int(staged_video.get("width") or 0) != width
            or int(staged_video.get("height") or 0) != height
        ):
            raise RuntimeError("Integrated video resolution changed unexpectedly")

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
        staged_mp3_probe = _probe(staged_mp3)
        _stream(staged_mp3_probe, "audio")
        staged_mp3_duration = _duration(staged_mp3_probe)
        if abs(staged_mp3_duration - staged_duration) > 0.20:
            raise RuntimeError("Integrated MP3 duration does not match the final video")

        os.replace(staged_mp4, final_mp4)
        os.replace(staged_mp3, combined_mp3)
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)

    final_probe = _probe(final_mp4)
    final_duration = _duration(final_probe)
    combined_mp3_duration = _duration(_probe(combined_mp3))
    final_sha256 = _sha256(final_mp4)

    background_end = opening_duration + program_duration - OUTRO_SECONDS
    report: dict[str, Any] = {
        "applied": True,
        "version": "DAY1-EXTERNAL-MEDIA-V1",
        "asset_folder_id": os.environ.get("DAY1_ASSET_FOLDER_ID"),
        "logo_used": False,
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
        "original_program_duration": program_duration,
        "original_program_sha256": original_program_sha256,
        "final_duration": final_duration,
        "final_sha256": final_sha256,
        "combined_mp3_duration": combined_mp3_duration,
        "resolution": [width, height],
        "fps": float(fps),
        "final_file": str(final_mp4),
        "combined_mp3": str(combined_mp3),
    }

    scene_order = list(existing_qa.get("scene_order") or [])
    scene_keys = list(existing_qa.get("scene_keys") or [])
    if not scene_keys or scene_keys[0] != "opening_video":
        scene_keys.insert(0, "opening_video")
        scene_order.insert(0, "OPENING VIDEO")

    existing_qa.update(
        {
            "passed": True,
            "scene_order": scene_order,
            "scene_keys": scene_keys,
            "scene_count": len(scene_keys),
            "expected_duration": final_duration,
            "final_duration": final_duration,
            "duration_delta": 0.0,
            "final_file": str(final_mp4),
            "combined_mp3": str(combined_mp3),
            "combined_mp3_duration": combined_mp3_duration,
            "external_media_integration": report,
        }
    )
    assembly_mode = str(existing_qa.get("assembly_mode") or "").strip()
    if "DAY1_EXTERNAL_MEDIA" not in assembly_mode:
        existing_qa["assembly_mode"] = (
            f"{assembly_mode}+DAY1_EXTERNAL_MEDIA"
            if assembly_mode
            else "DAY1_EXTERNAL_MEDIA"
        )

    _write_json_atomic(data_dir / "day1_media_qa.json", report)
    _write_json_atomic(qa_path, existing_qa)
    _remove_stale_combined_files(mp4_dir, "*COMBINED*.mp4", final_mp4)
    _remove_stale_combined_files(mp3_dir, "*COMBINED*.mp3", combined_mp3)

    print("[DAY 1 MEDIA] PASS")
    print(f"[DAY 1 MEDIA] Opening: {opening.name} ({opening_duration:.3f}s)")
    print(
        "[DAY 1 MEDIA] Background: "
        f"narration {NARRATION_LEVEL} / music {MUSIC_LEVEL} "
        f"(gain {MUSIC_GAIN:.8f})"
    )
    print(f"[DAY 1 MEDIA] Outro music: final {OUTRO_SECONDS:.3f}s only")
    print(f"[DAY 1 MEDIA] Final video: {final_mp4}")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepend the Day 1 opening and mix the confirmed Drive music assets"
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path(os.environ.get("DAY1_OUTPUT_ROOT", DEFAULT_OUTPUT_ROOT)),
    )
    parser.add_argument(
        "--asset-dir",
        type=Path,
        default=Path(os.environ.get("DAY1_ASSET_DIR", DEFAULT_ASSET_DIR)),
    )
    args = parser.parse_args()
    integrate_media(args.output_root, args.asset_dir)


if __name__ == "__main__":
    main()
