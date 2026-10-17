import os
import csv
import tempfile
import unittest

import opponents as op
import generate_subbranches as gsb
import generate_candidates as gc
import runtime_env

CARDSFOLDER = os.path.join(runtime_env.FORGE_ROOT, "forge-gui/res/cardsfolder")


def _write_csv(path, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "variant", "games", "wins", "win_share", "ci_lo", "ci_hi",
                "timeouts", "timeout_rate", "lost_first_rate", "avg_turns", "winner",
            ],
        )
        w.writeheader()
        for r in rows:
            w.writerow(r)


class SubbranchGenerationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_fetch = gc.fetch_edhrec_combos
        # Hermetic: no network. A tiny combo set keeps scoring deterministic.
        gc.fetch_edhrec_combos = lambda colors: {"Thopter Foundry", "Sword of the Meek"}

    @classmethod
    def tearDownClass(cls):
        gc.fetch_edhrec_combos = cls._orig_fetch

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.iter1 = os.path.join(self.tmp, "iter1")
        os.makedirs(self.iter1)

        self.grad_id = "cmdr_add_Vnwxt_Verbose_Host_cut_Sai_Master_Thopterist"
        _write_csv(
            os.path.join(self.iter1, "matrix-by-variant.csv"),
            [
                {"variant": "baseline", "games": 5, "wins": 1, "win_share": 0.2, "ci_lo": 0.0,
                 "ci_hi": 0.7, "timeouts": 0, "timeout_rate": 0.0, "lost_first_rate": 1.0,
                 "avg_turns": 30.0, "winner": False},
                {"variant": self.grad_id, "games": 5, "wins": 4, "win_share": 0.8, "ci_lo": 0.4,
                 "ci_hi": 0.99, "timeouts": 0, "timeout_rate": 0.0, "lost_first_rate": 0.2,
                 "avg_turns": 25.0, "winner": False},
            ],
        )

        self.spec = os.path.join(self.tmp, "iter1_spec.yaml")
        with open(self.spec, "w", encoding="utf-8") as f:
            f.write(f"base: {runtime_env.BASE_DECK}\n")
            f.write("swaps:\n")
            f.write(f"  - id: {self.grad_id}\n")
            f.write("    add: Vnwxt, Verbose Host\n")
            f.write("    cut: Sai, Master Thopterist\n")

    def test_subbranch_spec_is_created_and_loadable(self):
        gsb.generate_subbranches(self.iter1, CARDSFOLDER, self.spec,
                                 max_subbranches=1, min_games=1)
        iter2_spec = os.path.join(self.tmp, "iter2", "keen_variants.yaml")
        self.assertTrue(os.path.isfile(iter2_spec), iter2_spec)

        variants = op.load_variants(iter2_spec)
        self.assertEqual(len(variants), 1)
        v = variants[0]
        # Sub-branch = parent's changes + one new swap (stacked replacement).
        self.assertGreaterEqual(len(v["changes"]), 2)
        self.assertEqual(v["changes"][0], {"add": "Vnwxt, Verbose Host", "cut": "Sai, Master Thopterist"})

    def test_no_grads_means_no_spec(self):
        empty = os.path.join(self.tmp, "empty")
        os.makedirs(empty)
        _write_csv(
            os.path.join(empty, "matrix-by-variant.csv"),
            [
                {"variant": "baseline", "games": 5, "wins": 3, "win_share": 0.6, "ci_lo": 0.3,
                 "ci_hi": 0.8, "timeouts": 0, "timeout_rate": 0.0, "lost_first_rate": 0.0,
                 "avg_turns": 30.0, "winner": False},
                {"variant": "weak", "games": 5, "wins": 1, "win_share": 0.2, "ci_lo": 0.05,
                 "ci_hi": 0.5, "timeouts": 0, "timeout_rate": 0.0, "lost_first_rate": 1.0,
                 "avg_turns": 30.0, "winner": False},
            ],
        )
        gsb.generate_subbranches(empty, CARDSFOLDER, self.spec, max_subbranches=1, min_games=1)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "iter2")))


if __name__ == "__main__":
    unittest.main()
