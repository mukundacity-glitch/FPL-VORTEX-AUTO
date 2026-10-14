import unittest

import pandas as pd

from day3.transfer_desk import patch_transfer_desk_source, select_transfer_desk


class TransferDeskSelectionTests(unittest.TestCase):
    def setUp(self):
        self.players = pd.DataFrame([
            {"id": pid, "selected_by_percent": 20, "minutes": 500,
             "transfers_in_event": 20000 if pid <= 5 else 100,
             "transfers_out_event": 20000 if 6 <= pid <= 10 else 100,
             # Deliberately contradict transfer volumes with model recommendations.
             "verdict": "SELL" if pid <= 5 else "BUY", "xpts": pid * 100}
            for pid in range(1, 21)
        ])
        self.evidence = {pid: {"points": 6, "gameweeks": 3} for pid in range(1, 21)}
        self.eo = {pid: float(pid) for pid in range(11, 21)}

    def select(self, **kwargs):
        return select_transfer_desk(self.players, total_managers=1_000_000,
                                    recent_performance=self.evidence,
                                    effective_ownership=kwargs.pop("effective_ownership", self.eo), **kwargs)

    def ids(self, group):
        return [row["player_id"] for row in group]

    def test_volume_ranking_ignores_model_and_squad_verdicts(self):
        self.players.loc[self.players.id.eq(5), "transfers_in_event"] = 50000
        self.players.loc[self.players.id.eq(10), "transfers_out_event"] = 60000
        result = self.select()
        self.assertEqual(self.ids(result["BUY"]), [5, 1, 2, 3, 4])
        self.assertEqual(self.ids(result["SELL"]), [10, 6, 7, 8, 9])
        self.assertEqual(self.ids(result["HOLD"]), [20, 19, 18, 17, 16])
        self.assertEqual(len({pid for rows in result.values() for pid in self.ids(rows)}), 15)

    def test_direction_resolves_players_with_high_in_and_out_volume(self):
        self.players.loc[self.players.id.eq(20), ["transfers_in_event", "transfers_out_event"]] = [100000, 110000]
        result = self.select()
        self.assertEqual(result["SELL"][0]["player_id"], 20)
        self.assertNotIn(20, self.ids(result["BUY"]) + self.ids(result["HOLD"]))

    def test_retained_blank_gate_rejects_hauls_and_heavy_selling(self):
        self.evidence[20] = {"points": 15, "gameweeks": 3}
        self.players.loc[self.players.id.eq(19), ["transfers_in_event", "transfers_out_event"]] = [15000, 15000]
        result = self.select()
        self.assertEqual(self.ids(result["HOLD"]), [18, 17, 16, 15, 14])
        self.assertTrue(all(row["retained_percent_estimate"] >= 95 for row in result["HOLD"]))

    def test_retention_accounts_for_incoming_transfers_in_owner_base(self):
        self.players.loc[self.players.id.eq(20), ["transfers_in_event", "transfers_out_event"]] = [100000, 6000]
        result = self.select(count=4)
        self.assertNotIn(20, self.ids(result["HOLD"]))

    def test_low_eo_and_unknown_returns_are_not_holds(self):
        self.eo[20] = 2
        self.evidence[19] = {}
        result = self.select()
        self.assertNotIn(20, self.ids(result["HOLD"]))
        self.assertNotIn(19, self.ids(result["HOLD"]))

    def test_official_ownership_fallback_is_labeled_and_ranked(self):
        self.players.loc[self.players.id.eq(15), "selected_by_percent"] = 60
        result = self.select(effective_ownership={})
        self.assertEqual(result["HOLD"][0]["player_id"], 15)
        self.assertEqual(result["HOLD"][0]["ownership_source"], "OFFICIAL_OWNERSHIP")
        self.assertEqual(result["HOLD"][0]["ownership_score"], 60)

    def test_form_fallback_does_not_classify_unused_players_as_blanks(self):
        self.evidence[20] = {"form": 0}
        self.evidence[19] = {"form": 2}
        self.players.loc[self.players.id.eq(20), "minutes"] = 0
        result = self.select()
        self.assertNotIn(20, self.ids(result["HOLD"]))
        self.assertIn(19, self.ids(result["HOLD"]))

    def test_changed_snapshot_changes_names_without_source_edits(self):
        first = self.select()
        self.players.loc[self.players.id.eq(11), "transfers_in_event"] = 90000
        second = self.select()
        self.assertNotIn(11, self.ids(first["BUY"]))
        self.assertEqual(second["BUY"][0]["player_id"], 11)

    def test_retained_recent_blanks_fill_a_thin_strict_pool(self):
        for player_id in range(11, 19):
            gross_transfers = 20_000 if player_id in (15, 16, 17) else 200_000
            self.players.loc[self.players.id.eq(player_id),
                             ["transfers_in_event", "transfers_out_event"]] = [
                                 gross_transfers, gross_transfers
                             ]

        result = self.select()
        self.assertEqual(self.ids(result["HOLD"]), [20, 19, 17, 16, 15])
        self.assertTrue(all(row["recent_average_points"] <= 3 for row in result["HOLD"]))
        self.assertTrue(all(row["retained_percent_estimate"] >= 80 for row in result["HOLD"]))
        self.assertNotIn(18, self.ids(result["HOLD"]))

    def test_thin_evidence_never_fills_with_unrelated_model_picks(self):
        self.evidence = {}
        result = self.select()
        self.assertEqual(result["HOLD"], [])
        self.assertEqual(len(result["BUY"]), 5)
        self.assertEqual(len(result["SELL"]), 5)

    def test_short_hold_pool_preserves_gates_and_returns_only_real_candidates(self):
        for qualified_count in range(6):
            with self.subTest(qualified_count=qualified_count):
                self.evidence = {pid: {"points": 18, "gameweeks": 3} for pid in range(1, 21)}
                for pid in range(20 - qualified_count + 1, 21):
                    self.evidence[pid] = {"points": 6, "gameweeks": 3}
                result = self.select()
                self.assertEqual(self.ids(result["HOLD"]), list(range(20, 20 - qualified_count, -1)))
                self.assertTrue(all(row["recent_average_points"] <= 3 for row in result["HOLD"]))

    def test_missing_transfer_activity_still_fails(self):
        self.players["transfers_in_event"] = self.players["transfers_out_event"]
        with self.assertRaisesRegex(RuntimeError, "BUY=0.*SELL=0"):
            self.select()

    def test_missing_or_invalid_official_counts_fail_clearly(self):
        self.players.loc[0, "transfers_in_event"] = float("nan")
        with self.assertRaisesRegex(RuntimeError, "invalid official field"):
            self.select()

    def test_changed_notebook_contract_fails_before_rendering(self):
        with self.assertRaisesRegex(RuntimeError, "notebook contract changed"):
            patch_transfer_desk_source("pass\n")


if __name__ == "__main__":
    unittest.main()
