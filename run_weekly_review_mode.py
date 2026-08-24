from __future__ import annotations

import os
from pathlib import Path

from google.oauth2.credentials import Credentials

import run_weekly_review_cloud as cloud


# -----------------------------------------------------------------------------
# GitHub runner compatibility
# -----------------------------------------------------------------------------
# Colab owns /content, but a normal GitHub-hosted runner does not. Keep the
# notebook unchanged and transparently map its Colab Drive paths into a writable
# runner temp directory. Nothing about the model, slides, timing or narration is
# changed here.
RUNNER_TEMP = Path(os.environ.get("RUNNER_TEMP", ".cloud_runtime"))
RUNTIME_CONTENT = RUNNER_TEMP / "fpl_vortex_content"

cloud.LOCAL_CONTENT = RUNTIME_CONTENT
cloud.LOCAL_MYDRIVE = RUNTIME_CONTENT / "drive" / "MyDrive"
cloud.LOCAL_PROJECT = cloud.LOCAL_MYDRIVE / "FPL_VORTEX"
cloud.LOCAL_DATA = cloud.LOCAL_MYDRIVE / "FPL_VORTEX_DATA"
cloud.LOCAL_ELEMENTS = cloud.LOCAL_MYDRIVE / "elements"


def _clean_secret(name: str) -> str:
    value = os.environ.get(name, "").strip()
    # Be tolerant of values pasted into GitHub Secrets with surrounding quotes.
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1].strip()
    return value


def _github_drive_credentials() -> Credentials:
    client_id = _clean_secret("GOOGLE_OAUTH_CLIENT_ID")
    client_secret = _clean_secret("GOOGLE_OAUTH_CLIENT_SECRET")
    refresh_token = _clean_secret("GOOGLE_OAUTH_REFRESH_TOKEN")
    missing = [
        name
        for name, value in (
            ("GOOGLE_OAUTH_CLIENT_ID", client_id),
            ("GOOGLE_OAUTH_CLIENT_SECRET", client_secret),
            ("GOOGLE_OAUTH_REFRESH_TOKEN", refresh_token),
        )
        if not value
    ]
    if missing:
        raise RuntimeError("Missing GitHub secret(s): " + ", ".join(missing))
    return Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=cloud.TOKEN_URI,
        client_id=client_id,
        client_secret=client_secret,
        scopes=cloud.SCOPES,
    )


# Replace the base runner's credentials factory with the sanitized variant.
cloud.credentials = _github_drive_credentials


# -----------------------------------------------------------------------------
# Render profiles
# -----------------------------------------------------------------------------
# The notebook remains the single source of truth for content, data, narration,
# slides, timing and animations. Draft and Final execute the same notebook and
# the same selected scenes; only output dimensions and frame rate differ.
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


def _rewrite_colab_paths(source: str) -> str:
    """Map only Colab filesystem literals; leave notebook logic untouched."""
    source = source.replace(
        "/content/drive/MyDrive",
        str(cloud.LOCAL_MYDRIVE),
    )
    source = source.replace(
        "/content/drive",
        str(cloud.LOCAL_CONTENT / "drive"),
    )
    return source


def run_cell_with_render_profile(source: str, filename: str, namespace: dict) -> None:
    """Execute notebook normally while locking only its render profile."""
    global _profile_locked

    source = _rewrite_colab_paths(source)

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


# -----------------------------------------------------------------------------
# Entrypoint
# -----------------------------------------------------------------------------
def main() -> int:
    cloud.log(
        f"[CLOUD] Requested render profile: {MODE} | "
        f"{PROFILE['width']}x{PROFILE['height']} @ {PROFILE['fps']}fps"
    )
    cloud.log(f"[CLOUD] Writable runtime root: {cloud.LOCAL_CONTENT}")
    return cloud.main()


if __name__ == "__main__":
    raise SystemExit(main())
