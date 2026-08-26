from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

import run_vortex_auto
import run_weekly_github
from vortex.drive_sync import validate_weekly_outputs
from vortex.official_fpl import _parquet_safe_frame


class NotebookPreflightTests(unittest.TestCase):
    def test_notebook_preflight_and_phase0_fix_are_unique(self) -> None:
        run_weekly_github._preflight_notebook()

        notebook = json.loads(run_weekly_github.NOTEBOOK.read_text(encoding="utf-8"))
        source = "\n".join(
            "".join(cell.get("source", []))
            for cell in notebook["cells"]
            if cell.get("cell_type") == "code"
        )
        self.assertEqual(source.count(run_weekly_github.PHASE0_REVIEW_NAME_GUARD), 1)
        self.assertEqual(source.count(run_weekly_github.PHASE0_REVIEW_CLEANUP_MARKER), 1)


class AutoUpdateTests(unittest.TestCase):
    def test_rich_feed_errors_are_not_reported_as_pass(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "refresh failed"):
            run_vortex_auto._validate_rich_feed(
                {"status": "ERROR", "error": "upstream unavailable"},
                object(),
            )

    def test_missing_current_export_is_an_error(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "no current-season export"):
            run_vortex_auto._validate_rich_feed({"status": "UPDATED"}, None)

    def test_github_run_requires_drive_secrets(self) -> None:
        with (
            mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}, clear=True),
            mock.patch.object(run_vortex_auto, "ensure_local_dirs"),
            mock.patch.object(
                run_vortex_auto,
                "refresh_rich_match_feed",
                return_value={"status": "UPDATED"},
            ),
            mock.patch.object(
                run_vortex_auto,
                "export_current_rich_data",
                return_value=Path("rich"),
            ),
            mock.patch.object(
                run_vortex_auto,
                "collect_official_fpl",
                return_value=Path("official"),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "OAuth secrets are required"):
                run_vortex_auto.run()


class DataSafetyTests(unittest.TestCase):
    def test_nested_fpl_values_are_serialised_for_parquet(self) -> None:
        frame = _parquet_safe_frame(
            [
                {"id": 1, "rules": {}, "tags": ["a", "b"]},
                {"id": 2, "rules": {"x": 1}, "tags": []},
            ]
        )
        self.assertIsInstance(frame, pd.DataFrame)
        self.assertEqual(frame.loc[0, "rules"], "{}")
        self.assertEqual(frame.loc[1, "tags"], "[]")

    def test_weekly_output_contract_accepts_one_complete_master(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            first_video = Path(temporary) / "MyDrive" / "FPL_VORTEX" / "FIRST VIDEO"
            for name in ("MP4", "MP3", "SLIDE", "DATA"):
                (first_video / name).mkdir(parents=True, exist_ok=True)

            mp4 = first_video / "MP4" / "FPL_VORTEX_COMBINED.mp4"
            mp3 = first_video / "MP3" / "FPL_VORTEX_COMBINED.mp3"
            slide = first_video / "SLIDE" / "intro.png"
            qa_file = first_video / "DATA" / "final_video_qa.json"
            for path in (mp4, mp3, slide):
                path.write_bytes(b"test")
            qa_file.write_text(
                json.dumps(
                    {
                        "passed": True,
                        "output_contract": {"passed": True},
                        "final_file": str(mp4),
                    }
                ),
                encoding="utf-8",
            )

            report = validate_weekly_outputs(temporary)
            self.assertEqual(report["final_mp4"], mp4.name)
            self.assertEqual(report["final_mp3"], mp3.name)


if __name__ == "__main__":
    unittest.main()
