"""One-command variant campaigns with named presets (quick/medium/large).

Wraps run_variants.main with the canonical runtime defaults recorded by
Task 1 (see runtime_env.py): harness JAR, jpackage cwd, cardsfolder, and
the Keen Engineering base deck via keen_variants.yaml.

Usage:
    python3 auto_variants.py --preset quick
    python3 auto_variants.py --preset medium --reps 5 --workers 8
    python3 auto_variants.py --preset large --data-dir data/my_campaign

Any preset knob (reps, games, workers, max-variants, sim-budget, ...)
can be overridden per-run via CLI flags; explicit flags always win.
"""
import argparse
import os
import sys

import run_variants
import runtime_env

REPO_DIR = os.path.dirname(os.path.abspath(__file__))

DEFAULT_POOL_DIR = os.path.expanduser(
    "~/Library/Application Support/Forge/decks/net/EDHREC Average Decks"
)
DEFAULT_CARDSFOLDER = os.path.join(runtime_env.FORGE_ROOT, "forge-gui/res/cardsfolder")
DEFAULT_VARIANTS = os.path.join(REPO_DIR, "keen_variants.yaml")

PRESETS = {
    # Smoke: baseline only, 1 game, hard sim budget -> minutes, not hours.
    "quick": {
        "reps": 1,
        "games": 1,
        "workers": 1,
        "max_variants": 0,
        "sim_budget": 40000,
        "ai_timeout": 2,
        "clock": 300,
    },
    # First real comparison: a handful of variants, a few independent reps.
    # clock 2000000000 (~63 y) = effectively no wall cap: games play to a
    # winner. Forge's per-decision SIM_BUDGET/AI_TIMEOUT and rule 726 loop
    # handling keep runs finite in practice.
    "medium": {
        "reps": 3,
        "games": 1,
        "workers": 4,
        "max_variants": 5,
        "sim_budget": -1,
        "ai_timeout": 2,
        "clock": 2000000000,
    },
    # Full sweep: every variant, many reps. Long-running.
    "large": {
        "reps": 10,
        "games": 1,
        "workers": 6,
        "max_variants": None,
        "sim_budget": -1,
        "ai_timeout": 2,
        "clock": 2000000000,
    },
}


def resolve_config(preset: str, overrides: dict) -> dict:
    """Merge a preset with canonical defaults and per-run overrides.

    Overrides with value None are ignored so callers can pass argparse
    namespaces directly.
    """
    if preset not in PRESETS:
        raise ValueError(f"Unknown preset {preset!r}; expected one of {sorted(PRESETS)}")

    cfg = {
        # Canonical runtime (Task 1)
        "jar": runtime_env.HARNESS_JAR,
        "cwd": runtime_env.CWD,
        "cardsfolder": DEFAULT_CARDSFOLDER,
        "pool_dir": DEFAULT_POOL_DIR,
        "decks_dir": DEFAULT_POOL_DIR,
        "variants": DEFAULT_VARIANTS,
        "java": "java",
        # Preset knobs
        **PRESETS[preset],
        "preset": preset,
    }
    cfg["data_dir"] = os.path.join(REPO_DIR, "data", f"auto_{preset}")

    for key, value in overrides.items():
        if value is not None:
            cfg[key] = value
    return cfg


def build_run_argv(cfg: dict) -> list:
    """Translate a resolved config into run_variants.main argv."""
    argv = [
        "--jar", cfg["jar"],
        "--decks-dir", cfg["decks_dir"],
        "--pool-dir", cfg["pool_dir"],
        "--cardsfolder", cfg["cardsfolder"],
        "--variants", cfg["variants"],
        "--data-dir", cfg["data_dir"],
        "--reps", str(cfg["reps"]),
        "--games", str(cfg["games"]),
        "--workers", str(cfg["workers"]),
        "--ai-timeout", str(cfg["ai_timeout"]),
        "--clock", str(cfg["clock"]),
        "--java", cfg["java"],
        "--cwd", cfg["cwd"],
        "--sim-budget", str(cfg["sim_budget"]),
    ]
    if cfg["max_variants"] is not None:
        argv += ["--max-variants", str(cfg["max_variants"])]
    return argv


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Run a variant campaign with a named preset (quick/medium/large)."
    )
    parser.add_argument("--preset", choices=sorted(PRESETS), default="quick")
    # Overrides (None = use preset value)
    parser.add_argument("--reps", type=int, default=None)
    parser.add_argument("--games", type=int, default=None)
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--max-variants", type=int, default=None)
    parser.add_argument("--sim-budget", type=int, default=None)
    parser.add_argument("--ai-timeout", type=int, default=None)
    parser.add_argument("--clock", type=int, default=None)
    # Path overrides
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--pool-dir", default=None)
    parser.add_argument("--decks-dir", default=None)
    parser.add_argument("--variants", default=None)
    parser.add_argument("--jar", default=None)
    parser.add_argument("--cwd", default=None)
    parser.add_argument("--cardsfolder", default=None)
    parser.add_argument("--java", default=None)
    args = parser.parse_args(argv)

    overrides = {
        "reps": args.reps,
        "games": args.games,
        "workers": args.workers,
        "max_variants": args.max_variants,
        "sim_budget": args.sim_budget,
        "ai_timeout": args.ai_timeout,
        "clock": args.clock,
        "data_dir": args.data_dir,
        "pool_dir": args.pool_dir,
        "decks_dir": args.decks_dir,
        "variants": args.variants,
        "jar": args.jar,
        "cwd": args.cwd,
        "cardsfolder": args.cardsfolder,
        "java": args.java,
    }
    cfg = resolve_config(args.preset, overrides)
    run_argv = build_run_argv(cfg)

    preset = PRESETS[args.preset]
    units_note = (
        f"{(cfg['max_variants'] if cfg['max_variants'] is not None else 'all')} "
        f"+baseline arms x {cfg['reps']} reps"
    )
    print(
        f"[auto_variants] preset={args.preset} "
        f"(reps={preset['reps']}, games={preset['games']}, workers={preset['workers']}, "
        f"max_variants={preset['max_variants']}, sim_budget={preset['sim_budget']})"
    )
    print(f"[auto_variants] effective: {units_note} -> {cfg['data_dir']}")

    return run_variants.main(run_argv)


if __name__ == "__main__":
    sys.exit(main())
