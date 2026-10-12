# FPL VORTEX AUTO

## Day 1 and Day 2 video workflows

Day 1 remains the existing implementation on `main`:

- Workflow: `.github/workflows/day-1.yml`
- Source: `1_final_day1.py`
- Media integration: `vortex/day1_media.py`
- YouTube package/upload: `vortex/youtube_studio_package.py` and `vortex/youtube_private_upload.py`
- Output root: `My Drive/FPL_VORTEX/FIRST VIDEO`

Day 2 is isolated from Day 1:

- Workflow: `.github/workflows/day-2.yml`
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
2. Select **Day 2**.
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

## Day 3 workflow and authentication

[`day-3.yml`](.github/workflows/day-3.yml) is a manually dispatched workflow. It downloads the production notebook from Drive, restores the model inputs and media assets, runs the patched notebook in FINAL / FULL_VIDEO mode, uploads one private YouTube draft with its thumbnail, syncs the output to Drive, and exposes the finished video as an Actions artifact.

The authentication components are:

| Component | Credentials | Request flow and storage |
| --- | --- | --- |
| GitHub checkout | GitHub's job `GITHUB_TOKEN` | The workflow grants `contents: read`; `actions/checkout` uses the token to fetch the repo. This token is separate from the Google credentials. |
| Drive setup in `day-3.yml` | `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_REFRESH_TOKEN` | POST a form with `grant_type=refresh_token` to `https://oauth2.googleapis.com/token`. Download the notebook with `Authorization: Bearer <access_token>`. Store the client credentials, refresh token, access token, and expiry in `$RUNNER_TEMP/rclone/rclone.conf`, with mode `0600`. |
| Drive asset/state transfer | The same temporary rclone configuration | `vortex-drive` accesses My Drive and `day1-assets` uses the configured asset folder as its root. Only the configuration path is written to `GITHUB_ENV`. rclone uses the refresh token when the access token expires. The configuration is outside the output/artifact paths. |
| Colab compatibility in [`day3/run_notebook.py`](day3/run_notebook.py) | Runner environment variables | `google.colab.userdata.get(name)` reads the corresponding environment value. The Drive mount shim creates local directories; the earlier rclone restore provides their contents. |
| YouTube in [`day3/publish_private.py`](day3/publish_private.py) | `YOUTUBE_OAUTH_REFRESH_TOKEN`, YouTube client ID/secret, `YOUTUBE_CHANNEL_ID` | `Credentials(token=None, ...)` refreshes through Google's token endpoint. The client ID/secret individually fall back to their `GOOGLE_OAUTH_*` counterparts when absent; the YouTube refresh token has no fallback. The API client retains the access token in memory. |
| Official FPL data | No FPL login credentials | Public bootstrap, fixtures, entry/picks, and event-live reads use HTTP requests. OAuth tokens for Drive/YouTube are not FPL session tokens. |

The YouTube credentials request `youtube.upload` and `youtube.readonly`. `_verify_channel()` calls `channels.list(mine=True)` and rejects a channel other than `YOUTUBE_CHANNEL_ID`. `_upload_video()` explicitly sets `privacyStatus=private` and `notifySubscribers=false`; `_verify_private()` checks the resulting status. Upload metadata and reports contain video/channel information, not OAuth credentials. The repository uses preauthorized refresh tokens; it does not implement an interactive OAuth consent/callback flow or store Google passwords.

References: [GitHub workflow token authentication](https://docs.github.com/en/actions/tutorials/authenticate-with-github_token), [Google refresh tokens and offline access](https://developers.google.com/identity/protocols/oauth2/web-server#offline), and [rclone Drive configuration](https://rclone.org/drive/).

### Completed DEFCON history versus live review

The notebook's canonical FPL snapshot distinguishes `review_gw` (which may be `LIVE/PARTIAL`) from `review_ready_gws` (fully completed events). When the forecast is GW7 and GW6 is still partial, the review can show GW6 while DEFCON's completed history ends at GW5.

The Day 3 runner validates every player's DEFCON cutoff and the complete model-history list against `review_ready_gws`. Player and team cumulative totals use that same history, and the board labels its completed cutoff explicitly. A stale/mixed player cutoff or missing model-history event still stops the run. Once GW6 becomes review-ready, DEFCON must advance to GW6. The patch does not change the model formulas or the live review's gameweek selection.

After applying a runner fix, start a new **Day 3** workflow on `main`. Re-running a failed job retains the old run's source commit.


### Automatic watcher

`Gameweek State Watcher` checks official FPL state every two days (approximately 48 hours). Day 1 and Day 2 remain separate workflows. When the review state advances, the watcher can trigger each workflow independently; Day 2 remains Private-only on YouTube and keeps its own Drive output root.
