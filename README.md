# FPL VORTEX AUTO

## Day 1 and Day 2 video workflows

Day 1 remains the existing implementation on `main`:

- Workflow: `.github/workflows/day-1.yml`
- Source: `1_final_day1.py`
- Media integration: `vortex/day1_media.py`
- YouTube package/upload: `vortex/youtube_studio_package.py` and `vortex/youtube_private_upload.py`
- Output root: `My Drive/FPL_VORTEX/FIRST VIDEO`

Day 2 is isolated from Day 1:

- Workflow: `.github/workflows/day2-video.yml`
- Day 2 automation code/config: `day2/`
- Colab source: Drive file `DAY2_FINAL.ipynb` (configured by file/path in `day2/config.json`)
- Output root: `My Drive/FPL_VORTEX/Day_2`
- Actions artifacts: `day2-video-output-*` and `day2-video-log-*`
- YouTube uploads are explicitly `private`; automatic Public, Unlisted, and scheduled publishing are disabled.

The Day 2 workflow reads shared model inputs and the existing Day 1 media assets, but it never publishes to the Day 1 output path and never edits the Day 1 workflow or scripts. The Day 1 opening/music contract is reproduced in `day2/media.py`: opening first with its original audio, background music beginning at the Day 2 `slide_00` program start at the same `12/255` gain, and `outro.mp3` for the final 6 seconds. The Day 2 notebook keeps the canonical VORTEX logo/lion-head treatment and its Scene 7 subscribe CTA.

### Run Day 1 manually

1. Open the repository **Actions** tab.
2. Select **Day 1**.
3. Choose **Run workflow** on `main`.

No Day 2 input or file is required for Day 1.

### Run Day 2 manually

GitHub only exposes a `workflow_dispatch` workflow from the repository's default branch. After reviewing and manually merging `feature/day2-video-workflow` into `main`:

1. Open **Actions**.
2. Select **Day 2 Video — Build and Upload Draft**.
3. Choose **Run workflow**.
4. Set:
   - `upload_to_youtube`: `true` to allow a Private upload, or `false` to build only.
   - `dry_run`: `true` to build/render and validate metadata while skipping YouTube upload.
   - `video_date`: the date used in the title, for example `2026-09-03`.
   - `render_quality`: `FINAL` for a 3840x2160 YouTube-ready render; `DRAFT` for fast validation.
5. Start the run.

A real YouTube upload is refused unless `render_quality=FINAL`. Every upload request is created with `privacyStatus=private`, `notifySubscribers=false`, and no automatic scheduling/publication.

### Edit Day 2 metadata and assets

Edit `day2/metadata.json` for the default:

- video date
- topic/title template
- description/summary
- hashtags/tags

Edit `day2/config.json` only when the Day 2 Colab file, output location, or shared asset folder changes.

The media filenames intentionally match Day 1 and are read from the same established Drive asset folder:

- `opening.mp4`
- `Background_music.mp3`
- `outro.mp3`

The workflow copies those assets into runner-local Day 2 storage; it does not modify the Drive asset folder.

### GitHub secrets

Day 2 reuses the existing repository secret names established by Day 1:

- `GOOGLE_OAUTH_CLIENT_ID`
- `GOOGLE_OAUTH_CLIENT_SECRET`
- `GOOGLE_OAUTH_REFRESH_TOKEN`
- `YOUTUBE_OAUTH_CLIENT_ID`
- `YOUTUBE_OAUTH_CLIENT_SECRET`
- `YOUTUBE_OAUTH_REFRESH_TOKEN`
- `YOUTUBE_CHANNEL_ID`

The YouTube client ID/secret may fall back to the Google OAuth client ID/secret exactly as Day 1 does. `YOUTUBE_OAUTH_REFRESH_TOKEN` must authorize YouTube upload/read access for the same channel identified by `YOUTUBE_CHANNEL_ID`.

No credential value is written to logs, source, artifacts, or metadata files.
