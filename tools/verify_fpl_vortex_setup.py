from __future__ import annotations

import os

from vortex.drive_sync import MY_DRIVE_ROOT_ID, _resolve_folder, _service

REQUIRED_GOOGLE_SECRETS = (
    "GOOGLE_OAUTH_CLIENT_ID",
    "GOOGLE_OAUTH_CLIENT_SECRET",
    "GOOGLE_OAUTH_REFRESH_TOKEN",
)

missing = [name for name in REQUIRED_GOOGLE_SECRETS if not os.environ.get(name)]
if missing:
    raise RuntimeError("Missing GitHub secret(s): " + ", ".join(missing))

service = _service()
service.files().get(fileId=MY_DRIVE_ROOT_ID, fields="id").execute()

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
print("ODDS_API_KEY configured:", "YES" if os.environ.get("ODDS_API_KEY") else "NO (market-odds capture will use the notebook's existing no-key behavior)")
