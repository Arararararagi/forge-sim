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


if __name__ == "__main__":
    unittest.main()
