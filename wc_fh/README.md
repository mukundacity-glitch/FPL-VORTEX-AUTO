# WC- FH

The separate **WC- FH** Actions workflow generates **one selected Wildcard or Free Hit video** in **Final 3840×2160 / 24fps**, then saves it as a **private YouTube draft** with its matching thumbnail, description, SEO tags and hashtags. It uses the current [WC/FH Colab notebook](https://colab.research.google.com/drive/1RVyrEGhHg77h4ek7ASowfRHIeYFDl2aB) downloaded by Drive file ID on every run. It preserves Ryan narration, the 8% body music, the footer subscribe animation, and the opening/outro.

## Run a video

1. Open [Actions → WC- FH](https://github.com/mukundacity-glitch/FPL-VORTEX-YOUTUBE-POST/actions/workflows/wc-fh.yml) on `main` and select **Run workflow**.
2. Leave **task = Render and upload**.
3. Choose **chip = Wildcard** or **Free Hit**.
4. Choose **selection = Auto** or **Manual**.
5. Auto uses the notebook's freshly optimized squad. Manual lets you choose all 15 players in the position dropdowns. Each list includes every current player in that position, prices and xPts. `★ WC` / `★ FH` identifies the model's recommended squad players, placed first. An **AUTO** slot keeps that slot's current optimized recommendation.
6. Start the run. Current prices, 15 unique players, 2 GK / 5 DEF / 5 MID / 3 FWD and the club limit are checked before the notebook's own budget/eligibility checks and rendering. The selected team and **total cost** appear in the Actions summary and `data/selection.json`.

There is no Draft control. This workflow is manual-only and has no schedule or automatic video trigger.

## See a live cost while selecting players

GitHub's native Run workflow form accepts fixed inputs and does not recalculate a total while you change dropdowns. Use the companion [squad_picker.html](picker/squad_picker.html): download the raw file and open it in a browser. It works without a login or network request.

- Separate Wildcard and Free Hit tabs retain both squads and show both totals.
- Full position lists, search, current prices, chip-specific model recommendations first, and recommended squads prefilled.
- Every change recalculates the cost immediately. Duplicate picks are disabled and squads with more than three players per club are flagged.
- Click **Copy squad IDs**, then choose that chip / Manual in the workflow and paste into **manual_squad**. Imported IDs override the individual dropdowns.
- The total uses current purchase prices. The notebook retains its existing personal-budget policy and checks during execution.

The repository's native dropdown labels and bundled picker are a dated snapshot. To refresh recommendations and prices without rendering or uploading, run **task = Refresh player picker**. This loads the latest official FPL data, runs both existing optimizers, and provides a **wc-fh-squad-picker** artifact containing a self-contained `squad_picker.html` and `players.json`. Open that HTML, or load its JSON into the existing picker. The video run always revalidates live IDs and prices, even when an older dropdown label was selected.

To update the repository's static dropdown labels after a refresh, download the new catalog and run:

```python
import json
from pathlib import Path
from wc_fh.catalog import update_workflow

catalog = json.loads(Path("players.json").read_text(encoding="utf-8"))
update_workflow(catalog, Path(".github/workflows/wc-fh.yml"))
```

## Outputs and existing credentials

- Drive: `My Drive/FPL_VORTEX/WC_FH_GITHUB/runs/<run_id>_<attempt>/`.
- Actions artifacts: `wc-fh-final-*`, `wc-fh-reports-*`, `wc-fh-squad-picker-*`.
- YouTube: private draft, matching thumbnail, saved title/description/SEO tags/hashtags and an upload receipt with its Studio link. Public publication and subscriber notifications stay disabled, matching the existing workflows.
- Reuses `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_REFRESH_TOKEN`, `YOUTUBE_OAUTH_CLIENT_ID`, `YOUTUBE_OAUTH_CLIENT_SECRET`, `YOUTUBE_OAUTH_REFRESH_TOKEN`, `YOUTUBE_CHANNEL_ID`. YouTube client ID/secret retain the existing Google fallback.

Only `.github/workflows/wc-fh.yml` and `wc_fh/` belong to this feature. Day 1/2/3 workflows and shared modules are unchanged. Shared assets and the Colab file are read only. Only the isolated WC/FH output and model-cache directories are written back to Drive.

## Checks

```bash
python -m unittest discover -s wc_fh -p 'test_*.py' -v
python -m wc_fh.run_notebook /path/to/current-notebook.ipynb --validate-only
```
