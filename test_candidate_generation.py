import os
import re
import tempfile
import urllib.request
import unittest

import opponents as op
import deck_variant as dv
import generate_candidates as gc
import runtime_env

CARDSFOLDER = os.path.join(runtime_env.FORGE_ROOT, "forge-gui/res/cardsfolder")

FAKE_COMBOS = {
    "u": {"Thopter Foundry", "Chief Engineer", "Retrofitter Foundry"},
    "ub": {"Demonic Tutor", "Thopter Foundry"},
    "ur": {"Faithless Looting", "Thopter Foundry"},
    "uw": {"Heliod's Intervention", "Thopter Foundry"},
    "ug": {"Doubling Chant", "Thopter Foundry"},
}


class IdentityParsingTest(unittest.TestCase):
    def test_identity_key_supported_pairs(self):
        self.assertEqual(gc.identity_key({"U"}), "u")
        self.assertEqual(gc.identity_key({"U", "B"}), "ub")
        self.assertEqual(gc.identity_key({"B", "U"}), "ub")
        self.assertEqual(gc.identity_key({"U", "R"}), "ur")
        self.assertEqual(gc.identity_key({"W", "U"}), "uw")
        self.assertEqual(gc.identity_key({"G", "U"}), "ug")

    def test_identity_key_rejects_out_of_scope(self):
        self.assertIsNone(gc.identity_key(set()))           # colorless legends
        self.assertIsNone(gc.identity_key({"B"}))            # no blue
        self.assertIsNone(gc.identity_key({"U", "B", "R"}))  # 3-color
        self.assertEqual(gc.identity_key({"U", "C"}), "u")   # colorless extra

    def test_card_color_identity_from_manacost(self):
        self.assertEqual(gc.card_color_identity(CARDSFOLDER, "Sai, Master Thopterist"), {"U"})
        self.assertEqual(gc.card_color_identity(CARDSFOLDER, "Brudiclad, Telchor Engineer"), {"U", "R"})

    def test_offline_lists_cover_every_identity_with_real_cards(self):
        for key in gc.IDENTITY_KEYS:
            self.assertGreaterEqual(len(gc.OFFLINE_COMBOS[key]), 3, key)
            for name in gc.OFFLINE_COMBOS[key]:
                self.assertTrue(dv.forge_has_card(CARDSFOLDER, name), (key, name))

    def test_fetch_falls_back_to_identity_list_offline(self):
        orig = urllib.request.urlopen
        def boom(*a, **k):
            raise OSError("offline")
        urllib.request.urlopen = boom
        try:
            for key in gc.IDENTITY_KEYS:
                self.assertEqual(set(gc.fetch_edhrec_combos(key)), set(gc.OFFLINE_COMBOS[key]))
        finally:
            urllib.request.urlopen = orig


class DedupeChangeSetsTest(unittest.TestCase):
    def test_identical_change_sets_keep_first_only(self):
        variants = [
            {"id": "theme_A", "changes": [{"add": "X", "cut": "P"}]},
            {"id": "theme_B", "changes": [{"add": "X", "cut": "P"}]},
            {"id": "deep_C", "changes": [{"add": "X", "cut": "P"}, {"add": "Y", "cut": "Q"}]},
            {"id": "add_D", "changes": [{"add": "Z", "cut": "R"}]},
        ]
        keep, dropped = gc.dedupe_change_sets(variants)
        self.assertEqual([v["id"] for v in keep], ["theme_A", "deep_C", "add_D"])
        self.assertEqual(dropped, {"theme_B": "duplicate of theme_A"})
        # Order preserved for life in the spec.
        sigs = [tuple(sorted((c["add"], c["cut"]) for c in v["changes"])) for v in keep]
        self.assertEqual(len(sigs), len(set(sigs)))


class GeneratedSpecTest(unittest.TestCase):
    """One live generation pass (network mocked to the offline lists),
    exercising the real cardsfolder."""

    @classmethod
    def setUpClass(cls):
        orig = gc.fetch_edhrec_combos
        gc.fetch_edhrec_combos = lambda colors: set(FAKE_COMBOS.get(colors.lower(), set()))
        try:
            cls.tmp = tempfile.TemporaryDirectory()
            cls.spec = os.path.join(cls.tmp.name, "spec.yaml")
            gc.generate_candidates(
                runtime_env.BASE_DECK, CARDSFOLDER,
                max_variants=12, seed=7, output_path=cls.spec,
                commanders=2, package_cards=1,
            )
            cls.variants = op.load_variants(cls.spec)
        finally:
            gc.fetch_edhrec_combos = orig

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_budget_is_respected(self):
        self.assertLessEqual(len(self.variants), 12)
        deeps = [v for v in self.variants if v["id"].startswith("deep_")]
        self.assertLessEqual(len(deeps), 2)
        self.assertEqual(deeps[:1] and len(deeps) or 0, min(2, len(deeps)))

    def test_deeps_have_commander_swap_first(self):
        deeps = [v for v in self.variants if v["id"].startswith("deep_")]
        self.assertTrue(deeps, "no deep variants generated")
        for v in deeps:
            first = v["changes"][0]
            self.assertEqual(first["cut"], "Sai, Master Thopterist", v["id"])
            self.assertGreaterEqual(len(v["changes"]), 2, v["id"])

    def test_package_adds_within_commander_identity(self):
        deeps = [v for v in self.variants if v["id"].startswith("deep_")]
        for v in deeps:
            cmdr = v["changes"][0]["add"]
            cmdr_key = gc.identity_key(gc.card_color_identity(CARDSFOLDER, cmdr))
            self.assertIsNotNone(cmdr_key, cmdr)
            allowed = {"U"}
            if len(cmdr_key) == 2:
                allowed.add(cmdr_key[1].upper())
            for ch in v["changes"][1:]:
                self.assertTrue(gc.card_color_identity(CARDSFOLDER, ch["add"]) <= allowed,
                                (v["id"], ch["add"]))

    def test_all_unique_ids_and_every_variant_materializes(self):
        ids = [v["id"] for v in self.variants]
        self.assertEqual(ids, list(dict.fromkeys(ids)))
        # The generator already smoke-materialized every variant; re-check
        # one of each layer survives the same path from the written spec.
        for v in self.variants:
            with tempfile.TemporaryDirectory() as d:
                path = dv.make_variant(runtime_env.BASE_DECK, CARDSFOLDER, v["changes"], d)
                self.assertTrue(os.path.isfile(path))

    def test_generated_yaml_has_changes_schema(self):
        with open(self.spec, "r", encoding="utf-8") as f:
            raw = f.read()
        self.assertTrue(re.search(r"^\s+changes:\s*$", raw, re.M), raw[:400])
        self.assertTrue(re.search(r"^base: " + re.escape(os.path.abspath(runtime_env.BASE_DECK)) + "$", raw, re.M))


if __name__ == "__main__":
    unittest.main()
