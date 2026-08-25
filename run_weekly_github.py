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


def _preflight_notebook() -> None:
    if not NOTEBOOK.is_file():
        raise RuntimeError(f"Notebook not found: {NOTEBOOK}")
    nb = nbformat.read(NOTEBOOK, as_version=4)
    if not nb.cells:
        raise RuntimeError("Notebook contains no cells")

    forbidden = ("/content/drive/", "/content/drive/MyDrive/")
    duplicate_functions: collections.defaultdict[str, list[int]] = collections.defaultdict(list)
    syntax_errors: list[str] = []
    for index, cell in enumerate(nb.cells):
        if cell.cell_type != "code":
            continue
        source = cell.source
        for token in forbidden:
            if token in source:
                raise RuntimeError(f"Cell {index} still contains GitHub-incompatible path {token}")
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


def execute_notebook() -> None:
    if not os.environ.get("GITHUB_ACTIONS"):
        raise RuntimeError("This runner is only for GitHub Actions execution")
    _preflight_notebook()
    nb = nbformat.read(NOTEBOOK, as_version=4)
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
