import os
import unittest

import deck_variant as dv
import opponents
import runtime_env

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
SPEC_PATH = os.path.join(REPO_DIR, "keen_variants.yaml")


def read_raw_base(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("base:"):
                return line.split(":", 1)[1].strip()
    raise AssertionError(f"No 'base:' line in {path}")


class KeenVariantsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw_base = read_raw_base(SPEC_PATH)
        cls.variants = opponents.load_variants(SPEC_PATH)

    def test_yaml_base_is_canonical_deck(self):
        self.assertEqual(
            os.path.normpath(self.raw_base),
            os.path.normpath(runtime_env.BASE_DECK),
        )
        self.assertTrue(os.path.isfile(self.raw_base), self.raw_base)

    def test_load_variants_uses_canonical_base(self):
        self.assertTrue(self.variants, "no variants loaded")
        for v in self.variants:
            self.assertEqual(v["base"], runtime_env.BASE_DECK)
            self.assertTrue(v["id"], v)
            self.assertTrue(v["add"], v)
            self.assertTrue(v["cut"], v)
        ids = [v["id"] for v in self.variants]
        self.assertEqual(len(ids), len(set(ids)), "duplicate variant ids")

    def test_swaps_valid_against_base_deck(self):
        with open(runtime_env.BASE_DECK, "r", encoding="utf-8") as f:
            base_deck = dv.parse_dck(f.read())
        base_names = {dv.card_name(t) for t in base_deck["main"] + base_deck["commander"]}

        self.assertEqual(
            [dv.card_name(t) for t in base_deck["commander"]],
            ["Sai, Master Thopterist"],
        )
        self.assertEqual(dv.total_qty(base_deck["main"]) + len(base_deck["commander"]), 100)

        for v in self.variants:
            self.assertNotIn(v["add"], base_names, v["id"])
            self.assertIn(v["cut"], base_names, v["id"])


if __name__ == "__main__":
    unittest.main()
