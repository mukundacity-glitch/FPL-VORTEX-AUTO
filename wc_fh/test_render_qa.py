from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

from wc_fh.controls import CHIP_OPTIONS, chip_slug, request_controls, requested_chips
from wc_fh.render_qa import measured_timeline, verified_visual, verify_exported_visuals, verify_layout
from wc_fh.run_notebook import execute, patch_render


# The failed notebook runs this preview before either render function measures audio.
RENDER_SEQUENCE = '''
def _vx_render_verified_wildcard(plan, key, label, assets):
    scene = VX_SCENE_MANIFESTS[key][0]
    measure(scene)
    visual = vx_visual_from_manifest(scene, {}, VX_VIDEO_PROFILE['width'], VX_VIDEO_PROFILE['height'])
    return visual

def _vx_render_verified_freehit(plan, key, label, assets):
    scene = VX_SCENE_MANIFESTS[key][0]
    measure(scene)
    visual = vx_visual_from_manifest(scene, {}, VX_VIDEO_PROFILE['width'], VX_VIDEO_PROFILE['height'])
    return visual

if VX_GENERATE_WILDCARD:
    for key in VX_DATA_CONTRACTS:
        if key.startswith('wildcard'):
            proof = VXWV.verify_visuals({}, VX_SCENE_MANIFESTS[key], {}, {}, Path('.'))
'''


def scene_fixture() -> dict:
    return dict(scene_id="slide_2", timing=dict(status="measured", player_alignment="measured_segments"),
                duration_seconds=122.5, narration_text="First phrase. Second phrase.",
                narration_timeline=[dict(segment_id="first", narration_text="First phrase.",
                                         start=0.0, end=30.0, alignment="measured_segments"),
                                    dict(segment_id="second", narration_text="Second phrase.",
                                         start=30.0, end=120.0, alignment="measured_segments")])


class RenderSequenceTests(unittest.TestCase):
    def test_all_six_modes_measure_before_factory_and_keep_data_validation(self):
        for chip in CHIP_OPTIONS:
            for selection in ("Auto", "Manual"):
                with self.subTest(chip=chip, selection=selection), tempfile.TemporaryDirectory() as directory:
                    controls = request_controls(dict(WC_FH_CHIP=chip, WC_FH_SELECTION=selection))
                    keys = [chip_slug(value) + "_" + selection.lower() for value in requested_chips(controls)]
                    scenes = {key: [dict(scene_id="slide", timing={}, narration_timeline=[dict(start=None)])]
                              for key in keys}
                    order = []
                    def measure(scene):
                        order.append("measure")
                        scene.update(scene_fixture())
                    def factory(scene, contract, width, height):
                        measured_timeline(scene)
                        order.append("factory")
                        return SimpleNamespace(render_video=Mock())
                    validation = SimpleNamespace(validate_scene_json=Mock(), validate_render_script=Mock())
                    legacy = Mock(side_effect=ValueError("Measured narration cues required; estimated timings refused"))
                    root = Path(directory)
                    script = root / "narration.txt"
                    script.write_text("First phrase. Second phrase.")
                    namespace = dict(Path=Path, json=json, measure=measure, vx_visual_from_manifest=factory,
                                     VX_VIDEO_PROFILE=dict(width=3840, height=2160),
                                     VX_SCENE_MANIFESTS=scenes, VX_DATA_CONTRACTS={key: {} for key in keys},
                                     VX_GENERATE_WILDCARD="Wildcard" in requested_chips(controls),
                                     VXWC=validation, VXWV=SimpleNamespace(verify_visuals=legacy),
                                     VX_WILDCARD_SCENE_JSONS={key: {} for key in keys},
                                     VX_NARRATION_PATHS={key: script for key in keys},
                                     VX_NARRATION_OBJECTS={key: {} for key in keys}, VX_NARRATION={},
                                     VX_ANALYSIS_ROOT=root,
                                     decode_script=lambda text: [dict(text=text)])
                    if "Wildcard" in requested_chips(controls):
                        with self.assertRaisesRegex(ValueError, "Measured narration"):
                            exec(RENDER_SEQUENCE, dict(namespace))
                    with contextlib.redirect_stdout(io.StringIO()):
                        exec(patch_render(RENDER_SEQUENCE), namespace)
                    legacy.assert_called_once() if "Wildcard" in requested_chips(controls) else legacy.assert_not_called()
                    for key in keys:
                        name = "wildcard" if key.startswith("wildcard") else "freehit"
                        namespace[f"_vx_render_verified_{name}"](None, key, "", {})
                    self.assertEqual(order, ["measure", "factory"] * len(keys))
                    expected = int("Wildcard" in requested_chips(controls))
                    self.assertEqual(validation.validate_scene_json.call_count, expected)
                    self.assertEqual(validation.validate_render_script.call_count, expected)

    def test_changed_notebook_contract_fails_preflight(self):
        for source in (RENDER_SEQUENCE.replace("verify_visuals", "new_verifier"),
                       RENDER_SEQUENCE.replace("def _vx_render_verified_freehit", "def changed_renderer"),
                       RENDER_SEQUENCE.replace("visual = vx_visual_from_manifest", "visual = new_factory")):
            with self.subTest(source=source), self.assertRaisesRegex(ValueError, "contract changed"):
                patch_render(source)

    def test_invalid_scene_data_still_blocks_before_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            namespace = dict(VX_GENERATE_WILDCARD=True, VX_DATA_CONTRACTS={"wildcard_auto": {}},
                             VX_SCENE_MANIFESTS={"wildcard_auto": []}, VX_WILDCARD_SCENE_JSONS={"wildcard_auto": {}},
                             VXWC=SimpleNamespace(validate_scene_json=Mock(side_effect=ValueError("invalid scene data"))))
            with self.assertRaisesRegex(ValueError, "invalid scene data"):
                exec(patch_render(RENDER_SEQUENCE), namespace)


class MeasuredVisualTests(unittest.TestCase):
    def test_pillow_samples_the_actual_duration_and_validates_frame_size(self):
        draw = Mock(return_value=Image.new("RGB", (32, 18)))
        visual = SimpleNamespace(draw=draw, layout=[])
        self.assertIs(verified_visual(lambda *args: visual, scene_fixture(), {}, 32, 18), visual)
        self.assertEqual([call.args for call in draw.call_args_list],
                         [(0.0, 122.5), (61.25, 122.5), (122.45, 122.5)])
        with self.assertRaisesRegex(ValueError, "frame is not"):
            verified_visual(lambda *args: visual, scene_fixture(), {}, 64, 36)

    def test_remotion_uses_native_rendering_without_pillow_api(self):
        visual = SimpleNamespace(render_video=Mock())
        self.assertIs(verified_visual(lambda *args: visual, scene_fixture(), {}, 3840, 2160), visual)
        visual.render_video.assert_not_called()
        with self.assertRaisesRegex(TypeError, "neither draw nor native"):
            verified_visual(lambda *args: object(), scene_fixture(), {}, 3840, 2160)

    def test_unmeasured_estimated_nonfinite_or_overlapping_cues_are_rejected(self):
        mutations = [lambda scene: scene["timing"].update(player_alignment="estimated"),
                     lambda scene: scene["timing"].update(status="pending"),
                     lambda scene: scene.update(duration_seconds=float("nan")),
                     lambda scene: scene["narration_timeline"][0].update(start=None),
                     lambda scene: scene["narration_timeline"][0].update(end=float("inf")),
                     lambda scene: scene["narration_timeline"][1].update(start=29.0),
                     lambda scene: scene["narration_timeline"][1].update(end=123.0),
                     lambda scene: scene["narration_timeline"][0].update(alignment="estimated_word_counts")]
        for mutate in mutations:
            scene = scene_fixture()
            mutate(scene)
            factory = Mock()
            with self.subTest(scene=scene), self.assertRaises(ValueError):
                verified_visual(factory, scene, {}, 3840, 2160)
            factory.assert_not_called()

    def test_image_aspect_pitch_aspect_and_text_overlap_checks_remain_enforced(self):
        layouts = [[dict(kind="photo", source_aspect=1.5, rendered_aspect=1.0)],
                   [dict(kind="pitch", aspect=1.5, reference_aspect=1.0)],
                   [dict(kind="text", text="First", box=(0, 0, 10, 10)),
                    dict(kind="text", text="Second", box=(5, 5, 15, 15))]]
        for layout in layouts:
            with self.subTest(layout=layout), self.assertRaises(ValueError):
                verify_layout(layout)

    def test_both_native_deliveries_require_decoded_4k_images_and_measured_cues(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "analysis").mkdir()
            png = root / "slide.png"
            Image.new("RGB", (3840, 2160), "navy").save(png)
            manifest = {}
            for key in ("wildcard_manual", "free_hit_manual"):
                scenes = [dict(scene_fixture(), scene_id=f"slide_{index}") for index in range(4)]
                (root / "analysis" / f"{key}_scene_manifest.json").write_text(json.dumps(scenes))
                manifest[key] = dict(slide_pngs_4k=[str(png)] * 4)
            with contextlib.redirect_stdout(io.StringIO()):
                verify_exported_visuals(root, manifest)
            for key in manifest:
                self.assertEqual(len(json.loads((root / "analysis" / f"{key}_visual_qa.json").read_text())), 4)
            Image.new("RGB", (1280, 720)).save(png)
            with self.assertRaisesRegex(ValueError, "3840"):
                verify_exported_visuals(root, manifest)
            png.write_bytes(b"corrupt PNG")
            with self.assertRaises(OSError):
                verify_exported_visuals(root, manifest)


class RendererCleanupTests(unittest.TestCase):
    def test_cleanup_happens_on_success_and_on_original_cell_failure(self):
        for failure in (False, True):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                notebook = root / "notebook.ipynb"
                notebook.write_text("{}")
                (root / "delivery_manifest.json").write_text(json.dumps({"wildcard_auto": dict(mode="Final")}))
                error = ValueError("original render failure") if failure else None
                renderer = SimpleNamespace(close=Mock())
                shell = SimpleNamespace(user_ns={"VX_BROADCAST_RENDERER": renderer},
                                        run_cell=Mock(return_value=SimpleNamespace(error_before_exec=None, error_in_exec=error)))
                with patch("wc_fh.run_notebook.prepare_cells", return_value=[(17, "14", "pass")]), \
                     patch("wc_fh.run_notebook.request_controls", return_value=request_controls({})), \
                     patch("wc_fh.run_notebook.install_colab_compatibility"), \
                     patch("wc_fh.run_notebook.verify_exported_visuals"), \
                     patch("wc_fh.drive_io.output_root", return_value=root), \
                     patch("IPython.core.interactiveshell.InteractiveShell.instance", return_value=shell), \
                     contextlib.redirect_stdout(io.StringIO()):
                    if failure:
                        with self.assertRaises(RuntimeError) as raised:
                            execute(notebook)
                        self.assertIs(raised.exception.__cause__, error)
                    else:
                        execute(notebook)
                renderer.close.assert_called_once()

    def test_browser_atexit_callback_does_not_run_after_executor_shutdown(self):
        script = '''
import atexit
from concurrent.futures import ThreadPoolExecutor
from wc_fh.render_qa import close_renderer
class Renderer:
    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=1)
        atexit.register(self.close)
    def close(self):
        self.executor.submit(lambda: None).result()
        self.executor.shutdown()
renderer = Renderer()
close_renderer({'VX_BROADCAST_RENDERER': renderer})
'''
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=False, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
