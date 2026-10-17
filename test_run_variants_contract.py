import os
import re
import sys
import json
import types
import tempfile
import unittest

import deck_variant as dv
import opponents as op
import run_variants as rv
import runtime_env


class MultiSwapParsingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.spec = os.path.join(self.tmp, "spec.yaml")
        with open(self.spec, "w", encoding="utf-8") as f:
            f.write(f"base: {runtime_env.BASE_DECK}\n")
            f.write("swaps:\n")
            f.write("  - id: single\n")
            f.write("    add: Ponder\n")
            f.write("    cut: Thought Monitor\n")
            f.write("  - id: multi\n")
            f.write("    changes:\n")
            f.write("      - add: Preordain\n")
            f.write("        cut: Thought Monitor\n")
            f.write("      - add: Brainstorm\n")
            f.write("        cut: Thoughtcast\n")
            f.write("  - id: comma_name\n")
            f.write("    add: Vnwxt, Verbose Host\n")
            f.write("    cut: Thought Monitor\n")

    def test_single_swap_normalised_to_changes(self):
        variants = {v["id"]: v for v in op.load_variants(self.spec)}
        self.assertEqual(len(variants["single"]["changes"]), 1)
        self.assertEqual(variants["single"]["changes"][0], {"add": "Ponder", "cut": "Thought Monitor"})

    def test_multi_swap_parsed(self):
        variants = {v["id"]: v for v in op.load_variants(self.spec)}
        changes = variants["multi"]["changes"]
        self.assertEqual(len(changes), 2)
        self.assertEqual(changes[1], {"add": "Brainstorm", "cut": "Thoughtcast"})

    def test_commas_in_card_names_are_not_split(self):
        # "Vnwxt, Verbose Host" must stay a single card, not split at the comma.
        variants = {v["id"]: v for v in op.load_variants(self.spec)}
        self.assertEqual(variants["comma_name"]["changes"][0]["add"], "Vnwxt, Verbose Host")


class MaterialisationContractTest(unittest.TestCase):
    """Regression: run_unit must use the path make_variant actually wrote."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.variants_dir = os.path.join(self.tmp, "variants")
        os.makedirs(self.variants_dir)

    def test_make_variant_path_is_retrievable_by_run_unit(self):
        changes = [{"add": "Thoughtcast", "cut": "Thought Monitor"}]
        path = dv.make_variant(runtime_env.BASE_DECK, os.path.join(runtime_env.FORGE_ROOT, "forge-gui/res/cardsfolder"),
                               changes, self.variants_dir)
        self.assertTrue(os.path.isfile(path))

        args = types.SimpleNamespace(
            base_deck=runtime_env.BASE_DECK,
            variant_paths={"v1": path},
        )
        self.assertEqual(rv.focal_deck_for("v1", args), path)
        self.assertEqual(rv.focal_deck_for("baseline", args), runtime_env.BASE_DECK)

    def test_unknown_variant_raises(self):
        args = types.SimpleNamespace(base_deck=runtime_env.BASE_DECK, variant_paths={})
        with self.assertRaises(KeyError):
            rv.focal_deck_for("missing", args)

    def test_variant_filename_strips_apostrophes_and_punctuation(self):
        cf = os.path.join(runtime_env.FORGE_ROOT, "forge-gui/res/cardsfolder")
        changes = [
            {"add": "Raff Capashen, Ship's Mage", "cut": "Aetherize"},
            {"add": "Illusionist's Bracers", "cut": "Fall from Favor"},
        ]
        path = dv.make_variant(runtime_env.BASE_DECK, cf, changes, self.variants_dir)
        self.assertTrue(os.path.isfile(path), path)
        stem = os.path.basename(path)[:-len(".dck")]
        self.assertNotIn("'", stem)
        self.assertTrue(re.fullmatch(r"[\w\-]+", stem), stem)


class RelativeDataDirTest(unittest.TestCase):
    """Forge runs with a different cwd; every path handed to the java
    process (focal deck, opponents, -o output) must be absolute, even when
    the campaign was launched with a relative --data-dir."""

    def test_run_unit_absolutizes_all_paths(self):
        import threading
        tmp = tempfile.mkdtemp()
        calls = []

        class FakeCompleted:
            returncode = 0

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            out = cmd[cmd.index("-o") + 1]
            os.makedirs(os.path.dirname(out), exist_ok=True)
            open(out, "w").close()
            return FakeCompleted()

        real_run, real_makedirs = rv.subprocess.run, rv.os.makedirs
        rv.subprocess.run = fake_run
        rv.os.makedirs = lambda *a, **k: None  # keep the relative dir uncreated
        try:
            variant_deck = os.path.join(tmp, "v.dck")
            with open(variant_deck, "w") as f:
                f.write("[metadata]\nName=v\n[Main]\n1 Island\n[Commander]\n1 Sai\n")
            args = types.SimpleNamespace(
                data_dir="rel_out",  # RELATIVE on purpose
                base_deck=runtime_env.BASE_DECK,
                variant_paths={"v1": variant_deck},
                jar=runtime_env.HARNESS_JAR,
                cwd=runtime_env.CWD,
                java="java",
                ai_timeout=2,
                clock=300,
                games=1,
                sim_budget=-1,
            )
            unit = {"variant_id": "v1", "rep": 0, "seed": 4242,
                    "opponents": ["oppA.dck", "oppB.dck", "oppC.dck"]}
            counters = {"completed": 0, "timeout": 0, "error": 0, "games": 0}
            rv.run_unit(unit, args, threading.Lock(), counters)

            self.assertEqual(counters["error"], 1, counters)  # 0-byte fake run

            argv = calls[0]
            d_args = argv[argv.index("-d") + 1:]
            # -d focal deck followed by three opponent decks
            self.assertTrue(os.path.isabs(d_args[0]), d_args)
            self.assertEqual(d_args[0], os.path.abspath(variant_deck))
            for p in d_args[1:4]:
                self.assertTrue(os.path.isabs(p), (p, argv))
            out_path = argv[argv.index("-o") + 1]
            self.assertTrue(os.path.isabs(out_path), out_path)
        finally:
            rv.subprocess.run = real_run
            rv.os.makedirs = real_makedirs


class EmptyOutputDetectionTest(unittest.TestCase):
    def test_count_jsonl_rows(self):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".jsonl")
        f.write(b'{"a":1}\n\n{"a":2}\n')
        f.close()
        try:
            self.assertEqual(rv.count_jsonl_rows(f.name), 2)
        finally:
            os.unlink(f.name)

    def test_missing_file_is_zero(self):
        self.assertEqual(rv.count_jsonl_rows("/nonexistent/nope.jsonl"), 0)

    def test_empty_file_is_zero(self):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".jsonl")
        f.close()
        try:
            self.assertEqual(rv.count_jsonl_rows(f.name), 0)
        finally:
            os.unlink(f.name)


class RunUnitCountsErrorsTest(unittest.TestCase):
    """run_unit must count a variant that produces no JSONL as an error,
    and must pass the materialised path (not a reconstructed one) to Forge."""

    def test_empty_output_counted_as_error(self):
        tmp = tempfile.mkdtemp()
        calls = []

        class FakeCompleted:
            returncode = 0

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            # Simulate Forge creating a 0-byte output file.
            out = cmd[cmd.index("-o") + 1]
            open(out, "w").close()
            return FakeCompleted()

        real_run = rv.subprocess.run
        rv.subprocess.run = fake_run
        try:
            variant_deck = os.path.join(tmp, "v.dck")
            with open(variant_deck, "w") as f:
                f.write("[metadata]\nName=v\n[Main]\n1 Island\n[Commander]\n1 Sai, Master Thopterist\n")
            args = types.SimpleNamespace(
                data_dir=tmp,
                base_deck=runtime_env.BASE_DECK,
                variant_paths={"v1": variant_deck},
                jar=runtime_env.HARNESS_JAR,
                cwd=runtime_env.CWD,
                java="java",
                ai_timeout=2,
                clock=300,
                games=1,
                sim_budget=-1,
            )
            unit = {"variant_id": "v1", "rep": 0, "seed": 4242, "opponents": ["a", "b", "c"]}
            counters = {"completed": 0, "timeout": 0, "error": 0, "games": 0}
            rv.run_unit(unit, args, __import__("threading").Lock(), counters)
            self.assertEqual(counters["error"], 1, counters)
            self.assertEqual(counters["completed"], 0, counters)
            # The focal deck passed to Forge must be the materialised path.
            self.assertEqual(calls[0][calls[0].index("-d") + 1], variant_deck)
        finally:
            rv.subprocess.run = real_run


if __name__ == "__main__":
    unittest.main()
