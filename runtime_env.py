"""Canonical runtime environment for the forge-sim harness.

Task 1 of docs/plans/2025-10-17-automate-thousands-of-games.md records the
verified triple (JAR, cwd, base deck) that the headless `sim` mode needs:

- HARNESS_JAR: repo-built harness JAR (has `-o` JSONL sink, `-B` sim budget,
  direct deck paths).
- CWD: working directory containing `res/`; the JAR must be launched here.
- BASE_DECK: canonical 100-card "Keen Engineering" commander deck used as the
  focal deck (seat 0).

Smoke-verified with a single commander game (`-n 1 -s 42 -A 2 -c 300
-B 40000`), producing valid JSONL with all required schema keys.
"""
import os

FORGE_ROOT = "/Users/teclas/Programming/forge"

HARNESS_JAR = os.path.join(
    FORGE_ROOT,
    "forge-gui-desktop/target/forge-gui-desktop-2.0.15-SNAPSHOT-jar-with-dependencies.jar",
)
CWD = os.path.join(FORGE_ROOT, "forge-gui-desktop/target/jpackage-input")
BASE_DECK = os.path.join(
    FORGE_ROOT,
    "sim-results/foundations-commander/decks/Keen_Engineering.dck",
)

CANONICAL_TRIPLE = {
    "jar": HARNESS_JAR,
    "cwd": CWD,
    "base_deck": BASE_DECK,
}
