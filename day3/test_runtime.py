from __future__ import annotations

import json
import re
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from unittest.mock import patch

from day3 import run_notebook as runner
from day3.chip_plan import DAY3_CHIP_PLAN_SCENE, DAY3_PLANNED_CHIP_OPTIONS, chip_plan_copy
from day3.publish_private import _one_final_video


class FinalVideoSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.output_root = Path(self.temporary.name)
        self.video_directory = self.output_root / "MP4"
        self.video_directory.mkdir()

    def write_video(self, name: str, content: bytes = b"video") -> Path:
        path = self.video_directory / name
        path.write_bytes(content)
        return path

    def test_shortlist_scene_is_not_a_second_master(self) -> None:
        self.write_video("04_final_shortlists.mp4")
        self.write_video("gw_review.mp4")
        master = self.write_video("FPL_VORTEX_GW6_COMBINED_FINAL_2160P_24FPS.mp4")
        self.assertEqual(_one_final_video(self.output_root), master.resolve())

    def test_shortlist_scene_cannot_replace_a_missing_master(self) -> None:
        self.write_video("04_final_shortlists.mp4")
        with self.assertRaises(RuntimeError):
            _one_final_video(self.output_root)

    def test_empty_master_is_rejected(self) -> None:
        self.write_video("FPL_VORTEX_GW6_COMBINED_FINAL_2160P_24FPS.mp4", b"")
        with self.assertRaises(RuntimeError):
            _one_final_video(self.output_root)

    def test_multiple_masters_are_rejected(self) -> None:
        self.write_video("FPL_VORTEX_GW6_COMBINED_FINAL_2160P_24FPS.mp4")
        self.write_video("FPL_VORTEX_GW6_COMBINED_OLD.mp4")
        with self.assertRaises(RuntimeError):
            _one_final_video(self.output_root)


class DefconGameweekTests(unittest.TestCase):
    def test_partial_review_uses_latest_completed_gameweek(self) -> None:
        self.assertEqual(
            runner._resolve_defcon_history_gameweek([5, 5.0], [1, 2, 3, 4, 5], [1, 2, 3, 4, 5], 7),
            (5, [1, 2, 3, 4, 5]),
        )

    def test_final_review_advances_completed_cutoff(self) -> None:
        self.assertEqual(
            runner._resolve_defcon_history_gameweek([6, 6], [1, 2, 3, 4, 5, 6], [1, 2, 3, 4, 5, 6], 7),
            (6, [1, 2, 3, 4, 5, 6]),
        )

    def test_stale_player_features_are_rejected(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "latest completed GW 6"):
            runner._resolve_defcon_history_gameweek([5, 5], [1, 2, 3, 4, 5, 6], [1, 2, 3, 4, 5, 6], 7)

    def test_mixed_player_cutoffs_cannot_hide_behind_maximum(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "player feature Gameweeks"):
            runner._resolve_defcon_history_gameweek([4, 5], [1, 2, 3, 4, 5], [1, 2, 3, 4, 5], 7)

    def test_missing_model_gameweek_is_rejected_even_with_current_cutoff(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "model history"):
            runner._resolve_defcon_history_gameweek([5], [1, 2, 3, 4, 5], [1, 3, 4, 5], 7)

    def test_no_completed_history_is_rejected(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "no completed"):
            runner._resolve_defcon_history_gameweek([], [], [], 1)

    def test_invalid_gameweek_evidence_is_rejected(self) -> None:
        for invalid in (None, float("nan"), 5.5, True, "unknown", 39):
            with self.subTest(value=invalid), self.assertRaisesRegex(RuntimeError, "invalid Gameweek"):
                runner._resolve_defcon_history_gameweek([invalid], [1, 2, 3, 4, 5], [1, 2, 3, 4, 5], 7)


class Top10kTemplateNameTests(unittest.TestCase):
    def test_changing_bench_positions_do_not_become_player_names(self) -> None:
        for labels in (
            "GKP1. MID2. FWD3. MID",
            "GKP 1. DEF 2. MID 3. MID",
            "GKP 1. DEF 2. MID 3. FWD",
            "GK 1. FWD 2. DEF 3. MID",
            "GKP\n1.\nMID\n2.\nFWD\n3.\nMID",
        ):
            with self.subTest(labels=labels):
                self.assertEqual(runner._clean_top10k_template_name(f"{labels} Raya"), "Raya")

    def test_player_kit_labels_are_removed_before_bench_positions(self) -> None:
        self.assertEqual(
            runner._clean_top10k_template_name("Player Kit GKP1. MID2. FWD3. MID Player Kit João Pedro"),
            "João Pedro",
        )

    def test_real_names_preserve_accents_punctuation_and_multiple_words(self) -> None:
        for name in ("João Pedro", "Groß", "Calvert-Lewin", "B.Fernandes", "De Cuyper", "van Hecke", "Midtsjø", "Fwderson"):
            with self.subTest(name=name):
                self.assertEqual(runner._clean_top10k_template_name(name), name)

    def test_split_percentage_text_nodes_are_rejoined(self) -> None:
        self.assertEqual(
            runner._normalize_top10k_visible_text("Raya\r\nLEE (H)\n43\n%\n\nPalmer\n19.89 %"),
            "Raya\nLEE (H)\n43%\nPalmer\n19.89%",
        )

    def test_normalizer_preserves_captain_section_line_boundaries(self) -> None:
        self.assertEqual(
            runner._normalize_top10k_visible_text("Top 10k Captains\n João   Pedro \n17.4%\nGW6 Chip Usage"),
            "Top 10k Captains\nJoão Pedro\n17.4%\nGW6 Chip Usage",
        )


class NarrationExportTests(unittest.TestCase):
    # Keep the production export contract so tests exercise partial and final exports.
    export_source = r'''
from pathlib import Path
import json, os

VX_EXPORT_SCENES = {
 'gw_review': ('06_Gameweek_Review', 'gw_review_scene_4k'),
 '05a_my_plan_roll': ('05A_Gameweek_Plan_Roll', 'my_plan_5a_roll_scene_4k'),
 '05b_my_plan_transfer': ('05B_Transfer_Plan', 'my_plan_5b_transfer_scene_4k'),
 '05_outro': ('08_Outro', 'outro_scene_4k'),
}

def _vx_export_folder_guard():
    NARRATION_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    Path(SLIDE_DIR).mkdir(parents=True, exist_ok=True)

def _vx_remove_export_item(path):
    path.unlink()

def vx_export_narration_scripts(available_only=False):
    _vx_export_folder_guard()
    selected = list(globals().get('SELECTED_SCENES', []))
    if not selected:
        raise RuntimeError('Run Cell 0 to select slides before exporting narration')
    shared_path = VX_INTERNAL_DATA_DIR / 'other_scenes_narration.json'
    shared = json.loads(shared_path.read_text(encoding='utf-8')) if shared_path.is_file() else {}
    packages = {'gw_review': 'gw_review_package.json',
                '05a_my_plan_roll': 'my_plan_5a_roll_package.json',
                '05b_my_plan_transfer': 'my_plan_5b_transfer_package.json'}
    texts, missing = {}, []
    for key in selected:
        if key not in VX_EXPORT_SCENES:
            raise RuntimeError('Unknown selected slide: ' + key)
        text = None
        if key in packages:
            path = VX_INTERNAL_DATA_DIR / packages[key]
            if path.is_file():
                package = json.loads(path.read_text(encoding='utf-8'))
                segments = package.get('segments', [])
                if segments and all(isinstance(s.get('text'), str) and s['text'].strip() for s in segments):
                    text = '\n\n'.join(s['text'] for s in segments)
        else:
            text = shared.get(key, {}).get('script')
        if not isinstance(text, str) or not text.strip():
            missing.append(key)
            continue
        texts['Slide_' + VX_EXPORT_SCENES[key][0] + '.txt'] = text
    if missing and not available_only:
        raise RuntimeError('Narration not generated for: ' + ', '.join(missing))
    for filename, text in texts.items():
        target = NARRATION_EXPORT_DIR / filename
        pending = target.with_name('.' + filename + '.pending')
        try:
            pending.write_text(text, encoding='utf-8')
            if pending.read_text(encoding='utf-8') != text:
                raise RuntimeError('Narration write verification failed: ' + filename)
            os.replace(pending, target)
        finally:
            pending.unlink(missing_ok=True)
    keep = {'Slide_' + VX_EXPORT_SCENES[key][0] + '.txt' for key in selected}
    for path in list(NARRATION_EXPORT_DIR.iterdir()):
        if path.name not in keep:
            _vx_remove_export_item(path)
    return sorted(str(NARRATION_EXPORT_DIR / name) for name in texts)

def vx_finish_clean_exports():
    files = vx_export_narration_scripts(available_only=not bool(globals().get('VX_GENERATE_AUDIO', True)))
    keep = {VX_EXPORT_SCENES[key][1] + '.png' for key in SELECTED_SCENES}
    for path in list(Path(SLIDE_DIR).iterdir()):
        if path.name not in keep:
            _vx_remove_export_item(path)
    return files
'''

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.data_directory = self.root / "work" / "data"
        self.data_directory.mkdir(parents=True)
        self.export_directory = self.root / "output" / "DATA"
        self.slide_directory = self.root / "output" / "SLIDE"
        self.namespace: dict[str, Any] = {
            "VX_INTERNAL_DATA_DIR": self.data_directory,
            "NARRATION_EXPORT_DIR": self.export_directory,
            "SLIDE_DIR": self.slide_directory,
            "SELECTED_SCENES": [DAY3_CHIP_PLAN_SCENE],
        }
        exec(runner._patch_narration_exports(self.export_source), self.namespace)

    def write_package(self, filename: str, package: dict[str, Any]) -> None:
        (self.data_directory / filename).write_text(json.dumps(package), encoding="utf-8")

    def test_partial_export_waits_for_chip_renderer_and_keeps_review(self) -> None:
        self.namespace["SELECTED_SCENES"] = ["gw_review", DAY3_CHIP_PLAN_SCENE]
        self.write_package("gw_review_package.json", {"segments": [{"text": "Review narration."}]})
        files = self.namespace["vx_export_narration_scripts"](available_only=True)
        self.assertEqual(files, [str(self.export_directory / "Slide_06_Gameweek_Review.txt")])
        self.assertEqual(Path(files[0]).read_text(encoding="utf-8"), "Review narration.")

    def test_final_export_rejects_missing_chip_before_replacing_scripts(self) -> None:
        self.export_directory.mkdir(parents=True)
        existing = self.export_directory / "existing.txt"
        existing.write_text("Previous export", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "Narration not generated for: 05c_my_plan_chip"):
            self.namespace["vx_export_narration_scripts"]()
        self.assertEqual(existing.read_text(encoding="utf-8"), "Previous export")

    def test_all_planned_chips_export_the_exact_rendered_tts_text(self) -> None:
        for chip in DAY3_PLANNED_CHIP_OPTIONS:
            with self.subTest(chip=chip):
                text = chip_plan_copy(chip, 7)
                self.write_package("my_plan_chip_render.json", {"text": text, "passed": True})
                files = self.namespace["vx_export_narration_scripts"]()
                self.assertEqual(files, [str(self.export_directory / "Slide_05C_My_Plan_Chip.txt")])
                self.assertEqual(Path(files[0]).read_text(encoding="utf-8"), text)

    def test_review_roll_transfer_and_shared_narration_stay_exact(self) -> None:
        self.namespace["SELECTED_SCENES"] = [
            "gw_review", "05a_my_plan_roll", "05b_my_plan_transfer", "05_outro",
        ]
        for filename in (
            "gw_review_package.json", "my_plan_5a_roll_package.json", "my_plan_5b_transfer_package.json",
        ):
            self.write_package(filename, {"segments": [{"text": "First."}, {"text": "Second!"}]})
        self.write_package("other_scenes_narration.json", {"05_outro": {"script": "Goodbye!"}})
        files = self.namespace["vx_export_narration_scripts"]()
        self.assertEqual(len(files), 4)
        for filename in files:
            expected = "Goodbye!" if Path(filename).name == "Slide_08_Outro.txt" else "First.\n\nSecond!"
            self.assertEqual(Path(filename).read_text(encoding="utf-8"), expected)

    def test_final_cleanup_preserves_the_selected_chip_png(self) -> None:
        self.slide_directory.mkdir(parents=True)
        chip_png = self.slide_directory / "my_plan_chip_scene_4k.png"
        chip_png.write_bytes(b"selected chip PNG")
        unselected = self.slide_directory / "my_plan_5a_roll_scene_4k.png"
        unselected.write_bytes(b"unselected PNG")
        self.write_package("my_plan_chip_render.json", {"text": "Exact chip narration."})
        self.namespace["vx_finish_clean_exports"]()
        self.assertTrue(chip_png.exists())
        self.assertFalse(unselected.exists())

    def test_unknown_selected_scenes_still_fail_partial_export(self) -> None:
        self.namespace["SELECTED_SCENES"] = ["unknown_scene"]
        with self.assertRaisesRegex(RuntimeError, "Unknown selected slide: unknown_scene"):
            self.namespace["vx_export_narration_scripts"](available_only=True)

    def test_empty_chip_narration_still_fails_final_export(self) -> None:
        for text in (None, "", "   "):
            with self.subTest(text=text):
                self.write_package("my_plan_chip_render.json", {"text": text})
                with self.assertRaisesRegex(RuntimeError, "Narration not generated for: 05c_my_plan_chip"):
                    self.namespace["vx_export_narration_scripts"]()

    def test_changed_export_schema_fails_during_preflight(self) -> None:
        source = self.export_source.replace(" '05b_my_plan_transfer':", " 'renamed_transfer':", 1)
        with self.assertRaisesRegex(RuntimeError, "planned chip export registration"):
            runner._patch_narration_exports(source)


class PitchRevealTests(unittest.TestCase):
    def setUp(self) -> None:
        self.segments = [
            {"player_id": 1, "kind": "starter", "hero_off": 10.0},
            {"player_id": 229, "kind": "starter", "hero_off": 20.0},
            {"player_id": 2, "kind": "bench", "hero_off": 5.0},
        ]

    def test_pitch_stays_empty_through_first_hero_exit(self) -> None:
        for time in (0.0, 9.9, 10.0):
            self.assertEqual(runner._expected_pitch_player_count(self.segments, time, .42), 0)

    def test_cubic_entrance_is_visible_before_linear_halfway_cutoff(self) -> None:
        # At 25% elapsed, ease-out opacity is .578125, already above the DOM's .5.
        self.assertEqual(runner._expected_pitch_player_count(self.segments, 10.105, .42), 1)

    def test_dom_visibility_threshold_is_strict(self) -> None:
        self.assertEqual(runner._expected_pitch_player_count(self.segments, 10.08, .42), 0)
        self.assertEqual(runner._expected_pitch_player_count(self.segments, 10.09, .42), 1)

    def test_later_starter_and_bench_do_not_reveal_early(self) -> None:
        self.assertEqual(runner._expected_pitch_player_count(self.segments, 20.0, .42), 1)
        self.assertEqual(runner._expected_pitch_player_count(self.segments, 20.105, .42), 2)
        self.assertEqual(runner._expected_pitch_player_count(self.segments, 100.0, .42), 2)

    def test_changed_renderer_fails_preflight(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Pitch reveal audit"):
            runner._patch_pitch_reveal_audit("expected = 0")


class BenchingNarrationTests(unittest.TestCase):
    # Production's three factual sentence templates, with explicit test inputs.
    source = '''
def narrate(name, team, defender=True, multiple=False):
    lead = f"{name} from {team} leads the group."
    matchup, fdr, xpts = "Arsenal at home", 3.5, 4.2
    line = f"{lead} {matchup} is rated {fdr:.1f}, with a {xpts:.1f}-point projection."
    per_fixture = " per fixture" if multiple else ""
    if defender:
        cs = 43.0
        line += f" {team} have a {cs:.0f} percent clean-sheet chance{per_fixture}."
    else:
        goals = 1.7
        horizon = " across those fixtures" if multiple else ""
        line += f" {team} are expected to score {goals:.1f} goals{horizon}."
    return line
'''

    def setUp(self) -> None:
        namespace = {}
        exec(runner._patch_benching_narration(self.source), namespace)
        self.narrate = namespace["narrate"]

    def assert_no_duplicate_sentences(self, text: str) -> None:
        sentences = [part.strip().casefold() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]
        self.assertEqual(len(sentences), len(set(sentences)))

    def test_clubmate_defenders_keep_equal_probabilities_without_duplicate_prose(self) -> None:
        text = self.narrate("Senesi", "Bournemouth") + " " + self.narrate("Truffert", "Bournemouth")
        self.assert_no_duplicate_sentences(text)
        self.assertEqual(text.count("43 percent clean-sheet chance"), 2)
        for name in ("Senesi", "Truffert"):
            self.assertEqual(text.count(f"{name} from Bournemouth"), 1)
            self.assertIn(f"For {name}, Bournemouth have a 43 percent", text)

    def test_clubmate_attackers_keep_equal_goal_projections(self) -> None:
        text = self.narrate("Kluivert", "Bournemouth", False) + " " + self.narrate("Semenyo", "Bournemouth", False)
        self.assert_no_duplicate_sentences(text)
        self.assertEqual(text.count("expected to score 1.7 goals"), 2)

    def test_multiple_fixture_qualifiers_are_preserved(self) -> None:
        self.assertIn("43 percent clean-sheet chance per fixture.", self.narrate("Senesi", "Bournemouth", multiple=True))
        self.assertIn("1.7 goals across those fixtures.", self.narrate("Kluivert", "Bournemouth", False, True))

    def test_changed_templates_fail_preflight(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Benching narration"):
            runner._patch_benching_narration(self.source.replace("clean-sheet chance", "defensive outlook"))


class NotebookPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.notebook_path = self.root / "notebook.ipynb"
        self.patch_stack = ExitStack()
        self.addCleanup(self.patch_stack.close)
        for function_name in (
            "_patch_control_cell",
            "_patch_narration_exports",
            "_patch_outro_social_icon",
            "_patch_tts_profile",
            "_patch_player_card_sample",
            "_patch_narration_tone",
            "_patch_pitch_card_entrance",
            "_patch_pitch_reveal_audit",
            "_patch_benching_narration",
            "_patch_pitch_player_names",
            "_patch_shared_card_scene_checks",
            "_patch_defcon_gameweek_contract",
            "_patch_defcon_card_motion",
            "_patch_elite_template_parser",
            "patch_transfer_desk_source",
        ):
            self.patch_stack.enter_context(patch.object(runner, function_name, side_effect=lambda source: source))

    def write_notebook(self, overrides: dict[int, str]) -> None:
        cells = [
            {"cell_type": "code", "source": overrides.get(index, "pass\n")}
            for index in range(37)
        ]
        self.notebook_path.write_text(json.dumps({"cells": cells}), encoding="utf-8")

    def test_late_syntax_error_prevents_all_execution(self) -> None:
        marker = self.root / "executed.txt"
        self.write_notebook({
            0: f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n",
            36: "def unfinished(\n",
        })
        with patch.object(runner.InteractiveShell, "instance") as create_shell:
            with self.assertRaises(SyntaxError):
                runner.run_notebook(self.notebook_path)
        self.assertFalse(marker.exists())
        create_shell.assert_not_called()

    def test_preflight_supports_notebook_commands_and_async_cells(self) -> None:
        self.write_notebook({
            0: "import asyncio\nawait asyncio.sleep(0)\n",
            2: "!echo notebook-command\n",
        })
        self.assertEqual(len(runner._prepare_code_cells(self.notebook_path)), 37)


if __name__ == "__main__":
    unittest.main()
