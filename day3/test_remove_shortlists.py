import ast
from pathlib import Path
import tempfile
import unittest

from day3.publish_private import _metadata, _thumbnail_source
from day3.remove_shortlists import REMOVED_SCENE, _RemovePresentation, purge_shortlist_artifacts


class RemoveShortlistsTests(unittest.TestCase):
    def transform(self, source):
        tree = _RemovePresentation().visit(ast.parse(source))
        ast.fix_missing_locations(tree)
        result = ast.unparse(tree)
        self.assertNotIn(REMOVED_SCENE, result)
        return result

    def test_selection_agenda_and_registry_remove_only_shortlist(self):
        source = '''
selection = {"04_final_shortlists": True, "05_transfer_decision_desk": True, "05b_my_plan_transfer": True}
order = [("04_final_shortlists", "FINAL SHORTLISTS", "removed.mp4"), ("05_transfer_decision_desk", "DESK", "desk.mp4")]
names = ["04_final_shortlists", "05b_my_plan_transfer"]
'''
        namespace = {}
        exec(self.transform(source), namespace)
        self.assertEqual(namespace["selection"], {"05_transfer_decision_desk": True, "05b_my_plan_transfer": True})
        self.assertEqual(namespace["order"], [("05_transfer_decision_desk", "DESK", "desk.mp4")])
        self.assertEqual(namespace["names"], ["05b_my_plan_transfer"])

    def test_removed_branch_keeps_following_elif_and_else(self):
        source = '''
def choose(key):
    if key == "04_final_shortlists":
        return "deleted"
    elif key == "05_transfer_decision_desk":
        return "desk"
    else:
        return "other"
'''
        namespace = {}
        exec(self.transform(source), namespace)
        self.assertEqual(namespace["choose"]("05_transfer_decision_desk"), "desk")
        self.assertEqual(namespace["choose"]("gw_review"), "other")

    def test_shared_condition_preserves_all_remaining_scenes(self):
        source = '''
def active(key):
    if key in {"03_defcon", "04_final_shortlists", "05_transfer_decision_desk"}:
        return True
    return False
'''
        namespace = {}
        exec(self.transform(source), namespace)
        self.assertTrue(namespace["active"]("03_defcon"))
        self.assertTrue(namespace["active"]("05_transfer_decision_desk"))

    def test_stale_cleanup_keeps_model_and_other_slides(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for folder in ("SLIDE", "DATA", "html/animated", "MP4"):
                path = root / folder
                path.mkdir(parents=True)
                (path / "final_shortlists_scene_4k.png").write_bytes(b"removed")
                (path / "04_final_shortlists.mp4").write_bytes(b"removed")
                (path / "transfer_decision_desk.json").write_bytes(b"kept")
                (path / "phase11_transfer_shortlist.json").write_bytes(b"model")
            purge_shortlist_artifacts(root, root / "unused")
            self.assertFalse(list(root.rglob("final_shortlists*")))
            self.assertFalse(list(root.rglob("04_final_shortlists*")))
            self.assertEqual(len(list(root.rglob("transfer_decision_desk.json"))), 4)
            self.assertEqual(len(list(root.rglob("phase11_transfer_shortlist.json"))), 4)

    def test_upload_description_keeps_desk_without_promising_shortlists(self):
        description = _metadata(7, 6, "ROLL", "AUTO")["description"]
        self.assertNotIn("shortlist", description.lower())
        self.assertIn("Transfer Decision Desk", description)

    def test_thumbnail_never_uses_a_stale_shortlist_slide(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            slides = root / "SLIDE"
            slides.mkdir()
            (slides / "final_shortlists_scene_4k.png").write_bytes(b"old")
            with self.assertRaises(FileNotFoundError):
                _thumbnail_source(root, "NEITHER")
            active = slides / "defcon_scene_4k.png"
            active.write_bytes(b"active")
            self.assertEqual(_thumbnail_source(root, "NEITHER"), active)


if __name__ == "__main__":
    unittest.main()
