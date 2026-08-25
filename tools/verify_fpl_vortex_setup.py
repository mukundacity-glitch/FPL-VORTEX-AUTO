from __future__ import annotations

import os
import sys
from pathlib import Path

from google.auth.exceptions import RefreshError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vortex.drive_sync import MY_DRIVE_ROOT_ID, _resolve_folder, _service

REQUIRED_GOOGLE_SECRETS = (
    "GOOGLE_OAUTH_CLIENT_ID",
    "GOOGLE_OAUTH_CLIENT_SECRET",
    "GOOGLE_OAUTH_REFRESH_TOKEN",
)

missing = []
for name in REQUIRED_GOOGLE_SECRETS:
    value = (os.environ.get(name) or "").strip()
    if not value:
        missing.append(name)
    else:
        os.environ[name] = value

if missing:
    raise RuntimeError("Missing GitHub secret(s): " + ", ".join(missing))

if not os.environ["GOOGLE_OAUTH_CLIENT_ID"].endswith(".apps.googleusercontent.com"):
    raise RuntimeError(
        "GOOGLE_OAUTH_CLIENT_ID does not look like a Google OAuth client ID. "
        "Use the active OAuth client ID that issued GOOGLE_OAUTH_REFRESH_TOKEN."
    )

try:
    service = _service()
    service.files().get(fileId=MY_DRIVE_ROOT_ID, fields="id").execute()
except RefreshError as exc:
    text = str(exc).lower()
    if "invalid_client" in text or "oauth client was not found" in text:
        raise RuntimeError(
            "Google rejected the configured OAuth client. The GitHub secrets are present, "
            "but GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET do not identify an "
            "active Google OAuth client accepted for this refresh token. Replace those two "
            "GitHub secrets with the active OAuth client credentials that issued "
            "GOOGLE_OAUTH_REFRESH_TOKEN, or generate a new refresh token from the active client."
        ) from None
    raise

required_core = _resolve_folder(
    service,
    MY_DRIVE_ROOT_ID,
    ["FPL_VORTEX_DATA", "00_SOURCE_ARCHIVE", "FPL_CORE", "data"],
)
if required_core is None:
    raise RuntimeError(
        "Missing required Drive input: My Drive/FPL_VORTEX_DATA/00_SOURCE_ARCHIVE/FPL_CORE/data"
    )

print("Google Drive OAuth: PASS")
print("Required FPL_CORE Drive input: PASS")
print(
    "ODDS_API_KEY configured:",
    "YES"
    if os.environ.get("ODDS_API_KEY")
    else "NO (market-odds capture will use the notebook's existing no-key behavior)",
)
