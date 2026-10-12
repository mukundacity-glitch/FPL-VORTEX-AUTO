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
            "_patch_outro_social_icon",
            "_patch_tts_profile",
            "_patch_player_card_sample",
            "_patch_narration_tone",
            "_patch_pitch_card_entrance",
            "_patch_pitch_player_names",
            "_patch_shared_card_scene_checks",
            "_patch_defcon_gameweek_contract",
            "_patch_defcon_card_motion",
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
