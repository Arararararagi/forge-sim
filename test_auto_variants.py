import os
import unittest
from unittest import mock

import auto_variants
import runtime_env

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
KNOB_KEYS = {
    "reps",
    "games",
    "workers",
    "max_variants",
    "sim_budget",
    "ai_timeout",
    "clock",
}


class PresetsTest(unittest.TestCase):
    def test_presets_defined(self):
        self.assertEqual(set(auto_variants.PRESETS), {"quick", "medium", "large"})

    def test_presets_have_knob_keys(self):
        for name, preset in auto_variants.PRESETS.items():
            self.assertTrue(KNOB_KEYS.issubset(preset), f"{name} missing keys")
            for key in KNOB_KEYS:
                if key == "max_variants":
                    self.assertTrue(preset[key] is None or isinstance(preset[key], int))
                else:
                    self.assertIsInstance(preset[key], int, f"{name}.{key}")

    def test_quick_is_smallest(self):
        quick = auto_variants.PRESETS["quick"]
        for other_name in ("medium", "large"):
            other = auto_variants.PRESETS[other_name]
            self.assertLessEqual(quick["reps"], other["reps"])
            self.assertLessEqual(quick["games"], other["games"])
            self.assertLessEqual(quick["workers"], other["workers"])
            if other["max_variants"] is not None and quick["max_variants"] is not None:
                self.assertLessEqual(quick["max_variants"], other["max_variants"])
        self.assertEqual(quick["reps"], 1)
        self.assertEqual(quick["games"], 1)
        self.assertEqual(quick["max_variants"], 0)
        self.assertGreater(quick["sim_budget"], 0, "quick must be budget-bounded")

    def test_large_runs_everything(self):
        self.assertIsNone(auto_variants.PRESETS["large"]["max_variants"])


class ResolveConfigTest(unittest.TestCase):
    def test_canonical_defaults(self):
        cfg = auto_variants.resolve_config("quick", {})
        self.assertEqual(cfg["jar"], runtime_env.HARNESS_JAR)
        self.assertEqual(cfg["cwd"], runtime_env.CWD)
        self.assertTrue(os.path.isfile(cfg["jar"]), cfg["jar"])
        self.assertTrue(os.path.isdir(cfg["cwd"]), cfg["cwd"])
        self.assertTrue(os.path.isdir(cfg["cardsfolder"]), cfg["cardsfolder"])
        self.assertTrue(os.path.isdir(cfg["pool_dir"]), cfg["pool_dir"])
        self.assertTrue(os.path.isfile(cfg["variants"]), cfg["variants"])
        self.assertTrue(cfg["data_dir"].endswith("auto_quick"), cfg["data_dir"])

    def test_preset_values_applied(self):
        cfg = auto_variants.resolve_config("medium", {})
        self.assertEqual(cfg["reps"], auto_variants.PRESETS["medium"]["reps"])
        self.assertEqual(cfg["max_variants"], auto_variants.PRESETS["medium"]["max_variants"])

    def test_overrides_beat_preset(self):
        cfg = auto_variants.resolve_config(
            "large",
            {"reps": 99, "games": 7, "workers": 3, "max_variants": 2,
             "sim_budget": 1234, "data_dir": "/tmp/override_dir"},
        )
        self.assertEqual(cfg["reps"], 99)
        self.assertEqual(cfg["games"], 7)
        self.assertEqual(cfg["workers"], 3)
        self.assertEqual(cfg["max_variants"], 2)
        self.assertEqual(cfg["sim_budget"], 1234)
        self.assertEqual(cfg["data_dir"], "/tmp/override_dir")

    def test_pool_dir_override(self):
        cfg = auto_variants.resolve_config("quick", {"pool_dir": "/tmp/somewhere"})
        self.assertEqual(cfg["pool_dir"], "/tmp/somewhere")


class BuildArgvTest(unittest.TestCase):
    def test_contains_required_and_knobs(self):
        cfg = auto_variants.resolve_config("quick", {})
        argv = auto_variants.build_run_argv(cfg)
        self.assertIn("--jar", argv)
        self.assertEqual(argv[argv.index("--jar") + 1], cfg["jar"])
        self.assertIn("--pool-dir", argv)
        self.assertEqual(argv[argv.index("--pool-dir") + 1], cfg["pool_dir"])
        self.assertEqual(argv[argv.index("--reps") + 1], str(cfg["reps"]))
        self.assertEqual(argv[argv.index("--games") + 1], str(cfg["games"]))
        self.assertEqual(argv[argv.index("--workers") + 1], str(cfg["workers"]))
        self.assertEqual(argv[argv.index("--data-dir") + 1], cfg["data_dir"])
        self.assertEqual(argv[argv.index("--sim-budget") + 1], str(cfg["sim_budget"]))
        self.assertIn("--cwd", argv)
        self.assertIn("--cardsfolder", argv)

    def test_max_variants_omitted_when_none(self):
        cfg = auto_variants.resolve_config("large", {})
        argv = auto_variants.build_run_argv(cfg)
        self.assertNotIn("--max-variants", argv)

    def test_max_variants_included_when_set(self):
        cfg = auto_variants.resolve_config("quick", {"max_variants": 3})
        argv = auto_variants.build_run_argv(cfg)
        self.assertEqual(argv[argv.index("--max-variants") + 1], "3")


class MainWrapTest(unittest.TestCase):
    @mock.patch("run_variants.main")
    def test_main_calls_run_variants_main(self, mock_main):
        auto_variants.main(["--preset", "quick", "--data-dir", "/tmp/wrap_test"])
        mock_main.assert_called_once()
        run_argv = mock_main.call_args[0][0]
        self.assertIsInstance(run_argv, list)
        self.assertEqual(run_argv[run_argv.index("--data-dir") + 1], "/tmp/wrap_test")
        self.assertEqual(run_argv[run_argv.index("--reps") + 1], "1")
        self.assertEqual(run_argv[run_argv.index("--max-variants") + 1], "0")

    @mock.patch("run_variants.main")
    def test_main_override_flags_propagate(self, mock_main):
        auto_variants.main(["--preset", "medium", "--reps", "5", "--max-variants", "1"])
        run_argv = mock_main.call_args[0][0]
        self.assertEqual(run_argv[run_argv.index("--reps") + 1], "5")
        self.assertEqual(run_argv[run_argv.index("--max-variants") + 1], "1")


if __name__ == "__main__":
    unittest.main()
