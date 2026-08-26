from __future__ import annotations

import argparse
import ast
import collections
import os
import re
from pathlib import Path

import nbformat
from nbclient import NotebookClient

from vortex.drive_sync import (
    publish_weekly_to_drive,
    restore_weekly_from_drive,
    validate_weekly_outputs,
)

NOTEBOOK = Path(__file__).resolve().parent / "FPL_VORTEX_WEEKLY.ipynb"
LOCAL_ROOT = Path(os.environ.get("FPL_VORTEX_LOCAL_ROOT", "output")).resolve()
QUALITY_ASSIGNMENT = re.compile(
    r'^MP4_QUALITY\s*=\s*"(?:DRAFT|FINAL)"\s*#\s*@param\s*\["DRAFT",\s*"FINAL"\]\s*$',
    flags=re.M,
)
PHASE0_REVIEW_NAME_BAD = 'if "gw_review" in path.name.lower() or "review" in path.name.lower():'
PHASE0_REVIEW_NAME_FIXED = (
    'if "review" in re.split(r"[^a-z0-9]+", path.stem.lower()) and path.name.lower() not in _vx_phase0_approved_gw_review_outputs:'
)
# Filenames produced by the GW review cell chain (Cells 21, 23, 25, 29).
# These are the deterministic current-run scene template, data package, animation
# manifest, captions, and audio concat. They legitimately embed fixture context
# (FDR for the next 5 fixtures) as part of the approved design, so the
# retrospective-phrase content check is skipped for them. Any *other* file with
# "review" in its name is still scanned, so stale leftover artifacts are still
# caught as before.
PHASE0_APPROVED_GW_REVIEW_OUTPUTS = frozenset(
    {
        "gw_review_scene_4k.html",
        "gw_review_scene_4k_animated.html",
        "gw_review_package.json",
        "gw_review_animation_manifest.json",
        "gw_review_master.vtt",
        "gw_review_concat.txt",
    }
)


def _preflight_notebook() -> None:
    if not NOTEBOOK.is_file():
        raise RuntimeError(f"Notebook not found: {NOTEBOOK}")
    nb = nbformat.read(NOTEBOOK, as_version=4)
    if not nb.cells:
        raise RuntimeError("Notebook contains no cells")

    forbidden_paths = (
        "/content/drive/MyDrive/FPL_VORTEX",
        "/content/drive/MyDrive/FPL_VORTEX_DATA",
    )
    duplicate_functions: collections.defaultdict[str, list[int]] = collections.defaultdict(list)
    syntax_errors: list[str] = []
    quality_assignments = 0
    for index, cell in enumerate(nb.cells):
        if cell.cell_type != "code":
            continue
        source = cell.source
        quality_assignments += len(QUALITY_ASSIGNMENT.findall(source))
        for token in forbidden_paths:
            if token in source:
                raise RuntimeError(f"Cell {index} still contains GitHub-incompatible path {token}")
        if "drive.mount(" in source and index != 0:
            raise RuntimeError(f"Cell {index} contains an unexpected interactive Drive mount")
        if "from google.colab import drive" in source and index != 0:
            raise RuntimeError(f"Cell {index} contains an unexpected Colab Drive import")
        try:
            compile(source, f"<cell {index}>", "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
        except SyntaxError as exc:
            syntax_errors.append(f"cell {index}, line {exc.lineno}: {exc.msg}")
        for match in re.finditer(r"^def\s+([A-Za-z_]\w*)\s*\(", source, flags=re.M):
            duplicate_functions[match.group(1)].append(index)

    if syntax_errors:
        raise RuntimeError("Notebook syntax preflight failed:\n" + "\n".join(syntax_errors))
    duplicates = {name: cells for name, cells in duplicate_functions.items() if len(cells) > 1}
    if duplicates:
        raise RuntimeError(f"Duplicate notebook function definitions detected: {duplicates}")
    if quality_assignments != 1:
        raise RuntimeError(
            f"Expected exactly one notebook MP4_QUALITY owner, found {quality_assignments}"
        )


def _apply_phase0_cleanroom_filename_fix(nb) -> None:
    bad_matches: list[int] = []
    fixed_matches: list[int] = []
    for index, cell in enumerate(nb.cells):
        if cell.cell_type != "code":
            continue
        if PHASE0_REVIEW_NAME_BAD in cell.source:
            bad_matches.append(index)
        if PHASE0_REVIEW_NAME_FIXED in cell.source:
            fixed_matches.append(index)

    if bad_matches:
        if len(bad_matches) != 1:
            raise RuntimeError(
                "Expected exactly one Phase 0 review filename guard to patch, "
                f"found {len(bad_matches)} in cells {bad_matches}"
            )
        cell_index = bad_matches[0]
        source = nb.cells[cell_index].source

        # Inject the approved-outputs allowlist so the patched condition can
        # reference it. The constant is defined just before
        # _vx_phase0_review_forbidden_phrases so it is in scope when the scan
        # function runs at startup / pre-publish.
        if "_vx_phase0_approved_gw_review_outputs" not in source:
            anchor = "def _vx_phase0_review_forbidden_phrases"
            if anchor not in source:
                raise RuntimeError(
                    "Could not find the Phase 0 review forbidden phrases anchor "
                    "needed to inject the approved-outputs allowlist"
                )
            allowlist_block = (
                "# GitHub Actions only: the deterministic GW review outputs from\n"
                "# Cells 21, 23, 25 and 29 legitimately embed fixture context\n"
                "# (FDR for the next 5 fixtures) as part of the approved design.\n"
                "# They are NOT stale dependencies, so the retrospective-phrase\n"
                "# content check is skipped for these exact filenames. Any other\n"
                "# 'review'-named file is still scanned, so leftover artifacts\n"
                "# from prior runs continue to be caught.\n"
                f"_vx_phase0_approved_gw_review_outputs = frozenset({sorted(PHASE0_APPROVED_GW_REVIEW_OUTPUTS)!r})\n"
                "\n\n"
            )
            source = source.replace(anchor, allowlist_block + anchor, 1)

        nb.cells[cell_index].source = source.replace(
            PHASE0_REVIEW_NAME_BAD,
            PHASE0_REVIEW_NAME_FIXED,
            1,
        )
        print(
            "[VORTEX] Phase 0 clean-room filename guard fixed: "
            "review is now matched as a filename token, and approved "
            "GW review output files are exempted from the content check"
        )
        return

    if len(fixed_matches) != 1:
        raise RuntimeError(
            "Expected exactly one fixed Phase 0 review filename guard, "
            f"found {len(fixed_matches)} in cells {fixed_matches}"
        )


def _apply_github_render_quality(nb) -> str:
    quality = os.environ.get("FPL_VORTEX_MP4_QUALITY", "FINAL").strip().upper()
    if quality not in {"DRAFT", "FINAL"}:
        raise RuntimeError(
            "FPL_VORTEX_MP4_QUALITY must be DRAFT or FINAL, "
            f"got {quality!r}"
        )

    matches: list[tuple[int, re.Match[str]]] = []
    for index, cell in enumerate(nb.cells):
        if cell.cell_type != "code":
            continue
        match = QUALITY_ASSIGNMENT.search(cell.source)
        if match:
            matches.append((index, match))

    if len(matches) != 1:
        raise RuntimeError(
            f"Expected exactly one notebook MP4_QUALITY owner, found {len(matches)}"
        )

    cell_index, match = matches[0]
    source = nb.cells[cell_index].source
    replacement = f'MP4_QUALITY = "{quality}"  # @param ["DRAFT", "FINAL"]'
    nb.cells[cell_index].source = source[: match.start()] + replacement + source[match.end() :]
    print(f"[VORTEX] GitHub render quality selected: {quality}")
    return quality


def execute_notebook() -> None:
    if not os.environ.get("GITHUB_ACTIONS"):
        raise RuntimeError("This runner is only for GitHub Actions execution")
    _preflight_notebook()
    nb = nbformat.read(NOTEBOOK, as_version=4)
    _apply_phase0_cleanroom_filename_fix(nb)
    _apply_github_render_quality(nb)
    client = NotebookClient(
        nb,
        timeout=None,
        kernel_name="python3",
        allow_errors=False,
        resources={"metadata": {"path": str(NOTEBOOK.parent)}},
    )
    print(f"[VORTEX] Executing {NOTEBOOK.name} with {len(nb.cells)} cell(s)")
    client.execute()
    print("[VORTEX] Notebook execution PASS")


def main() -> None:
    parser = argparse.ArgumentParser(description="FPL VORTEX GitHub Actions weekly runner")
    parser.add_argument("stage", choices=("restore", "execute", "validate", "publish", "preflight"))
    args = parser.parse_args()

    if args.stage == "restore":
        restore_weekly_from_drive(LOCAL_ROOT)
    elif args.stage == "execute":
        execute_notebook()
    elif args.stage == "validate":
        validate_weekly_outputs(LOCAL_ROOT)
    elif args.stage == "publish":
        publish_weekly_to_drive(LOCAL_ROOT)
    else:
        _preflight_notebook()
        print("[VORTEX] Notebook preflight PASS")


if __name__ == "__main__":
    main()
