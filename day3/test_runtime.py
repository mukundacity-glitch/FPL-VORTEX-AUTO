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
            "_patch_tts_profile",
            "_patch_player_card_sample",
            "_patch_narration_tone",
            "_patch_pitch_card_entrance",
            "_patch_pitch_player_names",
            "_patch_shared_card_scene_checks",
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
