"""Unit tests for generate_html_report.

Pure unit level: the spec parser, mana-cost parsing, hypergeometric odds,
aggregation semantics (mirroring report.py), opener stats and HTML helpers.
No cardsfolder / live campaign data required.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import generate_html_report as g


def row(**kw):
    base = {
        "seed": 4242, "game": 0, "ms": 120000, "turns": 30,
        "timeout": False, "winner": "p1", "winnerSeat": 0,
        "decks": ["focal", "A", "B", "C"],
        "firstSeat": 0, "finalLife": [10, -1, -2, -3],
        "commanderTurns": [8, 9, 9, 9], "eliminationOrder": [1, 2, 3],
        "hands": [["Island", "Sol Ring"], [], [], []],
    }
    base.update(kw)
    return base


class ManaCost(unittest.TestCase):
    def test_generic_plus_colored(self):
        cmc, colors = g.parse_mana_cost("2 U")
        self.assertEqual(cmc, 3)
        self.assertEqual(colors, {"U": 1})

    def test_two_colored(self):
        cmc, colors = g.parse_mana_cost("1 U U")
        self.assertEqual(cmc, 3)
        self.assertEqual(colors, {"U": 2})

    def test_lands_have_no_cost(self):
        self.assertEqual(g.parse_mana_cost("no cost"), (None, {}))

    def test_colorless_only(self):
        cmc, colors = g.parse_mana_cost("4")
        self.assertEqual(cmc, 4)
        self.assertEqual(colors, {})

    def test_phixian_counts(self):
        cmc, colors = g.parse_mana_cost("UP")
        self.assertEqual(cmc, 1)
        self.assertEqual(colors, {"U": 1})


class SpecParsing(unittest.TestCase):
    def _write(self, text):
        fd, path = tempfile.mkstemp(suffix=".yaml")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        self.addCleanup(os.unlink, path)
        return path

    def test_uniform_changes_lists(self):
        yaml_text = """base: /some/deck.dck
swaps:
  - id: deep_add_Someone
    changes:
      - add: New Commander
        cut: Sai, Master Thopterist
      - add: Card A, the Punctual
        cut: Aetherize
  - id: add_Plain_Swap
    changes:
      - add: Card B
        cut: Counterspell
"""
        spec = g.load_spec(self._write(yaml_text))
        self.assertEqual(spec["base"], "/some/deck.dck")
        self.assertEqual(
            spec["variants"]["deep_add_Someone"],
            [
                {"add": "New Commander", "cut": "Sai, Master Thopterist"},
                {"add": "Card A, the Punctual", "cut": "Aetherize"},
            ])
        # commas are part of card names
        self.assertEqual(spec["variants"]["add_Plain_Swap"],
                         [{"add": "Card B", "cut": "Counterspell"}])

    def test_missing_file_is_ok(self):
        self.assertEqual(g.load_spec("/no/such/file.yaml"),
                         {"base": "", "variants": {}})


class VariantIds(unittest.TestCase):
    def test_canonical_strips_rep_and_seed(self):
        self.assertEqual(g.canonical_variant_id("deep_add_X_r3_s4245.jsonl"), "deep_add_X")
        self.assertEqual(g.canonical_variant_id("baseline_r0_s4242.jsonl"), "baseline")

    def test_layer_detection(self):
        self.assertEqual(g.variant_layer("deep_add_X"), "deep")
        self.assertEqual(g.variant_layer("theme_X__add_Y"), "theme")
        self.assertEqual(g.variant_layer("payoff_a"), "payoff")
        self.assertEqual(g.variant_layer("add_Z"), "add")
        self.assertEqual(g.variant_layer("baseline"), "baseline")


class LossDepth(unittest.TestCase):
    def test_win(self):
        self.assertEqual(g.loss_depth(row(winnerSeat=0)), "win")

    def test_draw_on_timeout(self):
        self.assertEqual(g.loss_depth(row(timeout=True, winnerSeat=0)), "draw")

    def test_draw_on_seat_minus_one(self):
        self.assertEqual(g.loss_depth(row(winnerSeat=-1)), "draw")

    def test_lost_first_mid_last(self):
        self.assertEqual(g.loss_depth(row(winnerSeat=2, eliminationOrder=[0, 1, 3])), "lost_first")
        self.assertEqual(g.loss_depth(row(winnerSeat=2, eliminationOrder=[1, 0, 3])), "lost_mid")
        self.assertEqual(g.loss_depth(row(winnerSeat=2, eliminationOrder=[1, 3, 0])), "lost_last")
        self.assertEqual(g.loss_depth(row(winnerSeat=2, eliminationOrder=[])), "lost_last")


class Wilson(unittest.TestCase):
    def test_zero_games(self):
        self.assertEqual(g.wilson_interval(0, 0), (0.0, 0.0))

    def test_full_success_reaches_one(self):
        lo, hi = g.wilson_interval(10, 10)
        self.assertEqual(hi, 1.0)
        self.assertGreater(lo, 0.72)
        self.assertLess(lo, 0.75)

    def test_bounds_bracket_point(self):
        lo, hi = g.wilson_interval(3, 10)
        self.assertLess(lo, 0.3)
        self.assertGreater(hi, 0.3)


class OddsMath(unittest.TestCase):
    def test_certain_when_successes_fill_pool(self):
        self.assertEqual(g.at_least(7, 7, 7, 1), 1.0)

    def test_opening_two_lands_matches_report_values(self):
        # 40 lands in a 99-card pool: values verified independently
        self.assertAlmostEqual(g.at_least(99, 40, 7, 2), 0.8556, places=3)
        self.assertAlmostEqual(g.at_least(99, 40, 7, 3), 0.5937, places=3)

    def test_hypergeom_sums_to_one(self):
        total = sum(g.hypergeom_pmf(99, 40, 7, k) for k in range(0, 8)
                    if 7 - k <= 59)
        self.assertAlmostEqual(total, 1.0, places=9)

    def test_sol_ring_class(self):
        # singleton deck: 1 Sol Ring copy in the 99-card pool
        p = g.at_least(99, 1, 7, 1)
        self.assertAlmostEqual(p, 7 / 99, places=9)


class Aggregation(unittest.TestCase):
    def test_record_counts_and_sentinels(self):
        rows = [
            row(),                                                  # focal win
            row(winnerSeat=2, eliminationOrder=[0, 1, 3]),          # lost first
            row(timeout=True, winnerSeat=-1, turns=20),             # draw
            row(winnerSeat=1, eliminationOrder=[2, 3, 0],
                commanderTurns=[-1, 4, 5, 6], turns=44),            # lost last
            row(winnerSeat=3, eliminationOrder=[1, 0, 3],
                commanderTurns=[10, 4, 5, 6], turns=25),            # lost mid
        ]
        a = g.aggregate_variant(rows)
        self.assertEqual(a["record"], "1-3-1")
        self.assertEqual(a["decisive"], 4)
        self.assertAlmostEqual(a["win_share"], 0.25)
        self.assertEqual(a["lost_first"], 1)
        self.assertEqual(a["lost_mid"], 1)
        self.assertEqual(a["lost_last"], 1)
        self.assertEqual(a["draws"], 1)
        self.assertAlmostEqual(a["avg_turns"], (30 + 30 + 20 + 44 + 25) / 5)
        # -1 commander turns are ignored; draws still report their cast turn
        self.assertAlmostEqual(a["cmd_avg_turn"], (8 + 8 + 8 + 10) / 4)

    def test_openers_per_variant(self):
        a = g.aggregate_variant([row(), row(hands=[["Island"], [], [], []])])
        self.assertEqual([h for h in a["openers"]][0],
                         ["Island", "Sol Ring"])
        self.assertEqual(a["openers"][1], ["Island"])


class Openers(unittest.TestCase):
    def test_counts(self):
        openers = [
            ["Island", "Island", "Aether Spellbomb"],
            ["Island"],
            [],
        ]
        land_set = {"Island"}
        art_set = {"Aether Spellbomb"}
        o = g.opener_stats(openers, land_set, art_set, "Sai, Master Thopterist")
        self.assertEqual(o["n"], 3)
        self.assertAlmostEqual(o["avg_lands"], 3 / 3)
        self.assertEqual(o["two_plus_obs"], 1)
        self.assertEqual(o["avg_arts"], 1 / 3)
        self.assertEqual(o["cmd_seen"], 0)


class OppLabels(unittest.TestCase):
    def test_clean_opp(self):
        raw = ("Kraum, Ludevic's Opus __ Toggo, Goblin Weaponsmith [June 2026]")
        self.assertEqual(g.clean_opp(raw), "Kraum + Toggo [June 2026]")

    def test_clean_opp_single_commander(self):
        self.assertEqual(g.clean_opp("Peregrin Took [July 2026]"),
                         "Peregrin Took [July 2026]")


class HtmlHelpers(unittest.TestCase):
    def test_esc(self):
        self.assertEqual(g.esc("<b>&\"'>"), "&lt;b&gt;&amp;&quot;'&gt;")

    def test_sharebar_no_decisive(self):
        self.assertIn("no decisive games", g.sharebar(0.5, 0))

    def test_sharebar_width(self):
        bar = g.sharebar(0.5, 1)
        self.assertIn("width:50%", bar)

    def test_svg_curve_shape(self):
        svg = g.svg_curve({2: 10, 3: 5})
        self.assertTrue(svg.startswith("<svg"))
        self.assertIn("aria-label=\"mana curve\"", svg)

    def test_chips_for_changes(self):
        chips = g.chips_for_changes([{"add": "A B C", "cut": "X, Y Z"}])
        self.assertIn("+ A B C", chips)
        self.assertIn("− X, Y Z", chips.replace("&minus;", "−"))

    def test_chips_stock(self):
        self.assertIn("stock build", g.chips_for_changes([]))


class ShortLabel(unittest.TestCase):
    def _spec(self, changes):
        return {"base": "", "variants": changes}

    def test_deep_uses_commander(self):
        spec = self._spec({"deep_add_X": [{"add": "Vivi Ornitier"}]})
        self.assertEqual(g.short_label("deep_add_X", spec), "Vivi Ornitier")

    def test_add_prefix(self):
        self.assertEqual(g.short_label("add_Trading_Post", self._spec({})),
                         "+ Trading Post")

    def test_baseline(self):
        self.assertEqual(g.short_label("baseline", self._spec({})), "Stock precon")


if __name__ == "__main__":
    unittest.main()
