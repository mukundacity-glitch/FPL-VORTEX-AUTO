from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from day3 import run_notebook as runner
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


class TransferDeskPacingTests(unittest.TestCase):
    def test_unvoiced_card_hold_is_shortened(self) -> None:
        source = 'sections.append({"silent_card_seconds": 4.0})'
        updated = runner._patch_transfer_desk_pacing(source)
        self.assertIn('"silent_card_seconds": 1.35}', updated)
        self.assertNotIn('"silent_card_seconds": 4.0}', updated)

    def test_pacing_rejects_unrecognized_notebook_source(self) -> None:
        with self.assertRaises(RuntimeError):
            runner._patch_transfer_desk_pacing('sections = []')


class ReviewOnlyChipTeaserTests(unittest.TestCase):
    SOURCE = (
        '    _next_review_gw=int(globals().get("NEXT_GW") or (int(REVIEW_GW)+1))\n'
        '    segments.append({\n'
        '        "id":"review_close",\n'
        '        "text":(\n'
        '            "The lesson is to judge decisions. "\n'
        '            + ("Next: elite ownership trends."\n'
        '               if True\n'
        '               else f"Next, we turn those lessons into the Gameweek {_next_review_gw} plan.")\n'
        '        ),\n'
        '    })\n'
    )

    def test_chip_teasers_are_after_the_review_closing_words(self) -> None:
        labels = {
            "WILDCARD": "Wildcard",
            "FREE_HIT": "Free Hit",
            "TRIPLE_CAPTAIN": "Triple Captain",
            "BENCH_BOOST": "Bench Boost",
        }
        for chip, label in labels.items():
            with self.subTest(chip=chip):
                with patch.dict(runner.os.environ, {"DAY3_PLAN": "NEITHER", "DAY3_REVIEW_CHIP": chip}):
                    modified = runner._patch_review_only_teaser(self.SOURCE)
                self.assertIn(f"I'm planning to play my {label} in Gameweek", modified)
                self.assertIn(' + " " + _vx_day3_review_teaser', modified)
                compile("def render_review():\n" + modified, "<review-only-teaser>", "exec")

    def test_review_only_no_chip_still_mentions_the_next_video(self) -> None:
        with patch.dict(runner.os.environ, {"DAY3_PLAN": "NEITHER", "DAY3_REVIEW_CHIP": "NONE"}):
            modified = runner._patch_review_only_teaser(self.SOURCE)
        self.assertIn("My Gameweek plan will be in my next video.", modified)
        self.assertNotIn("I'm planning to play my", modified)

    def test_roll_and_transfer_keep_original_narration(self) -> None:
        for plan in ("ROLL", "TRANSFER"):
            with self.subTest(plan=plan):
                with patch.dict(runner.os.environ, {"DAY3_PLAN": plan, "DAY3_REVIEW_CHIP": "WILDCARD"}):
                    self.assertEqual(runner._patch_review_only_teaser(self.SOURCE), self.SOURCE)

    def test_invalid_chip_selection_is_rejected(self) -> None:
        with patch.dict(runner.os.environ, {"DAY3_PLAN": "NEITHER", "DAY3_REVIEW_CHIP": "UNKNOWN"}):
            with self.assertRaises(ValueError):
                runner._patch_review_only_teaser(self.SOURCE)


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
            "_patch_official_player_portraits",
            "_patch_outro_social_icon",
            "_patch_tts_profile",
            "_patch_player_card_sample",
            "_patch_narration_tone",
            "_patch_review_only_teaser",
            "_patch_pitch_card_entrance",
            "_patch_pitch_player_names",
            "_patch_shared_card_scene_checks",
            "_patch_transfer_desk_pacing",
            "_patch_defcon_card_motion",
            "_patch_scene_polish",
            "_patch_desk_closing_audio",
            "_patch_transition_title_bounds",
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
