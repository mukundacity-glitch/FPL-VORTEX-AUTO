# FPL-VORTEX-AUTO

Automated data pipeline, historical database builder, modelling, and decision engine for FPL VORTEX.

## Workflows

- `FPL VORTEX Auto Update` refreshes the rich match feed and Official FPL snapshot twice daily at 08:17 and 18:17 UTC, then publishes the result to Google Drive.
- `FPL VORTEX` is the manually triggered weekly notebook renderer with `DRAFT` and `FINAL` video modes.

Both workflows require `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, and `GOOGLE_OAUTH_REFRESH_TOKEN` repository secrets.

## Local checks

```bash
python -m pip install -r requirements.txt
python run_weekly_github.py preflight
python -m unittest discover -s tests -v
```

The weekly preflight validates every code cell, rejects duplicate top-level function definitions, and verifies that the Phase 0 review guard and temporary-render cleanup each have exactly one owner.
