from __future__ import annotations

import argparse
import json
import os
import re
import sys
import types
from pathlib import Path
from typing import Iterable

from IPython.core.interactiveshell import InteractiveShell


PLAN_VALUES = {"NEITHER", "ROLL", "TRANSFER"}
TRANSFER_MODE_VALUES = {"AUTO", "MANUAL"}
NOTEBOOK_CONTROL_CELL_INDEX = 1
PREVIEW_CELL_MARKER = "CELL 14C — ALL SELECTED SLIDES • LIVE HTML PREVIEW IN COLAB"


def _replace_once(source: str, pattern: str, replacement: str, label: str) -> str:
    updated, count = re.subn(pattern, replacement, source, count=1, flags=re.MULTILINE)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return updated


def _parse_player_choice(value: str) -> int | None:
    value = str(value or "").strip()
    if not value or value.upper() == "NONE":
        return None
    match = re.match(r"^\s*(\d+)\b", value)
    if not match:
        raise ValueError(f"Invalid player choice: {value!r}")
    player_id = int(match.group(1))
    if player_id <= 0:
        raise ValueError(f"Invalid player id: {player_id}")
    return player_id


def _manual_pairs_from_env() -> tuple[list[int], list[int]]:
    outs: list[int] = []
    ins: list[int] = []
    found_gap = False

    for index in range(1, 6):
        out_id = _parse_player_choice(os.getenv(f"DAY3_OUT_{index}", "NONE"))
        in_id = _parse_player_choice(os.getenv(f"DAY3_IN_{index}", "NONE"))

        if out_id is None and in_id is None:
            found_gap = True
            continue
        if out_id is None or in_id is None:
            raise ValueError(
                f"Manual transfer pair {index} is incomplete. Select both Player OUT {index} "
                f"and Player IN {index}, or leave both as NONE."
            )
        if found_gap:
            raise ValueError(
                "Manual transfer pairs must be filled in order without gaps. "
                "Use pair 1 first, then pair 2, and so on."
            )
        outs.append(out_id)
        ins.append(in_id)

    if not 1 <= len(outs) <= 5:
        raise ValueError("Transfer → Manual requires between 1 and 5 complete OUT/IN pairs.")
    if len(set(outs)) != len(outs):
        raise ValueError("The same Player OUT cannot be selected more than once.")
    if len(set(ins)) != len(ins):
        raise ValueError("The same Player IN cannot be selected more than once.")
    if set(outs) & set(ins):
        raise ValueError("A player cannot appear in both the OUT and IN selections.")
    return outs, ins


def _patch_control_cell(source: str) -> str:
    plan = str(os.environ.get("DAY3_PLAN", "NEITHER")).strip().upper()
    transfer_mode = str(os.environ.get("DAY3_TRANSFER_MODE", "AUTO")).strip().upper()

    if plan not in PLAN_VALUES:
        raise ValueError(f"DAY3_PLAN must be one of {sorted(PLAN_VALUES)}, got {plan!r}")
    if transfer_mode not in TRANSFER_MODE_VALUES:
        raise ValueError(
            f"DAY3_TRANSFER_MODE must be one of {sorted(TRANSFER_MODE_VALUES)}, "
            f"got {transfer_mode!r}"
        )

    # Production workflow is intentionally fixed to FINAL + FULL_VIDEO.
    source = _replace_once(
        source,
        r'^RENDER_MODE\s*=\s*"[^"]+"\s*# @param \["DRAFT", "FINAL"\]\s*$',
        'RENDER_MODE = "FINAL"  # @param ["DRAFT", "FINAL"]',
        "render mode",
    )
    source = _replace_once(
        source,
        r'^OUTPUT_MODE\s*=\s*"[^"]+"\s*# @param \["SLIDES_ONLY", "FULL_VIDEO"\]\s*$',
        'OUTPUT_MODE = "FULL_VIDEO"  # @param ["SLIDES_ONLY", "FULL_VIDEO"]',
        "output mode",
    )

    fixed_true_flags = (
        "INTRO",
        "PROJECTED_GOALS",
        "DEFENSIVE_CONTRIBUTION",
        "FDR_PLAYER_RECOMMENDATIONS",
        "TRANSFER_DECISION",
        "GAMEWEEK_REVIEW",
        "ELITE_OWNERSHIP_TRENDS",
        "BENCHING_DECISION",
        "OUTRO",
    )
    for flag in fixed_true_flags:
        source = _replace_once(
            source,
            rf"^{re.escape(flag)}\s*=\s*(?:True|False)\s*# @param \{{type:\"boolean\"\}}\s*$",
            f'{flag} = True  # @param {{type:"boolean"}}',
            flag,
        )

    roll_selected = plan == "ROLL"
    transfer_selected = plan == "TRANSFER"
    source = _replace_once(
        source,
        r'^GAMEWEEK_PLAN_ROLL\s*=\s*(?:True|False)\s*# @param \{type:"boolean"\}\s*$',
        f'GAMEWEEK_PLAN_ROLL = {roll_selected}  # @param {{type:"boolean"}}',
        "roll scene selector",
    )
    source = _replace_once(
        source,
        r'^TRANSFER_PLAN\s*=\s*(?:True|False)\s*# @param \{type:"boolean"\}\s*$',
        f'TRANSFER_PLAN = {transfer_selected}  # @param {{type:"boolean"}}',
        "transfer scene selector",
    )

    internal_plan = "B • TRANSFER 1–5" if transfer_selected else "A • ROLL"
    source = _replace_once(
        source,
        r'^PLAN\s*=\s*"[^"]+"\s*# @param \["A • ROLL", "B • TRANSFER 1–5"\]\s*$',
        f'PLAN = "{internal_plan}"  # @param ["A • ROLL", "B • TRANSFER 1–5"]',
        "plan",
    )
    source = _replace_once(
        source,
        r'^ACCOUNT\s*=\s*"[^"]+"\s*# @param \["AUTO", "MANUAL"\]\s*$',
        'ACCOUNT = "AUTO"  # @param ["AUTO", "MANUAL"]',
        "account mode",
    )

    # Always bypass the interactive Colab picker. Manual GitHub choices are injected
    # immediately after the picker call, then the notebook's existing transfer
    # validation applies them to the final squad.
    source = _replace_once(
        source,
        r'^PLAYERS\s*=\s*"[^"]+"\s*# @param \["AUTO MODEL", "MANUAL PICKER"\]\s*$',
        'PLAYERS = "AUTO MODEL"  # @param ["AUTO MODEL", "MANUAL PICKER"]',
        "player selection mode",
    )

    if plan == "TRANSFER" and transfer_mode == "MANUAL":
        outs, ins = _manual_pairs_from_env()
        manual_block = f'''
_vx_build_my_plan_player_picker()

# GitHub Actions manual-transfer bridge.
MY_PLAN_SELECTION_MODE = "MANUAL PICKER"
MY_PLAN_TRANSFER_COUNT = "{len(outs)}"
MY_PLAN_TRANSFER_SELECTION = "MANUAL OUT / IN IDS"
MY_PLAN_MANUAL_OUT_PLAYER_IDS = "{",".join(map(str, outs))}"
MY_PLAN_MANUAL_IN_PLAYER_IDS = "{",".join(map(str, ins))}"
VX_MY_PLAN_SELECTION_READY = True
VX_MY_PLAN_PICKER_STATE = {{
    "resolver_version": "GITHUB_ACTIONS_MANUAL_V1",
    "team_id": int(FPL_TEAM_ID),
    "scenario_key": str(MY_PLAN_SCENARIO_KEY),
    "scenario": str(MY_PLAN_SCENARIO),
    "count": {len(outs)},
    "outs": {outs!r},
    "ins": {ins!r},
    "ready": True,
}}
print("✅ GitHub Manual Transfer selection loaded: "
      + " • ".join(
          f"{{index}}. OUT {{out_id}} → IN {{in_id}}"
          for index, (out_id, in_id) in enumerate(zip({outs!r}, {ins!r}), 1)
      ))
'''
        source = source.replace(
            "_vx_build_my_plan_player_picker()\n\n\nMY_PLAN_SCENARIOS",
            manual_block + "\n\nMY_PLAN_SCENARIOS",
            1,
        )
        if "GITHUB_ACTIONS_MANUAL_V1" not in source:
            raise RuntimeError("Could not inject GitHub manual-transfer bridge.")
    elif plan == "TRANSFER":
        # Transfer Auto: notebook chooses the best legal 1–5 transfers.
        pass
    else:
        # Neither and Roll require no transfer picker. Neither omits both personal-plan
        # scenes; Roll keeps only 5A.
        transfer_mode = "AUTO"

    return source


def _install_colab_compatibility() -> None:
    google_pkg = sys.modules.get("google")
    if google_pkg is None:
        google_pkg = types.ModuleType("google")
        google_pkg.__path__ = []
        sys.modules["google"] = google_pkg

    colab_pkg = types.ModuleType("google.colab")
    drive_mod = types.ModuleType("google.colab.drive")
    output_mod = types.ModuleType("google.colab.output")
    userdata_mod = types.ModuleType("google.colab.userdata")

    def mount(path: str, **_: object) -> None:
        Path(path).mkdir(parents=True, exist_ok=True)
        (Path(path) / "MyDrive").mkdir(parents=True, exist_ok=True)
        print(f"✅ GitHub runner Drive compatibility active at {path}")

    def enable_custom_widget_manager() -> None:
        return None

    def serve_kernel_port_as_iframe(*_: object, **__: object) -> None:
        print("ℹ️ Colab live preview iframe skipped on GitHub Actions.")

    def get_secret(name: str) -> str:
        value = str(os.environ.get(name, "") or "").strip()
        if not value:
            raise KeyError(name)
        return value

    drive_mod.mount = mount  # type: ignore[attr-defined]
    output_mod.enable_custom_widget_manager = enable_custom_widget_manager  # type: ignore[attr-defined]
    output_mod.serve_kernel_port_as_iframe = serve_kernel_port_as_iframe  # type: ignore[attr-defined]
    userdata_mod.get = get_secret  # type: ignore[attr-defined]

    colab_pkg.drive = drive_mod  # type: ignore[attr-defined]
    colab_pkg.output = output_mod  # type: ignore[attr-defined]
    colab_pkg.userdata = userdata_mod  # type: ignore[attr-defined]
    google_pkg.colab = colab_pkg  # type: ignore[attr-defined]

    sys.modules["google.colab"] = colab_pkg
    sys.modules["google.colab.drive"] = drive_mod
    sys.modules["google.colab.output"] = output_mod
    sys.modules["google.colab.userdata"] = userdata_mod


def _iter_code_cells(notebook: dict[str, object]) -> Iterable[tuple[int, str]]:
    cells = notebook.get("cells")
    if not isinstance(cells, list):
        raise ValueError("Notebook JSON does not contain a cells list.")
    for index, cell in enumerate(cells):
        if not isinstance(cell, dict) or cell.get("cell_type") != "code":
            continue
        source = cell.get("source", "")
        if isinstance(source, list):
            source_text = "".join(str(part) for part in source)
        else:
            source_text = str(source)
        if source_text.strip():
            yield index, source_text


def run_notebook(notebook_path: Path) -> None:
    notebook = json.loads(notebook_path.read_text(encoding="utf-8"))
    cells = list(_iter_code_cells(notebook))
    if len(cells) < 30:
        raise RuntimeError(f"Unexpected Day 3 notebook shape: only {len(cells)} code cells.")

    _install_colab_compatibility()
    shell = InteractiveShell.instance()
    shell.autoawait = True
    shell.user_ns["__name__"] = "__main__"
    shell.user_ns["__file__"] = str(notebook_path)

    for cell_index, source in cells:
        if cell_index == NOTEBOOK_CONTROL_CELL_INDEX:
            source = _patch_control_cell(source)
        if PREVIEW_CELL_MARKER in source:
            print(f"⏭️ Skipping Colab-only live preview cell {cell_index}.")
            continue

        print(f"\n{'=' * 76}\n▶ DAY 3 NOTEBOOK CELL {cell_index}\n{'=' * 76}", flush=True)
        result = shell.run_cell(source, store_history=False, silent=False)
        error = result.error_before_exec or result.error_in_exec
        if error is not None:
            raise RuntimeError(f"Day 3 notebook cell {cell_index} failed") from error

    print("✅ Final Day 3 notebook execution completed.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the final Day 3 Colab notebook in GitHub Actions.")
    parser.add_argument("notebook", type=Path)
    args = parser.parse_args()

    notebook_path = args.notebook.resolve()
    if not notebook_path.is_file():
        raise FileNotFoundError(notebook_path)
    run_notebook(notebook_path)


if __name__ == "__main__":
    main()
