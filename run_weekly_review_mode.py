from __future__ import annotations

import os

import run_weekly_review_cloud as cloud


# The notebook remains the single source of truth for content, data, narration,
# slides, timing and animations. This wrapper changes ONLY the render profile.
# Draft and Final therefore execute the same notebook and the same selected
# scenes; only output dimensions and frame rate differ.
PROFILES = {
    "DRAFT": {"width": 854, "height": 480, "fps": 5},
    "FINAL": {"width": 1920, "height": 1080, "fps": 30},
}

MODE = os.environ.get("VORTEX_RENDER_MODE", "DRAFT").strip().upper()
if MODE not in PROFILES:
    raise RuntimeError(
        f"Invalid VORTEX_RENDER_MODE={MODE!r}. Choose DRAFT or FINAL."
    )

PROFILE = PROFILES[MODE]
_original_run_cell = cloud.run_cell
_profile_locked = False


def _lock_profile(namespace: dict) -> None:
    namespace["MP4_QUALITY"] = MODE
    namespace["VIDEO_WIDTH"] = PROFILE["width"]
    namespace["VIDEO_HEIGHT"] = PROFILE["height"]
    namespace["VIDEO_FPS"] = PROFILE["fps"]


def run_cell_with_render_profile(source: str, filename: str, namespace: dict) -> None:
    """Execute notebook normally while locking only its render profile."""
    global _profile_locked

    # Once Cell 0 has been seen, re-lock before every later cell so no helper
    # can accidentally drift away from the requested Draft/Final profile.
    if _profile_locked:
        _lock_profile(namespace)

    _original_run_cell(source, filename, namespace)

    # Cell 0 owns MP4_QUALITY / VIDEO_WIDTH / VIDEO_HEIGHT / VIDEO_FPS in the
    # saved notebook. Override those four values immediately after that cell.
    if (
        "MP4_QUALITY" in source
        and "VIDEO_WIDTH" in source
        and "VIDEO_HEIGHT" in source
        and "VIDEO_FPS" in source
    ):
        _profile_locked = True
        _lock_profile(namespace)
        cloud.log(
            f"[CLOUD] Render mode locked: {MODE} | "
            f"{PROFILE['width']}x{PROFILE['height']} @ {PROFILE['fps']}fps"
        )


cloud.run_cell = run_cell_with_render_profile

# Include render mode in the cloud report without changing notebook logic.
_original_main = cloud.main


def main() -> int:
    cloud.log(
        f"[CLOUD] Requested render profile: {MODE} | "
        f"{PROFILE['width']}x{PROFILE['height']} @ {PROFILE['fps']}fps"
    )
    return _original_main()


if __name__ == "__main__":
    raise SystemExit(main())
