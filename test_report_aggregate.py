import os
import tempfile
import unittest
import json
from report import load_results, canonical_variant_id


class ReportAggregateTest(unittest.TestCase):
    def test_canonical_variant_id_strips_suffixes(self):
        self.assertEqual(canonical_variant_id("baseline_r0_s123.jsonl"), "baseline")
        self.assertEqual(canonical_variant_id("keen_swap_steel_gearhulk_r2_s127.jsonl"), "keen_swap_steel_gearhulk")
        self.assertEqual(canonical_variant_id("keen_swap_a_b_r10_s4242.jsonl"), "keen_swap_a_b")

    def test_load_results_aggregates_across_files(self):
        with tempfile.TemporaryDirectory() as d:
            # Write two files for same canonical variant (different reps/seeds)
            row1 = {"winnerSeat": 0, "timeout": False, "eliminationOrder": [], "turns": 5}
            row2 = {"winnerSeat": 1, "timeout": False, "eliminationOrder": [1, 0], "turns": 6}
            row3 = {"winnerSeat": 0, "timeout": False, "eliminationOrder": [], "turns": 7}
            # baseline r0
            with open(os.path.join(d, "baseline_r0_s1.jsonl"), "w") as f:
                f.write(json.dumps(row1) + "\n")
                f.write(json.dumps(row2) + "\n")  # 1 win, 1 loss from baseline perspective? winnerSeat==1 means focal lost
            # baseline r1
            with open(os.path.join(d, "baseline_r1_s2.jsonl"), "w") as f:
                f.write(json.dumps(row3) + "\n")
            # variant file
            with open(os.path.join(d, "keen_swap_steel_gearhulk_r0_s3.jsonl"), "w") as f:
                f.write(json.dumps(row1) + "\n")
                f.write(json.dumps(row1) + "\n")
                f.write(json.dumps(row2) + "\n")
            res = load_results(d)
            byv = {r["variant"]: r for r in res}
            self.assertIn("baseline", byv)
            self.assertIn("keen_swap_steel_gearhulk", byv)
            self.assertEqual(byv["baseline"]["games"], 3)
            self.assertEqual(byv["keen_swap_steel_gearhulk"]["games"], 3)

    def test_reps_gt1_case(self):
        with tempfile.TemporaryDirectory() as d:
            # Simulate reps > 1 for baseline
            for rep in range(3):
                p = os.path.join(d, f"baseline_r{rep}_s{rep+100}.jsonl")
                with open(p, "w") as f:
                    f.write(json.dumps({"winnerSeat": 0, "timeout": False, "turns": 4}) + "\n")
            for rep in range(2):
                p = os.path.join(d, f"v1_r{rep}_s{rep+200}.jsonl")
                with open(p, "w") as f:
                    f.write(json.dumps({"winnerSeat": 0, "timeout": False, "turns": 5}) + "\n")
                    f.write(json.dumps({"winnerSeat": 1, "timeout": False, "eliminationOrder": [1,0], "turns": 6}) + "\n")
            res = load_results(d)
            byv = {r["variant"]: r for r in res}
            self.assertEqual(byv["baseline"]["games"], 3)
            self.assertEqual(byv["v1"]["games"], 4)

    def test_manifest_variants_without_jsonl_are_recorded(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "baseline_r0_s1.jsonl"), "w") as f:
                f.write(json.dumps({"winnerSeat": 0, "timeout": False, "turns": 4}) + "\n")
            # A variant that produced no games at all (failed deck load / skipped).
            with open(os.path.join(d, "manifest.json"), "w") as f:
                json.dump({"variants": ["baseline", "never_ran"], "skipped": ["broke_on_build"]}, f)
            res = load_results(d)
            byv = {r["variant"]: r for r in res}
            self.assertIn("never_ran", byv)
            self.assertIn("broke_on_build", byv)
            self.assertEqual(byv["never_ran"]["games"], 0)
            self.assertEqual(byv["broke_on_build"]["games"], 0)

    def test_draws_count_against_in_win_share(self):
        """Regression for the excludes-decided semantics: a stall variant
        that wins 1 and draws 7 of 8 games must show win_share 0.125, not
        1.0, and draws/feed the denominator of the Wilson interval."""
        with tempfile.TemporaryDirectory() as d:
            # baseline: 4 wins in 6 decided games
            for i, ws in enumerate([0, 0, 0, 0, 1, 1]):
                p = os.path.join(d, f"baseline_r{i}_s{i}.jsonl")
                with open(p, "w") as f:
                    f.write(json.dumps({"winnerSeat": ws, "timeout": False, "turns": 5}) + "\n")
            # variant: 1 win + 7 draws (timeout rows, everyone alive)
            for i in range(8):
                row = {"winnerSeat": 0, "timeout": False, "turns": 9} if i == 0 else {
                    "winnerSeat": -1, "timeout": True, "winner": "draw", "turns": 40,
                    "finalLife": [30, 20, 25, 18], "eliminationOrder": []}
                with open(os.path.join(d, f"stall_r{i}_s{i}.jsonl"), "w") as f:
                    f.write(json.dumps(row) + "\n")
            res = load_results(d, min_games=3)
            byv = {r["variant"]: r for r in res}
            self.assertAlmostEqual(byv["stall"]["win_share"], 1 / 8)
            self.assertEqual(byv["stall"]["wins"], 1)
            self.assertEqual(byv["stall"]["decided"], 1)
            self.assertEqual(byv["stall"]["draws"], 7)
            self.assertAlmostEqual(byv["stall"]["draw_rate"], 7 / 8)
            self.assertAlmostEqual(byv["stall"]["decided_win_share"], 1.0)

    def test_winner_flag_achievable_at_small_n(self):
        """The old games>=50 promotion gate made winners impossible at any
        realistic per-variant count; a clearly stronger variant at n>=3
        must now qualify."""
        with tempfile.TemporaryDirectory() as d:
            # baseline loses everything it decides
            for i in range(4):
                p = os.path.join(d, f"baseline_r{i}_s{i}.jsonl")
                with open(p, "w") as f:
                    f.write(json.dumps({"winnerSeat": 1, "timeout": False, "turns": 5,
                                        "eliminationOrder": [0, 1, 2]}) + "\n")
            # variant wins every game
            for i in range(4):
                p = os.path.join(d, f"strong_r{i}_s{i}.jsonl")
                with open(p, "w") as f:
                    f.write(json.dumps({"winnerSeat": 0, "timeout": False, "turns": 5}) + "\n")
            res = load_results(d, min_games=3)
            byv = {r["variant"]: r for r in res}
            self.assertTrue(byv["strong"]["winner"])
            # ...but a below-baseline variant does not
            for i in range(4):
                p = os.path.join(d, f"weak_r{i}_s{i}.jsonl")
                with open(p, "w") as f:
                    f.write(json.dumps({"winnerSeat": 1, "timeout": False, "turns": 5,
                                        "eliminationOrder": [0, 1, 2]}) + "\n")
            res = load_results(d, min_games=3)
            byv = {r["variant"]: r for r in res}
            self.assertFalse(byv["weak"]["winner"])

    def test_min_games_floor_suppresses_flag(self):
        with tempfile.TemporaryDirectory() as d:
            for i in range(1):
                p = os.path.join(d, f"baseline_r{i}_s{i}.jsonl")
                with open(p, "w") as f:
                    f.write(json.dumps({"winnerSeat": 1, "timeout": False, "turns": 5,
                                        "eliminationOrder": [0, 1, 2]}) + "\n")
            for i in range(1):
                p = os.path.join(d, f"ok_r{i}_s{i}.jsonl")
                with open(p, "w") as f:
                    f.write(json.dumps({"winnerSeat": 0, "timeout": False, "turns": 5}) + "\n")
            self.assertFalse({r["variant"]: r for r in load_results(d, min_games=3)}["ok"]["winner"])
            self.assertTrue({r["variant"]: r for r in load_results(d, min_games=1)}["ok"]["winner"])


if __name__ == "__main__":
    unittest.main()
