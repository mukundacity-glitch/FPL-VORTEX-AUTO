# WC- FH

The separate **WC- FH** Actions workflow generates **Wildcard, Free Hit, or both videos in one run** in **Final 3840×2160 / 24fps**, then saves each as a **private YouTube draft** with its own matching thumbnail, description, SEO tags and hashtags. **Both** is the default. It uses the current [WC/FH Colab notebook](https://colab.research.google.com/drive/1RVyrEGhHg77h4ek7ASowfRHIeYFDl2aB) downloaded by Drive file ID on every run. It preserves Ryan narration, the 8% body music, the footer subscribe animation, and the opening/outro.

## Run a video

1. Open [Actions → WC- FH](https://github.com/mukundacity-glitch/FPL-VORTEX-YOUTUBE-POST/actions/workflows/wc-fh.yml) on `main` and select **Run workflow**.
2. Leave **task = Render and upload**.
3. Choose **chip = Both**, **Wildcard**, or **Free Hit**.
4. Choose **selection = Auto** or **Manual**.
5. Auto uses a freshly optimized squad for each selected chip. Manual lets you choose all 15 players in the position dropdowns. Each list includes every current player in that position, prices and xPts. `★ WC` / `★ FH` identifies the model's recommended squad players, placed first. An **AUTO** slot keeps that chip's current optimized recommendation. In **Both** mode, dropdown overrides apply to both squads; AUTO slots can differ by chip.
6. For separate manual teams, paste 15 IDs into **wildcard_squad** and **free_hit_squad** from the matching picker tabs. Each chip-specific field overrides **manual_squad** and the dropdowns for that chip. The optional **manual_squad** field applies one common imported squad to any selected chip without a chip-specific import.
7. Start the run. Current prices, 15 unique players, 2 GK / 5 DEF / 5 MID / 3 FWD and the club limit are checked before the notebook's own budget/eligibility checks and rendering. Each selected team and its **total cost** appear in the Actions summary. Both mode saves `data/selection_wildcard.json` and `data/selection_free_hit.json`; single-chip mode retains `data/selection.json`.

There is no Draft control. This workflow is manual-only and has no schedule or automatic video trigger.

## See a live cost while selecting players

GitHub's native Run workflow form accepts fixed inputs and does not recalculate a total while you change dropdowns. Use the companion [squad_picker.html](picker/squad_picker.html): download the raw file and open it in a browser. It works without a login or network request.

- Separate Wildcard and Free Hit tabs retain both squads and show both totals.
- Full position lists, search, current prices, chip-specific model recommendations first, and recommended squads prefilled.
- Every change recalculates the cost immediately. Duplicate picks are disabled and squads with more than three players per club are flagged.
- Click **Copy Wildcard IDs** and paste into **wildcard_squad**; switch tabs and use **Copy Free Hit IDs** for **free_hit_squad**. Choose **Both / Manual** to use both teams in one run, or select an individual chip.
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
- YouTube: one private draft per selected chip, each with a matching thumbnail, saved title/description/SEO tags/hashtags and an upload receipt with its Studio link. Both mode keeps these under `youtube/wildcard_<mode>/` and `youtube/free_hit_<mode>/`. A retry reuses each existing upload receipt. Public publication and subscriber notifications stay disabled, matching the existing workflows.
- Reuses `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_REFRESH_TOKEN`, `YOUTUBE_OAUTH_CLIENT_ID`, `YOUTUBE_OAUTH_CLIENT_SECRET`, `YOUTUBE_OAUTH_REFRESH_TOKEN`, `YOUTUBE_CHANNEL_ID`. YouTube client ID/secret retain the existing Google fallback.

Only `.github/workflows/wc-fh.yml` and `wc_fh/` belong to this feature. Day 1/2/3 workflows and shared modules are unchanged. Shared assets and the Colab file are read only. Only the isolated WC/FH output and model-cache directories are written back to Drive.

## Checks

The headless runner validates scene data and narration before rendering, then checks visuals only after actual PCM narration has supplied measured cue times. Pillow scenes retain frame, image-aspect, pitch-aspect and text-overlap checks. Remotion scenes use their native render/still path. Every selected video must also have four decoded native 4K PNGs and four measured scene timelines before upload; the reports artifact includes scene manifests and `*_visual_qa.json`. The browser renderer closes before Python shuts down its thread pools, including on failure.

```bash
python -m unittest discover -s wc_fh -p 'test_*.py' -v
python -m wc_fh.run_notebook /path/to/current-notebook.ipynb --validate-only
```
