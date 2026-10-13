"""Remove the Final Shortlists presentation while retaining shared model data."""

import ast
import shutil
from pathlib import Path


REMOVED_SCENE = "04_final_shortlists"
PATCHED_CELLS = {1, 2, 10, 22, 24, 32, 33, 34, 35}


def _cut(source, start, end):
    if source.count(start) != 1 or source.count(end) != 1:
        raise RuntimeError(f"Shortlist removal notebook contract changed: {start!r}")
    first, last = source.index(start), source.index(end)
    if last <= first:
        raise RuntimeError("Shortlist removal markers are out of order")
    return source[:first] + source[last:]


def _entry(node):
    if isinstance(node, ast.Constant):
        return node.value in {REMOVED_SCENE, "FINAL SHORTLISTS", "4 • FDR + PLAYER RECOMMENDATIONS"}
    if isinstance(node, (ast.Tuple, ast.List)) and node.elts:
        return _entry(node.elts[0])
    return False


class _RemovePresentation(ast.NodeTransformer):
    def visit_Dict(self, node):
        pairs = [(key, value) for key, value in zip(node.keys, node.values) if not _entry(key)]
        node.keys = [self.visit(key) if key is not None else None for key, _ in pairs]
        node.values = [self.visit(value) for _, value in pairs]
        return node

    def visit_List(self, node):
        node.elts = [self.visit(value) for value in node.elts if not _entry(value)]
        return node

    visit_Tuple = visit_List
    visit_Set = visit_List

    def visit_FunctionDef(self, node):
        if node.name.startswith("_d3_shortlist_"):
            return None
        return self.generic_visit(node)

    def visit_Assign(self, node):
        if any(isinstance(target, ast.Name) and target.id in {
            "FDR_PLAYER_RECOMMENDATIONS", "_D3_SHORTLIST_SECTION_SPECS"
        } for target in node.targets):
            return None
        return self.generic_visit(node)

    def visit_If(self, node):
        # Delete a scene-specific branch, preserving any following elif/else.
        test = node.test
        exclusive = isinstance(test, ast.Compare) and any(
            isinstance(value, ast.Constant) and value.value == REMOVED_SCENE
            for value in [test.left, *test.comparators]
        )
        exclusive |= any(isinstance(value, ast.Name) and value.id == "_d3_short_html"
                         for value in ast.walk(test))
        exclusive |= isinstance(test, ast.Call) and any(
            isinstance(value, ast.Constant) and value.value == REMOVED_SCENE
            for value in ast.walk(test)
        )
        if exclusive:
            remaining = []
            for statement in node.orelse:
                visited = self.visit(statement)
                if isinstance(visited, list):
                    remaining.extend(visited)
                elif visited is not None:
                    remaining.append(visited)
            return remaining
        return self.generic_visit(node)

    def visit_Expr(self, node):
        if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) and node.value.func.id == "print":
            if any(isinstance(value, ast.Constant) and isinstance(value.value, str)
                   and any(term in value.value.lower() for term in ("final shortlists", "final_shortlists"))
                   for value in ast.walk(node.value)):
                return None
        return self.generic_visit(node)


def remove_shortlist_cell(cell_index, source):
    if cell_index not in PATCHED_CELLS:
        return source
    if cell_index == 24:
        # The desk uses the Phase-10 aggregates and fixture lookup, but does not
        # need shortlist eligibility, position ranking, HTML, PNG or manifest.
        source = _cut(source, "DAY3_SHORTLIST_MIN_XMINS =", "_d3_short_fdr_frame = SLIDE_FDR_TABLE.copy()")
        source = _cut(source, "def _d3_short_fdr_html(team_id, position):", "# SLIDE 4 — TRANSFER DECISION DESK")
        for old, new in (
            ("_d3_short_agg", "_d3_desk_projection_input"),
            ("_d3_short_src", "_d3_desk_projection_source"),
            ("_d3_short_missing", "_d3_desk_projection_missing"),
            ("_d3_short_numeric", "_d3_desk_projection_numeric"),
            ("_D3_SHORT_REQUIRED", "_D3_DESK_PROJECTION_REQUIRED"),
            ("_d3_short_fdr_frame", "_d3_desk_fdr_frame"),
            ("_d3_short_next_fdr", "_d3_desk_next_fdr"),
            ("Day 3_3 Final Shortlists", "Day 3 Transfer Desk projections"),
        ):
            source = source.replace(old, new)
    if cell_index == 32:
        if source.count("Next, we move to the player shortlist.") != 1:
            raise RuntimeError("Shortlist removal needs the DEFCON closing transition")
        source = source.replace("Next, we move to the player shortlist.", "Next, we move to the transfer decision desk.")
    if cell_index == 33:
        source = _cut(source, '            if(VX.key==="04_final_shortlists"){',
                      '            if(VX.key==="03_projected_goals" && h.team_id!=null')
        source = _cut(source, "        /* Player Shortlist:", "        /* Transfer Decision Desk")
    if cell_index == 34:
        source = source.replace("                    shortlist: vx.shortlist_focus || null,\n", "")
        source = _cut(source, '            shortlist = vx_timing.get("shortlist") or {}',
                      "            times.update(required_times)")
    tree = _RemovePresentation().visit(ast.parse(source))
    ast.fix_missing_locations(tree)
    result = ast.unparse(tree) + "\n"
    if REMOVED_SCENE in result or "DAY3_SHORTLISTS" in result or "SHORTLIST_NARRATION_CUES" in result:
        raise RuntimeError(f"Final Shortlists presentation survived in cell {cell_index}")
    return result


def purge_shortlist_artifacts(output_root, work_root):
    """Remove only this deleted scene's stale files during a real run."""
    for root in (Path(output_root), Path(work_root)):
        for folder in ("SLIDE", "DATA", "DATA/slide_layout", "MP3", "MP4", "html", "html/animated", "data", "data/slide_layout", "mp3", "mp4", "preview", "video", "video/cell20_keyframes"):
            directory = root / folder
            if not directory.is_dir():
                continue
            for pattern in ("final_shortlists*", "04_final_shortlists*", "Slide_04_Player_Shortlist*", "04_Player_Shortlist*"):
                for path in directory.glob(pattern):
                    if path.is_file() or path.is_symlink():
                        path.unlink()
                    elif path.is_dir():
                        shutil.rmtree(path)
