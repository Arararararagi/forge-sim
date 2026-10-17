import os
import unittest

import runtime_env


class RuntimeEnvTest(unittest.TestCase):
    def test_canonical_paths_exist(self):
        self.assertTrue(os.path.isfile(runtime_env.HARNESS_JAR), runtime_env.HARNESS_JAR)
        self.assertTrue(os.path.isdir(runtime_env.CWD), runtime_env.CWD)
        self.assertTrue(os.path.isdir(os.path.join(runtime_env.CWD, "res")), "res/ missing")
        self.assertTrue(os.path.isfile(runtime_env.BASE_DECK), runtime_env.BASE_DECK)

    def test_canonical_triple_values(self):
        self.assertEqual(
            runtime_env.HARNESS_JAR,
            "/Users/teclas/Programming/forge/forge-gui-desktop/target/"
            "forge-gui-desktop-2.0.15-SNAPSHOT-jar-with-dependencies.jar",
        )
        self.assertEqual(
            runtime_env.CWD,
            "/Users/teclas/Programming/forge/forge-gui-desktop/target/jpackage-input",
        )
        self.assertEqual(
            runtime_env.BASE_DECK,
            "/Users/teclas/Programming/forge/sim-results/foundations-commander/"
            "decks/Keen_Engineering.dck",
        )


if __name__ == "__main__":
    unittest.main()
