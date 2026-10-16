from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wc_fh.catalog import build_catalog, player_label
from wc_fh.controls import (AUTO, CHIPS, CHIP_OPTIONS, POSITIONS, SLOTS, chip_slug, controls_for_chip,
                            request_controls, requested_chips, selected_squad)
from PIL import Image

from wc_fh.publish_private import build_package, build_packages, metadata, publish
from wc_fh.run_notebook import install_colab_compatibility, patch_setup, select_chip, validate_delivery
from wc_fh.manual_plans import choose_lineup


def fixture_catalog() -> dict:
    players = []
    player_id = 1
    for position, count in POSITIONS:
        for _ in range(count):
            players.append(dict(id=player_id, name=f"Player {player_id}", club=f"Club {player_id%10}",
                club_id=player_id % 10, position=position, price_tenths=40 + player_id,
                status="a", scores={"Wildcard": float(player_id), "Free Hit": player_id / 6.0}))
            player_id += 1
    return dict(version=1, players=players, recommended={chip: list(range(1, 16)) for chip in CHIPS},
                gameweek=7, generated_at="2026-10-16T13:00:00Z", source="fixture", budget_millions=100.0)


class IsolatedTestCase(unittest.TestCase):
    def setUp(self):
        summary = patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": ""})
        summary.start()
        self.addCleanup(summary.stop)
        output = contextlib.redirect_stdout(io.StringIO())
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)


class SquadControlTests(IsolatedTestCase):
    def test_all_six_chip_selection_combinations_and_current_cost(self):
        catalog = fixture_catalog()
        for chip in CHIP_OPTIONS:
            for selection in ("Auto", "Manual"):
                with self.subTest(chip=chip, selection=selection):
                    controls = request_controls(dict(WC_FH_CHIP=chip, WC_FH_SELECTION=selection))
                    for selected_chip in requested_chips(controls):
                        ids, total = selected_squad(catalog, controls_for_chip(controls, selected_chip))
                        self.assertEqual(ids, list(range(1, 16)))
                        self.assertEqual(total, sum(range(41, 56)))
                    with tempfile.TemporaryDirectory() as directory:
                        namespace = {}
                        select_chip(namespace, controls, catalog, Path(directory))
                        self.assertEqual(namespace["VX_GENERATE_CHIPS"], chip)
                        self.assertEqual(namespace["VX_GENERATE_WILDCARD"], chip in ("Wildcard", "Both"))
                        self.assertEqual(namespace["VX_GENERATE_FREEHIT"], chip in ("Free Hit", "Both"))
                        self.assertEqual(namespace["USE_MANUAL_WILDCARD"], chip in ("Wildcard", "Both") and selection == "Manual")
                        self.assertEqual(namespace["USE_MANUAL_FREEHIT"], chip in ("Free Hit", "Both") and selection == "Manual")
                        if selection == "Manual":
                            for selected_chip in requested_chips(controls):
                                prefix = "WC" if selected_chip == "Wildcard" else "FH"
                                self.assertIn(" — ID 1 — ", namespace["VX_MANUAL_SAVED_VALUES"][f"{prefix}_GK1"])
                            self.assertTrue(namespace["VX_MANUAL_SELECTION_SAVED"])

    def test_both_manual_teams_keep_separate_players_costs_and_native_plans(self):
        catalog = fixture_catalog()
        catalog["players"].append(dict(catalog["players"][-1], id=16, name="My Free Hit forward", price_tenths=70))
        wildcard = list(range(1, 16))
        free_hit = list(range(1, 15)) + [16]
        controls = request_controls(dict(WC_FH_CHIP="Both", WC_FH_SELECTION="Manual", WC_FH_INPUTS=json.dumps(
            dict(wildcard_squad=",".join(map(str, wildcard)), free_hit_squad=",".join(map(str, free_hit)),
                 manual_squad="old import is overridden"))))
        with tempfile.TemporaryDirectory() as directory, patch("wc_fh.manual_plans.build_manual_plan") as build:
            root = Path(directory)
            namespace = {"VX_WC_ENGINE": object()}
            select_chip(namespace, controls, catalog, root)
            self.assertEqual([call.args[1:] for call in build.call_args_list], [("Wildcard", wildcard), ("Free Hit", free_hit)])
            snapshots = namespace["VX_MANUAL_SAVED_VALUES"]
            self.assertIn("ID 15", snapshots["WC_FWD3"])
            self.assertIn("ID 16", snapshots["FH_FWD3"])
            wc = json.loads((root / "data" / "selection_wildcard.json").read_text())
            fh = json.loads((root / "data" / "selection_free_hit.json").read_text())
            self.assertEqual(wc["selected_ids"], wildcard)
            self.assertEqual(fh["selected_ids"], free_hit)
            self.assertAlmostEqual(fh["total_cost_millions"] - wc["total_cost_millions"], 1.5)
            self.assertFalse((root / "data" / "selection.json").exists())

    def test_both_manual_falls_back_per_chip_and_ignores_unselected_imports(self):
        catalog = fixture_catalog()
        catalog["players"].append(dict(catalog["players"][-1], id=16, name="Alternate forward"))
        catalog["recommended"]["Free Hit"][-1] = 16
        controls = request_controls(dict(WC_FH_CHIP="Both", WC_FH_SELECTION="Manual"))
        for chip, expected in (("Wildcard", 15), ("Free Hit", 16)):
            self.assertEqual(selected_squad(catalog, controls_for_chip(controls, chip))[0][-1], expected)
        imported = ",".join(map(str, range(1, 16)))
        controls = request_controls(dict(WC_FH_CHIP="Both", WC_FH_SELECTION="Manual", WC_FH_INPUTS=json.dumps(
            dict(wildcard_squad=imported))))
        self.assertEqual(selected_squad(catalog, controls_for_chip(controls, "Wildcard"))[0][-1], 15)
        self.assertEqual(selected_squad(catalog, controls_for_chip(controls, "Free Hit"))[0][-1], 16)
        request_controls(dict(WC_FH_CHIP="Wildcard", WC_FH_SELECTION="Manual", WC_FH_INPUTS=json.dumps(
            dict(wildcard_squad=imported, free_hit_squad="unused old import"))))
        request_controls(dict(WC_FH_CHIP="Both", WC_FH_SELECTION="Auto", WC_FH_INPUTS=json.dumps(
            dict(wildcard_squad="unused old import", free_hit_squad="unused old import"))))
        with self.assertRaisesRegex(ValueError, "free_hit_squad"):
            request_controls(dict(WC_FH_CHIP="Both", WC_FH_SELECTION="Manual", WC_FH_INPUTS=json.dumps(
                dict(wildcard_squad=imported, free_hit_squad="bad import"))))

    def test_imported_ids_are_ordered_by_position(self):
        controls = request_controls(dict(WC_FH_SELECTION="Manual", WC_FH_INPUTS=json.dumps(
            dict(manual_squad=",".join(map(str, reversed(range(1, 16))))))))
        ids, _ = selected_squad(fixture_catalog(), controls)
        self.assertEqual(len(ids), 15)
        self.assertEqual(set(ids[:2]), {1, 2})
        self.assertEqual(set(ids[-3:]), {13, 14, 15})

    def test_duplicate_unknown_and_wrong_position_fail_before_render(self):
        for overrides in ({"gk_1": "Player — ID 2 — £4.2m"}, {"gk_1": "Player — ID 999 — £4.2m"},
                          {"gk_1": "Player — ID 3 — £4.3m"}):
            with self.subTest(overrides=overrides):
                controls = request_controls(dict(WC_FH_SELECTION="Manual", WC_FH_INPUTS=json.dumps(overrides)))
                with self.assertRaises(ValueError):
                    selected_squad(fixture_catalog(), controls)

    def test_illegal_club_count_fails(self):
        catalog = fixture_catalog()
        for player in catalog["players"][:4]:
            player["club_id"] = 99
        with self.assertRaisesRegex(ValueError, "three players"):
            selected_squad(catalog, request_controls({}))

    def test_auto_ignores_old_manual_import(self):
        controls = request_controls(dict(WC_FH_SELECTION="Auto", WC_FH_INPUTS='{"manual_squad":"bad old data"}'))
        self.assertEqual(selected_squad(fixture_catalog(), controls)[0], list(range(1, 16)))

    def test_rank_labels_include_both_recommendations_price_and_id(self):
        catalog = fixture_catalog()
        label = player_label(catalog["players"][0], catalog["recommended"])
        self.assertTrue(label.startswith("★ WC/FH"))
        self.assertIn("ID 1", label)
        self.assertIn("£4.1m", label)

    def test_catalog_uses_six_week_wildcard_and_single_week_freehit(self):
        fixture = fixture_catalog()
        bootstrap = dict(teams=[dict(id=index, short_name=f"C{index}") for index in range(10)],
                         elements=[dict(id=player["id"], web_name=player["name"], team=player["club_id"],
                                        element_type={"GK": 1, "DEF": 2, "MID": 3, "FWD": 4}[player["position"]],
                                        now_cost=player["price_tenths"]) for player in fixture["players"]])
        rows = [dict(player_id=player_id, gameweek=gameweek, gameweek_expected_points=2.0)
                for player_id in range(1, 16) for gameweek in range(7, 18)]
        catalog = build_catalog(bootstrap, rows, fixture["recommended"])
        self.assertEqual(catalog["players"][0]["scores"], {"Wildcard": 12.0, "Free Hit": 2.0})


class NotebookTests(IsolatedTestCase):
    def test_both_delivery_requires_two_matching_final_videos(self):
        for selection in ("Auto", "Manual"):
            controls = request_controls(dict(WC_FH_CHIP="Both", WC_FH_SELECTION=selection))
            manifest = {f"{chip_slug(chip)}_{selection.lower()}": dict(mode="Final") for chip in CHIPS}
            validate_delivery(manifest, controls)
            with self.assertRaises(RuntimeError):
                validate_delivery({next(iter(manifest)): dict(mode="Final")}, controls)
            manifest[next(iter(manifest))]["mode"] = "Draft"
            with self.assertRaises(RuntimeError):
                validate_delivery(manifest, controls)

    def test_manual_xi_keeps_the_replacement_and_does_not_restore_an_auto_player(self):
        cards = [dict(player, weekly_points={7: float(player["id"])}) for player in fixture_catalog()["players"]]
        cards[-1] = dict(cards[-1], id=99, name="My manual replacement", weekly_points={7: 15.0})
        row = choose_lineup(cards, 7)
        ids = {player["id"] for player in row["starting_xi"]}
        self.assertIn(99, ids)
        self.assertNotIn(15, ids)
        self.assertEqual(len(ids), 11)
        self.assertEqual({player["id"] for player in row["all_players"]}, {player["id"] for player in cards})

    def test_manual_lineup_never_invents_missing_player_points(self):
        cards = [dict(player, weekly_points={7: float(player["id"])}) for player in fixture_catalog()["players"]]
        for player in cards[:2]:
            player["weekly_points"][7] = None
        with self.assertRaisesRegex(ValueError, "verified projections"):
            choose_lineup(cards, 7)

    def test_colab_compatibility_can_mount_locally_without_google_signin(self):
        install_colab_compatibility()
        import google.colab
        with tempfile.TemporaryDirectory() as directory:
            google.colab.drive.mount(directory)
            self.assertTrue((Path(directory) / "MyDrive").is_dir())

    def test_final_controls_cannot_inherit_draft_or_manual_widget_defaults(self):
        source = '''VX_VIDEO_MODE = "Draft"
VX_CHIPS = "Free Hit"
VX_SELECTION_MODE = "Manual"
VX_FINAL_FPS = 30
VX_RENDER_VIDEOS = False
VX_INTRO_PREVIEW_ONLY = True
VX_INCLUDE_POPUPS = False
VX_BACKGROUND_MUSIC_VOLUME = 0.5
def configure_outputs():
    raise RuntimeError("old output path")
'''
        patched = patch_setup(source, request_controls({}))
        namespace = {}
        exec(patched, namespace)
        self.assertEqual(namespace["VX_VIDEO_MODE"], "Final")
        self.assertEqual(namespace["VX_FINAL_FPS"], 24)
        self.assertEqual(namespace["VX_BACKGROUND_MUSIC_VOLUME"], 0.08)
        self.assertNotIn("old output path", patched)


class PublishingTests(IsolatedTestCase):
    def test_builds_real_thumbnail_and_rejects_non_4k_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "analysis").mkdir()
            (root / "data").mkdir()
            video = root / "combined.mp4"
            video.write_bytes(b"fixture-video")
            png = root / "slide.png"
            Image.new("RGB", (3840, 2160), "navy").save(png)
            key = "wildcard_manual"
            selection = dict(chip="Wildcard", selection="Manual", total_cost_millions=99.5,
                             players=fixture_catalog()["players"])
            (root / "data" / "selection.json").write_text(json.dumps(selection))
            delivery = dict(mode="Final", gameweek=7, final_mp4=str(video), slide_pngs_4k=[str(png)]*4)
            (root / "delivery_manifest.json").write_text(json.dumps({key: delivery}))
            audit = dict(opening_duration=10, body_duration=200, media_effects_version="body-music-footer-v7",
                         background_music_volume=0.08, popup_times=[180, 204], footer_popup={"audio": "muted"})
            (root / "analysis" / f"{key}_media_audit.json").write_text(json.dumps(audit))
            (root / "analysis" / f"{key}_scene_manifest.json").write_text(json.dumps([dict(title="Squad", start_seconds=0)]))
            with patch("wc_fh.publish_private._probe_video", return_value=dict(width=3840,height=2160,duration=220)):
                package = build_package(root)
                self.assertEqual(package["privacy_status"], "private")
                with Image.open(package["thumbnail_file"]) as thumbnail:
                    self.assertEqual(thumbnail.size, (1280, 720))
                self.assertIn("#FPLWildcard", (root / "youtube" / "description.txt").read_text())
            with patch("wc_fh.publish_private._probe_video", return_value=dict(width=1920,height=1080,duration=220)):
                with self.assertRaisesRegex(ValueError, "3840"):
                    build_package(root)

    def test_both_chips_have_seo_hashtags_actual_cost_and_chapters(self):
        for chip in CHIPS:
            selection = dict(selection="Manual", total_cost_millions=98.1, players=fixture_catalog()["players"])
            details = metadata(chip, 7, selection, [dict(title="Squad plan", start_seconds=25)],
                               dict(opening_duration=10, body_duration=400))
            self.assertLessEqual(len(details["title"]), 100)
            self.assertIn(chip, details["title"])
            self.assertIn("£98.1m", details["description"])
            self.assertIn("0:35 Squad plan", details["description"])
            self.assertIn("#FPLGW7", details["description"])
            self.assertIn(f"FPL {chip}", details["tags"])

    def test_thumbnail_retry_reuses_private_upload_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "youtube").mkdir()
            package = dict(video_file=str(root / "video.mp4"), thumbnail_file=str(root / "thumb.jpg"),
                           output_directory=str(root / "youtube"),
                           metadata=dict(title="test"), privacy_status="private", notify_subscribers=False)
            with patch("wc_fh.publish_private.build_packages", return_value={"wildcard_manual": package}), \
                 patch("wc_fh.publish_private._youtube_service", return_value=object()), \
                 patch("wc_fh.publish_private._verify_channel", return_value={"id": "channel"}), \
                 patch("wc_fh.publish_private._upload_video", return_value="video-id") as upload, \
                 patch("wc_fh.publish_private._verify_private"), \
                 patch("wc_fh.publish_private._set_thumbnail", side_effect=[RuntimeError("temporary thumbnail failure"), None]):
                with self.assertRaises(RuntimeError):
                    publish(root)
                result = publish(root)
                self.assertEqual(upload.call_count, 1)
                self.assertEqual(result["status"], "private_draft_ready")

    def test_both_videos_keep_distinct_metadata_thumbnails_and_retry_receipts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "analysis").mkdir()
            (root / "data").mkdir()
            manifest = {}
            for index, chip in enumerate(CHIPS):
                slug = chip_slug(chip)
                key = slug + "_manual"
                video = root / f"{slug}.mp4"
                video.write_bytes(b"fixture-video")
                png = root / f"{slug}.png"
                Image.new("RGB", (3840, 2160), ("navy", "green")[index]).save(png)
                selection = dict(chip=chip, selection="Manual", total_cost_millions=99.5-index,
                                 players=fixture_catalog()["players"])
                (root / "data" / f"selection_{slug}.json").write_text(json.dumps(selection))
                manifest[key] = dict(mode="Final", gameweek=7, final_mp4=str(video), slide_pngs_4k=[str(png)]*4)
                audit = dict(opening_duration=10, body_duration=200, media_effects_version="body-music-footer-v7",
                             background_music_volume=0.08, popup_times=[180, 204], footer_popup={"audio": "muted"})
                (root / "analysis" / f"{key}_media_audit.json").write_text(json.dumps(audit))
                (root / "analysis" / f"{key}_scene_manifest.json").write_text(json.dumps([dict(title=chip, start_seconds=0)]))
            (root / "delivery_manifest.json").write_text(json.dumps(manifest))
            with patch("wc_fh.publish_private._probe_video", return_value=dict(width=3840,height=2160,duration=220)), \
                 patch("wc_fh.publish_private._youtube_service", return_value=object()), \
                 patch("wc_fh.publish_private._verify_channel", return_value={"id": "channel"}), \
                 patch("wc_fh.publish_private._upload_video", side_effect=["fh-video", "wc-video"]) as upload, \
                 patch("wc_fh.publish_private._verify_private"), \
                 patch("wc_fh.publish_private._set_thumbnail", side_effect=[None, RuntimeError("retry"), None, None]):
                packages = build_packages(root)
                self.assertEqual(set(packages), set(manifest))
                self.assertNotEqual(packages["wildcard_manual"]["thumbnail_file"], packages["free_hit_manual"]["thumbnail_file"])
                for key, package in packages.items():
                    details = package["metadata"]
                    self.assertIn(package["chip"], details["title"])
                    self.assertIn("£99.5m" if key.startswith("wildcard") else "£98.5m", details["description"])
                    self.assertIn("#FPLWildcard" if key.startswith("wildcard") else "#FPLFreeHit", details["description"])
                with self.assertRaisesRegex(RuntimeError, "retry"):
                    publish(root)
                reports = publish(root)
                self.assertEqual(upload.call_count, 2)
                self.assertEqual(set(reports), set(manifest))
                self.assertEqual(reports["free_hit_manual"]["youtube"]["video_id"], "fh-video")
                self.assertEqual(reports["wildcard_manual"]["youtube"]["video_id"], "wc-video")
                self.assertTrue(all(report["status"] == "private_draft_ready" for report in reports.values()))
                for key in manifest:
                    self.assertTrue((root / "youtube" / key / "upload.json").is_file())


if __name__ == "__main__":
    unittest.main()
