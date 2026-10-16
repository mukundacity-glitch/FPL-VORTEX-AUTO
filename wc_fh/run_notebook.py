from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import sys
import traceback
import types
from pathlib import Path
from typing import Any

from .catalog import build_catalog, save_catalog
from .controls import (CHIPS, POSITIONS, TASKS, chip_slug, controls_for_chip,
                       request_controls, requested_chips, save_selection, selected_squad)
from .render_qa import close_renderer, verify_exported_visuals


def replace_assignments(source: str, replacements: dict[str, Any]) -> str:
    lines = source.splitlines(keepends=True)
    found: set[str] = set()
    changes = []
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in replacements:
                if name in found:
                    raise ValueError(f"Ambiguous notebook assignment: {name}")
                found.add(name)
                changes.append((node.lineno - 1, node.end_lineno, f"{name} = {replacements[name]!r}\n"))
    missing = replacements.keys() - found
    if missing:
        raise ValueError("Notebook control contract changed; missing assignments: " + ", ".join(sorted(missing)))
    for start, stop, replacement in reversed(changes):
        lines[start:stop] = [replacement]
    return "".join(lines)


def patch_setup(source: str, controls: dict[str, Any]) -> str:
    source = replace_assignments(source, dict(VX_VIDEO_MODE="Final", VX_CHIPS="Both",
        VX_SELECTION_MODE="Auto", VX_FINAL_FPS=24, VX_RENDER_VIDEOS=True,
        VX_INTRO_PREVIEW_ONLY=False, VX_INCLUDE_POPUPS=True, VX_BACKGROUND_MUSIC_VOLUME=0.08))
    lines = source.splitlines(keepends=True)
    functions = [node for node in ast.parse(source).body
                 if isinstance(node, ast.FunctionDef) and node.name == "configure_outputs"]
    if len(functions) != 1:
        raise ValueError("Notebook configure_outputs contract changed.")
    node = functions[0]
    replacement = '''def configure_outputs():
    root = Path(os.environ["WC_FH_OUTPUT_ROOT"]).resolve()
    work = Path(os.environ["RUNNER_TEMP"]) / "wc-fh-work"
    analysis = root / "analysis"
    for directory in (root / "mp4", root / "data", root / "slide", work, analysis):
        directory.mkdir(parents=True, exist_ok=True)
    return root, work, analysis
'''
    lines[node.lineno - 1:node.end_lineno] = [replacement]
    return "".join(lines)


def patch_render(source: str) -> str:
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    previews = [node for node in tree.body if isinstance(node, ast.If)
                and any(isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
                        and isinstance(child.func.value, ast.Name) and child.func.value.id == "VXWV"
                        and child.func.attr == "verify_visuals" for child in ast.walk(node))]
    if len(previews) != 1:
        raise ValueError("Notebook visual preflight contract changed; expected one early Wildcard preview block.")
    node = previews[0]
    replacement = '''if VX_GENERATE_WILDCARD:
    for key in (key for key in VX_DATA_CONTRACTS if key.startswith('wildcard')):
        scenes = VX_SCENE_MANIFESTS[key]
        VXWC.validate_scene_json(VX_WILDCARD_SCENE_JSONS[key])
        VX_WILDCARD_PREVIEW = None
        script_path = VX_NARRATION_PATHS.get(key)
        if script_path is None or not Path(script_path).is_file() or key not in VX_NARRATION_OBJECTS:
            raise RuntimeError(f"{key}: validated Scene-JSON narration is missing; rerun Cells 12 and 13")
        narration = decode_script(Path(script_path).read_text(encoding='utf-8'))
        VXWC.validate_render_script(VX_NARRATION_OBJECTS[key], narration)
        VX_NARRATION[key] = narration
        for scene, segment in zip(scenes, narration):
            scene['narration_text'] = segment['text']
        (VX_ANALYSIS_ROOT / (key + '_scene_manifest.json')).write_text(json.dumps(scenes, indent=2, ensure_ascii=False))
        print(f"{key}: scene data and narration validated; visual QA follows measured audio", flush=True)
'''
    changes = [(node.lineno - 1, node.end_lineno, replacement)]
    for name in ("_vx_render_verified_wildcard", "_vx_render_verified_freehit"):
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name]
        if len(functions) != 1:
            raise ValueError(f"Notebook measured-render contract changed: {name}")
        calls = [node for node in ast.walk(functions[0]) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == "vx_visual_from_manifest"]
        if len(calls) != 1 or len(calls[0].args) != 4 or calls[0].keywords:
            raise ValueError(f"Notebook visual factory contract changed: {name}")
        call = calls[0]
        argument_sources = [ast.get_source_segment(source, argument) for argument in call.args]
        if any(argument is None for argument in argument_sources):
            raise ValueError(f"Notebook visual factory arguments changed: {name}")
        arguments = ", ".join(argument for argument in argument_sources if argument is not None)
        # The existing factory call is downstream of PCM measurement in both native render functions.
        line = lines[call.lineno - 1]
        expression = ast.get_source_segment(source, call)
        if call.lineno != call.end_lineno or expression is None:
            raise ValueError(f"Notebook visual factory call shape changed: {name}")
        changes.append((call.lineno - 1, call.end_lineno,
                        line.replace(expression, f"_wc_fh_verified_visual(vx_visual_from_manifest, {arguments})", 1)))
    for start, stop, replacement in sorted(changes, reverse=True):
        lines[start:stop] = [replacement]
    return "from wc_fh.render_qa import verified_visual as _wc_fh_verified_visual\n" + "".join(lines)


def prepare_cells(notebook: dict[str, Any], controls: dict[str, Any]) -> list[tuple[int, str, str]]:
    from IPython.core.inputtransformer2 import TransformerManager

    prepared = []
    seen = set()
    transformer = TransformerManager()
    for index, cell in enumerate(notebook.get("cells", [])):
        if cell.get("cell_type") != "code":
            continue
        raw = cell.get("source", "")
        source = "".join(raw) if isinstance(raw, list) else str(raw)
        title = re.search(r"^#@title .*?CELL\s+(\d+[A-Z]?)\s*[-–]", source, re.MULTILINE)
        if not title:
            continue
        label = title.group(1)
        if label == "16":
            continue
        if label == "0":
            source = patch_setup(source, controls)
        if label == "11":
            lines = source.splitlines(keepends=True)
            function = next(node for node in ast.parse(source).body
                            if isinstance(node, ast.FunctionDef) and node.name == "vx_static_plan_score")
            position = function.body[0].end_lineno
            lines[position:position] = ['''    fixed = globals().get("_VX_WC_FH_MANUAL_FREEHIT_PLAN")
    if fixed and set(map(int, squad.player_id)) == {player["id"] for player in fixed["squad"]}:
        from wildcard_slide3 import weekly_plan_rows
        return pd.DataFrame(weekly_plan_rows(fixed, squad, gw_scores_source))
''']
            source = "".join(lines)
        if label == "12":
            marker = "\n\n# Reject missing/stale selections"
            if source.count(marker) != 1:
                raise ValueError("The scene-preparation reload contract changed.")
            source = source.replace(marker, "\nfrom wc_fh.manual_plans import install_manual_narration\ninstall_manual_narration()\n" + marker, 1)
        if label == "14":
            source = patch_render(source)
            # Colab's extra viewer calls .draw() on a Remotion renderer; delivery QA already verifies the 4K exports.
            lines = source.splitlines(keepends=True)
            for node in reversed(ast.parse(source).body):
                if isinstance(node, ast.If):
                    condition = ast.get_source_segment(source, node.test) or ""
                    if "VX_GENERATE_FREEHIT" in condition and "VX_VIDEO_MODE=='Final'" in condition.replace(" ", ""):
                        lines[node.lineno - 1:node.end_lineno] = ["# Colab-only duplicate preview skipped in the headless runner.\n"]
            source = "".join(lines)
        compile(transformer.transform_cell(source), f"wc-fh:cell-{index}", "exec",
                flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
        prepared.append((index, label, source))
        seen.add(label)
    required = {"0", "1", "2", "8", "10", "11", "12", "13", "14", "15", "17"}
    if required - seen:
        raise ValueError(f"WC/FH notebook is missing required production cells: {sorted(required-seen)}")
    return prepared


def install_colab_compatibility() -> None:
    try:
        import google
    except ModuleNotFoundError as error:
        if error.name != "google":
            raise
        google = types.ModuleType("google")
        google.__path__ = []
        sys.modules["google"] = google

    colab = types.ModuleType("google.colab")
    colab.__path__ = []
    drive = types.ModuleType("google.colab.drive")
    userdata = types.ModuleType("google.colab.userdata")
    output = types.ModuleType("google.colab.output")
    def mount(path: str, **_: Any) -> None:
        (Path(path) / "MyDrive").mkdir(parents=True, exist_ok=True)
    drive.mount = mount
    userdata.get = lambda name: os.environ[name]
    output.enable_custom_widget_manager = lambda: None
    colab.drive, colab.userdata, colab.output = drive, userdata, output
    google.colab = colab
    for module in (colab, drive, userdata, output):
        sys.modules[module.__name__] = module


def catalog_from_namespace(namespace: dict[str, Any]) -> dict[str, Any]:
    squads = {chip: namespace[f"VX_{'WILDCARD' if chip == 'Wildcard' else 'FREEHIT'}_AUTO_SQUAD"]
              .player_id.astype(int).tolist() for chip in CHIPS}
    return build_catalog(namespace["_bootstrap"], namespace["_VX_GW_SCORES"].to_dict("records"),
                         squads, budget=float(namespace["VX_BUDGET"]))


def select_chip(namespace: dict[str, Any], controls: dict[str, Any], catalog: dict[str, Any], root: Path) -> None:
    chips = requested_chips(controls)
    manual = controls["selection"] == "Manual"
    scoped = {chip: controls_for_chip(controls, chip) for chip in chips}
    # Validate both squads before installing either manual plan.
    chosen = {chip: selected_squad(catalog, scoped[chip])[0] for chip in chips} if manual else {}
    namespace.update(VX_CHIPS=controls["chip"], VX_GENERATE_CHIPS=controls["chip"],
        VX_GENERATE_WILDCARD="Wildcard" in chips, VX_GENERATE_FREEHIT="Free Hit" in chips,
        VX_SELECTION_MODE=controls["selection"],
        VX_WILDCARD_SELECTION_MODE=controls["selection"], VX_FREEHIT_SELECTION_MODE=controls["selection"],
        USE_MANUAL_WILDCARD=manual and "Wildcard" in chips, USE_MANUAL_FREEHIT=manual and "Free Hit" in chips,
        VX_MANUAL_SELECTION_SAVED=True, VX_CELL0C_COMPLETE=True)
    if manual:
        players = {player["id"]: player for player in catalog["players"]}
        snapshot: dict[str, Any] = {}
        for chip in chips:
            save_selection(catalog, scoped[chip], root)
            prefix = "WC" if chip == "Wildcard" else "FH"
            for position, count in POSITIONS:
                ids = [player_id for player_id in chosen[chip] if players[player_id]["position"] == position]
                if len(ids) != count:
                    raise ValueError("Incorrect manual position count")
                for index, player_id in enumerate(ids, 1):
                    player = players[player_id]
                    snapshot[f"{prefix}_{position}{index}"] = (
                        f"{player['name']} — ID {player_id} — £{player['price_tenths']/10:.1f}m — "
                        f"{player['scores'][chip]:.2f} xPts")
            snapshot.update({f"{prefix}_CAPTAIN": "", f"{prefix}_VICE": ""})
        namespace["VX_MANUAL_SAVED_VALUES"] = snapshot
        if "VX_WC_ENGINE" in namespace:
            from .manual_plans import build_manual_plan
            for chip in chips:
                build_manual_plan(namespace, chip, chosen[chip])


def validate_delivery(manifest: dict[str, Any], controls: dict[str, Any]) -> None:
    expected = {chip_slug(chip) + "_" + controls["selection"].lower() for chip in requested_chips(controls)}
    if set(manifest) != expected or any(manifest[key].get("mode") != "Final" for key in expected):
        raise RuntimeError("Rendered output does not match the selected chips and selection mode.")


def execute(notebook_path: Path, *, validate_only: bool = False) -> None:
    controls = request_controls()
    notebook_bytes = notebook_path.read_bytes()
    cells = prepare_cells(json.loads(notebook_bytes), controls)
    print(f"WC- FH preflight: {len(cells)} cells compile; Final / 3840×2160 / 24fps", flush=True)
    if validate_only:
        return
    from IPython.core.interactiveshell import InteractiveShell
    from .drive_io import output_root

    root = output_root()
    root.mkdir(parents=True, exist_ok=True)
    install_colab_compatibility()
    shell = InteractiveShell.instance()
    shell.autoawait = True
    shell.user_ns.update(__name__="__main__", __file__=str(notebook_path))
    run_metadata = dict(notebook_sha256=hashlib.sha256(notebook_bytes).hexdigest(),
                        chip=controls["chip"], selection=controls["selection"], mode="Final")
    (root / "source.json").write_text(json.dumps(run_metadata, indent=2))
    def run_cell(index: int, label: str, source: str) -> None:
        print(f"\n▶ WC- FH CELL {label} (notebook index {index})", flush=True)
        result = shell.run_cell(source, store_history=False, silent=False)
        error = result.error_before_exec or result.error_in_exec
        if error is not None:
            (root / "runtime_error.txt").write_text("".join(traceback.format_exception(error)), encoding="utf-8")
            raise RuntimeError(f"WC- FH cell {label} failed") from error
    try:
        for index, label, source in cells:
            run_cell(index, label, source)
            if label == "0":
                if shell.user_ns["VX_VIDEO_MODE"] != "Final" or shell.user_ns["VX_VIDEO_PROFILE"]["height"] != 2160:
                    raise RuntimeError("WC- FH only permits Final 4K rendering.")
            if label == "11":
                catalog = catalog_from_namespace(shell.user_ns)
                save_catalog(catalog, root / "picker")
                if controls["task"] == TASKS[1]:
                    print("WC- FH: refreshed both player lists and live-cost picker; no video upload requested.")
                    return
                select_chip(shell.user_ns, controls, catalog, root)
                run_cell(index, label, source)
                if controls["selection"] == "Auto":
                    squads = {chip: shell.user_ns[f"VX_{'WILDCARD' if chip == 'Wildcard' else 'FREEHIT'}_AUTO_SQUAD"]
                              .player_id.astype(int).tolist() for chip in requested_chips(controls)}
                    actual = build_catalog(shell.user_ns["_bootstrap"], shell.user_ns["_VX_GW_SCORES"].to_dict("records"),
                                           squads, budget=float(shell.user_ns["VX_BUDGET"]))
                    for chip in requested_chips(controls):
                        save_selection(actual, controls_for_chip(controls, chip), root)
        manifest = json.loads((root / "delivery_manifest.json").read_text(encoding="utf-8"))
        validate_delivery(manifest, controls)
        verify_exported_visuals(root, manifest)
        print(f"WC- FH: verified {controls['chip']} / {controls['selection']} / Final 4K", flush=True)
    finally:
        active_error = sys.exc_info()[1]
        try:
            close_renderer(shell.user_ns)
        except Exception:
            if active_error is None:
                raise
            traceback.print_exc()


def main() -> None:
    parser = argparse.ArgumentParser(description="Execute the existing WC/FH notebook headlessly in Final mode.")
    parser.add_argument("notebook", type=Path)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    execute(args.notebook.resolve(), validate_only=args.validate_only)


if __name__ == "__main__":
    main()
