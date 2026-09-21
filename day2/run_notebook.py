from __future__ import annotations

import argparse
import ast
import collections
import hashlib
import json
import re
import os
import subprocess
import sys
import types
from pathlib import Path

from IPython.core.interactiveshell import InteractiveShell

from .common import read_json


DAY2_OUTPUT_ROOT = "/content/drive/MyDrive/FPL_VORTEX/Day_2"
CONFIG_PATH = Path(__file__).with_name("config.json")
QUALITY_PATTERN = re.compile(
    r'(?m)^MP4_QUALITY\s*=\s*["\'](?:DRAFT|FINAL)["\'](?P<suffix>[^\n]*)$'
)


def _cell_source(cell: dict) -> str:
    source = cell.get("source", [])
    return "".join(source) if isinstance(source, list) else str(source)


def _set_cell_source(cell: dict, source: str) -> None:
    cell["source"] = source.splitlines(keepends=True)



LAST_DATA_SLIDE_PATCH_MARKER = "DAY2_LAST_DATA_SLIDE_PATCH_V1"
_LAST_DATA_SLIDE_TERMS = ("MODEL", "EO", "EXPOSURE", "TRAP")
_LAST_DATA_SLIDE_PROTECTED = (
    "FPL_VORTEX_DAY2_FINAL.MP4",
    "FFMPEG",
    "PLAYWRIGHT",
    "INTERACTIVESHELL",
    "WRITE_FFCONCAT",
)


def _safe_current_gw() -> int:
    for name in ("GW0", "GW", "NEXT_GW", "TARGET_GW", "CURRENT_GW"):
        value = globals().get(name)
        try:
            if value is not None and int(value) > 0:
                return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def _neutral_payload_value(key: str):
    """Return a template-safe empty value for a removed final data surface."""
    k = str(key or "").strip().lower()
    if any(token in k for token in (
        "rows", "cards", "recs", "target", "avoid", "players", "fixtures",
        "items", "stats", "leaders", "picks", "alternatives", "results",
    )):
        return []
    if k in {"gws", "gameweeks", "weeks"} or k.endswith("_gws"):
        gw = _safe_current_gw()
        return [gw] if gw else []
    if k in {"gw", "gameweek", "week"} or k.endswith("_gw"):
        return _safe_current_gw()
    if "chip" in k:
        return {"name": "", "gw": _safe_current_gw()}
    if any(token in k for token in ("bool", "enabled", "visible", "show_", "ok")):
        return False
    if any(token in k for token in (
        "title", "headline", "label", "sub", "subtitle", "summary",
        "description", "message", "text", "note",
    )):
        return ""
    if any(token in k for token in (
        "score", "prob", "percent", "pct", "value", "price", "proj",
        "rank", "count", "duration", "n_", "total", "avg",
    )):
        return 0
    if any(token in k for token in ("photo", "image", "url", "team", "name")):
        return ""
    return ""


def _extract_last_add_contract(source: str):
    """Find the last add() call and its stable section/template contract."""
    tree = ast.parse(source, filename="DAY2_FINAL.ipynb::target-slide")
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (
            func.id if isinstance(func, ast.Name)
            else func.attr if isinstance(func, ast.Attribute)
            else ""
        )
        if name != "add" or len(node.args) < 4:
            continue
        if not (
            isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            continue
        calls.append(node)

    if not calls:
        return None

    call = max(
        calls,
        key=lambda node: (
            getattr(node, "end_lineno", node.lineno),
            getattr(node, "end_col_offset", 0),
        ),
    )
    payload_keys = []
    payload = call.args[3]
    if isinstance(payload, ast.Dict):
        for key in payload.keys:
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                payload_keys.append(key.value)

    return {
        "section_id": call.args[0].value,
        "template": call.args[1].value,
        "payload_keys": payload_keys,
        "start_line": call.lineno,
        "end_line": getattr(call, "end_lineno", call.lineno),
    }


def _replace_last_data_slide(payload: dict) -> bool:
    """
    Replace only the final MODEL/EO/EXPOSURE/TRAP presentation block.

    The surrounding notebook, section id, execution order, and downstream
    renderer remain intact. The old calculations before the final SB()/add()
    block are intentionally left untouched so dependent notebook state cannot
    disappear during this safety migration.
    """
    cells = payload.get("cells") or []
    if not cells:
        raise RuntimeError("Day 2 notebook has no cells")

    for cell in cells:
        if LAST_DATA_SLIDE_PATCH_MARKER in _cell_source(cell):
            print("[DAY 2] Last data slide replacement: already applied")
            return False

    candidates = []
    for index, cell in enumerate(cells):
        if cell.get("cell_type") != "code":
            continue
        source = _cell_source(cell)
        upper = source.upper()
        if index <= 13:
            continue
        if any(marker in upper for marker in _LAST_DATA_SLIDE_PROTECTED):
            continue

        model_hits = sum(upper.count(term) for term in _LAST_DATA_SLIDE_TERMS)
        slide_hits = upper.count("SCENE") + upper.count("SLIDE")
        if model_hits < 3 or slide_hits < 1:
            continue

        contract = _extract_last_add_contract(source)
        if not contract:
            continue

        sb_nodes = []
        try:
            tree = ast.parse(source, filename=f"DAY2_FINAL.ipynb::cell-{index}")
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                    func = node.value.func
                    func_name = (
                        func.id if isinstance(func, ast.Name)
                        else func.attr if isinstance(func, ast.Attribute)
                        else ""
                    )
                    if func_name == "SB":
                        sb_nodes.append(node)
                elif isinstance(node, ast.AnnAssign) and isinstance(node.value, ast.Call):
                    func = node.value.func
                    func_name = (
                        func.id if isinstance(func, ast.Name)
                        else func.attr if isinstance(func, ast.Attribute)
                        else ""
                    )
                    if func_name == "SB":
                        sb_nodes.append(node)
        except SyntaxError:
            continue

        if not sb_nodes:
            continue

        sb_node = max(
            sb_nodes,
            key=lambda node: (
                getattr(node, "end_lineno", node.lineno),
                getattr(node, "end_col_offset", 0),
            ),
        )
        if (
            getattr(sb_node, "lineno", 0) >= contract["start_line"]
            or getattr(sb_node, "end_lineno", sb_node.lineno) > contract["start_line"]
        ):
            continue

        score = model_hits + (2 * slide_hits)
        candidates.append((index, score, contract, sb_node))

    if not candidates:
        print(
            "[DAY 2] Last data slide replacement: no safe MODEL/EXPOSURE/TRAP "
            "presentation block matched; notebook left unchanged"
        )
        return False

    index, _, contract, sb_node = max(candidates, key=lambda item: (item[0], item[1]))
    source = _cell_source(cells[index])
    lines = source.splitlines()

    start_line = sb_node.lineno
    end_line = contract["end_line"]
    indent_source = lines[start_line - 1]
    indent = indent_source[: len(indent_source) - len(indent_source.lstrip())]

    if contract["payload_keys"]:
        payload_lines = []
        for key in contract["payload_keys"]:
            value = _neutral_payload_value(key)
            payload_lines.append(
                f"{indent}_final_payload[{key!r}] = {value!r}"
            )
        payload_body = "\n".join(payload_lines)
        add_template = contract["template"]
    else:
        add_template = (
            "summary"
            if not (
                isinstance(globals().get("TEMPLATES"), dict)
                and "summary" not in globals().get("TEMPLATES", {})
            )
            else contract["template"]
        )
        payload_body = "\n".join(
            (
                f"{indent}_final_payload = {{}}",
                f"{indent}_final_payload['target'] = []",
                f"{indent}_final_payload['avoid'] = []",
                f"{indent}_final_payload['recs'] = []",
                f"{indent}_final_payload['chip'] = "
                f"{{'name': '', 'gw': _safe_current_gw()}}",
            )
        )

    replacement_lines = [
        f"{indent}# {LAST_DATA_SLIDE_PATCH_MARKER}",
        f"{indent}b = SB()",
        f"{indent}_final_gw = _safe_current_gw()",
        f"{indent}if _final_gw:",
        f"{indent}    b.s(",
        f"{indent}        f\"Final check for Gameweek {{_final_gw}}: keep the final decision simple and actionable.\",",
        f"{indent}        nums=[_final_gw],",
        f"{indent}    )",
        f"{indent}else:",
        f"{indent}    b.s(\"Final check: keep the last decision simple and actionable.\")",
        f"{indent}b.s(\"Confirm lineups, expected minutes, fixtures, and late team news before the deadline.\")",
        f"{indent}b.s(\"That closes the data section. The final VORTEX outro follows.\")",
        f"{indent}_final_payload = {{}}",
        payload_body,
        f"{indent}add({contract['section_id']!r}, {add_template!r}, \"FINAL CHECK\", _final_payload, b)",
    ]

    new_lines = lines[:start_line - 1] + replacement_lines + lines[end_line:]
    repaired_source = "\n".join(new_lines) + "\n"
    compile(repaired_source, f"DAY2_FINAL.ipynb::cell-{index}", "exec")
    _set_cell_source(cells[index], repaired_source)

    print(
        "[DAY 2] Last data slide replacement: PASS "
        f"(cell={index}, section={contract['section_id']!r}, template={add_template!r})"
    )
    print("[DAY 2] Removed final-slide presentation of model/EO/exposure trap data")
    return True


def _repair_python311_fstrings(payload: dict) -> bool:
    """Repair every legacy Scene 5 nested f-string unsupported by Python 3.11."""
    cells = payload.get("cells") or []
    if len(cells) <= 13 or cells[13].get("cell_type") != "code":
        raise RuntimeError("Day 2 scene cell 13 is missing")

    source = _cell_source(cells[13])
    if (
        "_badge_html =" in source
        and "_hero_html =" in source
        and "_summary_visual_html =" in source
        and "_summary_badge_html =" in source
    ):
        return False

    lines = source.splitlines()

    try:
        featured_anchor = next(
            index
            for index, line in enumerate(lines)
            if line.strip()
            == '_warning = _s5e(str(player.get("warning") or "").upper())'
        )
    except StopIteration as exc:
        raise RuntimeError("Scene 5 featured-card repair anchor is missing") from exc

    featured_helpers = [
        '    _badge_html = f\'<img src="{_badge}" alt="{_club}">\' if _badge else _club',
        '    _hero_html = (',
        '        \'<img class="vxPlayerVisual {}" src="{}" alt="{}" \'',
        '        \'onerror="if(this.dataset.fallback!==\\\'1\\\'){{this.dataset.fallback=\\\'1\\\';\'',
        '        \'this.src=\\\'{}\\\';this.classList.add(\\\'jerseyFallback\\\')}}\'',
        '        \'else{{this.style.display=\\\'none\\\'}}">\'',
        '    ).format(_img_kind, _img, _name, _img_fallback) if _img else (',
        '        f\'<div class="heroInitials">{_name[:2]}</div>\'',
        '    )',
        '    _variance_html = (',
        '        \'<div class="varianceTag">HIGHER VARIANCE</div>\' if rank == 3 else ""',
        '    )',
    ]
    lines[featured_anchor + 1:featured_anchor + 1] = featured_helpers

    try:
        summary_start = next(
            index
            for index, line in enumerate(lines)
            if line.startswith("def _s5_summary_row")
        )
        summary_anchor = next(
            index
            for index in range(summary_start, len(lines))
            if lines[index].strip()
            == '_purpose = _s5e(str(player.get("purpose") or "DIFFERENTIAL").upper())'
        )
    except StopIteration as exc:
        raise RuntimeError("Scene 5 summary-card repair anchor is missing") from exc

    summary_helpers = [
        '    _summary_visual_html = (',
        '        \'<img class="photo vxPlayerVisual {}" src="{}" alt="{}" \'',
        '        \'onerror="if(this.dataset.fallback!==\\\'1\\\'){{this.dataset.fallback=\\\'1\\\';\'',
        '        \'this.src=\\\'{}\\\';this.classList.add(\\\'jerseyFallback\\\')}}\'',
        '        \'else{{this.style.display=\\\'none\\\'}}">\'',
        '    ).format(_img_kind, _img, _name, _img_fallback) if _img else (',
        '        f\'<div class="initials">{_name[:2]}</div>\'',
        '    )',
        '    _summary_badge_html = f\'<img src="{_badge}" alt="{_club}">\' if _badge else _club',
    ]
    lines[summary_anchor + 1:summary_anchor + 1] = summary_helpers

    replaced = {
        "featured_badge": False,
        "featured_visual": False,
        "variance": False,
        "summary_visual": False,
        "summary_badge": False,
    }
    for index, line in enumerate(lines):
        if '<div class="teamBadge">{f\'<img src=' in line:
            lines[index] = '      <div class="teamBadge">{_badge_html}</div>'
            replaced["featured_badge"] = True
        elif '<div class="heroVisual">{f\'<img class=' in line:
            lines[index] = '      <div class="heroVisual">{_hero_html}</div>'
            replaced["featured_visual"] = True
        elif '{f\'<div class="varianceTag">HIGHER VARIANCE</div>\'' in line:
            lines[index] = '      {_variance_html}'
            replaced["variance"] = True
        elif '<div class="visual">{f\'<img class="photo' in line:
            lines[index] = '      <div class="visual">{_summary_visual_html}</div>'
            replaced["summary_visual"] = True
        elif '<div class="summaryBadge">{f\'<img src=' in line:
            lines[index] = '      <div class="summaryBadge">{_summary_badge_html}</div>'
            replaced["summary_badge"] = True

    missing = [name for name, ok in replaced.items() if not ok]
    if missing:
        raise RuntimeError(
            "Scene 5 Python 3.11 repair did not replace: " + ", ".join(missing)
        )

    repaired_source = "\n".join(lines) + "\n"
    compile(repaired_source, "DAY2_FINAL.ipynb::cell-13", "exec")
    _set_cell_source(cells[13], repaired_source)
    print("[DAY 2] Repaired all five Python 3.11-safe Scene 5 HTML expressions")
    return True

def _sync_repaired_notebook(payload: dict, notebook_path: Path) -> None:
    """Persist source repairs and the final data-slide replacement back to Drive."""
    if not os.environ.get("RCLONE_CONFIG"):
        print("[DAY 2] Drive sync skipped outside GitHub Actions")
        return

    cfg = read_json(CONFIG_PATH, "Day 2 config")
    drive_path = str(cfg["notebook"]["drive_path"])
    notebook_path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    subprocess.run(
        [
            "rclone",
            "copyto",
            str(notebook_path),
            f"vortex-drive:{drive_path}",
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    print(f"[DAY 2] Repaired source synced to My Drive/{drive_path}")


def _audit_notebook(payload: dict) -> None:
    cells = payload.get("cells") or []
    code_cells = [
        (index, _cell_source(cell))
        for index, cell in enumerate(cells)
        if cell.get("cell_type") == "code"
    ]
    if len(code_cells) != 21:
        raise RuntimeError(f"Expected 21 Day 2 code cells, found {len(code_cells)}")

    definition_locations: dict[str, list[int]] = collections.defaultdict(list)
    normalized_hashes: dict[str, list[int]] = collections.defaultdict(list)
    for index, source in code_cells:
        compile(source, f"DAY2_FINAL.ipynb::cell-{index}", "exec")
        tree = ast.parse(source, filename=f"DAY2_FINAL.ipynb::cell-{index}")
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                definition_locations[node.name].append(index)

        normalized = "\n".join(
            line.strip()
            for line in source.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
        if len(normalized) >= 160:
            normalized_hashes[
                hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            ].append(index)

    duplicate_defs = {
        name: indexes
        for name, indexes in definition_locations.items()
        if len(indexes) > 1
    }
    if duplicate_defs:
        raise RuntimeError(f"Duplicate top-level definitions found: {duplicate_defs}")

    duplicate_cells = [
        indexes for indexes in normalized_hashes.values() if len(indexes) > 1
    ]
    if duplicate_cells:
        raise RuntimeError(f"Duplicate whole-code cells found: {duplicate_cells}")

    all_source = "\n".join(source for _, source in code_cells)
    if DAY2_OUTPUT_ROOT not in all_source:
        raise RuntimeError("Day 2 output root contract is missing from the notebook")
    if "FPL_VORTEX_DAY2_FINAL.mp4" not in all_source:
        raise RuntimeError("Day 2 final MP4 contract is missing from the notebook")


def _patch_quality(payload: dict, quality: str, notebook_path: Path) -> None:
    quality = quality.upper()
    if quality not in {"DRAFT", "FINAL"}:
        raise ValueError(f"Unsupported render quality: {quality}")

    cells = payload.get("cells") or []
    if not cells or cells[0].get("cell_type") != "code":
        raise RuntimeError("Day 2 Cell 0 is missing")

    source = _cell_source(cells[0])
    replacement = f'MP4_QUALITY = "{quality}"\\g<suffix>'
    patched, count = QUALITY_PATTERN.subn(replacement, source, count=1)
    if count != 1:
        raise RuntimeError(
            "Could not apply the isolated GitHub Actions MP4_QUALITY override"
        )
    notebook_pattern = re.compile(
        r"(?m)^NOTEBOOK_JSON_PATH\s*=\s*['\"][^'\"]+['\"](?P<suffix>[^\n]*)$"
    )
    notebook_replacement = (
        f"NOTEBOOK_JSON_PATH = {str(notebook_path)!r}\\g<suffix>"
    )
    patched, notebook_count = notebook_pattern.subn(
        notebook_replacement, patched, count=1
    )
    if notebook_count != 1:
        raise RuntimeError(
            "Could not point the Day 2 self-audit at the ephemeral GitHub Actions notebook"
        )
    _set_cell_source(cells[0], patched)


def _install_colab_compat() -> None:
    """Provide only the Colab APIs used by the saved notebook."""
    try:
        import google  # type: ignore
    except ImportError:
        google = types.ModuleType("google")
        google.__path__ = []  # type: ignore[attr-defined]
        sys.modules["google"] = google

    colab = types.ModuleType("google.colab")
    drive = types.ModuleType("google.colab.drive")
    output = types.ModuleType("google.colab.output")
    userdata = types.ModuleType("google.colab.userdata")

    def mount(mountpoint, **kwargs):
        (Path(mountpoint) / "MyDrive").mkdir(parents=True, exist_ok=True)
        return None

    drive.mount = mount
    drive.flush_and_unmount = lambda: None
    output.serve_kernel_port_as_iframe = lambda *a, **k: None
    output.serve_kernel_port_as_window = lambda *a, **k: None
    userdata.get = lambda name: os.environ.get(str(name))
    colab.drive, colab.output, colab.userdata = drive, output, userdata
    sys.modules.update({
        "google.colab": colab,
        "google.colab.drive": drive,
        "google.colab.output": output,
        "google.colab.userdata": userdata,
    })


def execute_notebook(notebook_path: Path, quality: str) -> None:
    notebook_path = notebook_path.resolve()
    if not notebook_path.is_file():
        raise FileNotFoundError(f"Day 2 notebook is missing: {notebook_path}")

    payload = json.loads(notebook_path.read_text(encoding="utf-8"))
    repaired = _repair_python311_fstrings(payload)
    replaced = _replace_last_data_slide(payload)
    _audit_notebook(payload)
    if repaired or replaced:
        _sync_repaired_notebook(payload, notebook_path)
    _patch_quality(payload, quality, notebook_path)

    # The execution copy is ephemeral. Only the Python 3.11 source repair above
    # is synced back to Drive; workflow-only quality/path overrides stay local.
    notebook_path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    _audit_notebook(payload)

    code_cells = [
        (index, _cell_source(cell))
        for index, cell in enumerate(payload.get("cells") or [])
        if cell.get("cell_type") == "code"
    ]

    _install_colab_compat()
    shell = InteractiveShell.instance()
    shell.autoawait = True
    shell.user_ns["__name__"] = "__main__"

    print("[DAY 2] Notebook preflight: PASS")
    print(f"[DAY 2] Notebook: {notebook_path}")
    print(f"[DAY 2] Render quality override: {quality.upper()}")
    print(f"[DAY 2] Isolated output root: {DAY2_OUTPUT_ROOT}")

    for position, (cell_index, source) in enumerate(code_cells, start=1):
        label = next(
            (
                line.removeprefix("# ").strip()
                for line in source.splitlines()
                if "CELL " in line.upper()
            ),
            f"code cell {cell_index}",
        )
        print(
            f"::group::Day 2 cell {position:02d}/{len(code_cells)} — {label}",
            flush=True,
        )
        try:
            result = shell.run_cell(source, store_history=False, silent=False)
            if not result.success:
                raise RuntimeError(f"Day 2 execution failed in notebook cell {cell_index}")
        finally:
            print("::endgroup::", flush=True)

    print("[DAY 2] Notebook execution: PASS")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Execute the isolated Day 2 Colab notebook in GitHub Actions"
    )
    parser.add_argument("--notebook", type=Path, required=True)
    parser.add_argument("--quality", choices=("DRAFT", "FINAL"), default="FINAL")
    args = parser.parse_args()
    execute_notebook(args.notebook, args.quality)


if __name__ == "__main__":
    main()
