import os
import sys
import json
import random
import subprocess
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

import deck_variant as dv
import opponents as op

def pod_command(jar: str, focal_deck: str, opponents: list, profiles: list, games: int, seed: int, ai_timeout: int, clock: int, out: str, worker_home: str, java: str, sim_budget: int = -1) -> list:
    cmd = [java, f"-Duser.home={worker_home}", "-Xmx2048m", "-Dfile.encoding=UTF-8", "-jar", os.path.abspath(jar),
           "sim", "-f", "commander", "-d", focal_deck] + opponents + ["-a"] + profiles + [
           "-n", str(games), "-s", str(seed), "-A", str(ai_timeout), "-c", str(clock), "-q", "-o", out]
    if sim_budget > 0:
        cmd.extend(["-B", str(sim_budget)])
    return cmd

def variant_units(variants: list, reps: int, pool: list, seed_base: int) -> list:
    units = []
    # Always include baseline
    all_variants = [{"id": "baseline", "add": None, "cut": None}] + variants
    
    for rep in range(reps):
        seed = seed_base + rep
        rng = random.Random(seed)
        pod = op.sample_pod(pool, 3, rng)
        
        for v in all_variants:
            units.append({
                "variant_id": v["id"],
                "add": v.get("add"),
                "cut": v.get("cut"),
                "opponents": pod,
                "seed": seed,
                "rep": rep
            })
    return units

def focal_deck_for(variant_id: str, args) -> str:
    """Resolve the focal deck path for a unit.

    The variant deck path must be the value returned by
    deck_variant.make_variant during materialization (stored in
    args.variant_paths). Reconstructing the filename from variant_id is
    fragile and previously caused silent deck-load failures.
    """
    if variant_id == "baseline":
        return args.base_deck
    try:
        return args.variant_paths[variant_id]
    except (KeyError, AttributeError):
        raise KeyError(
            f"Variant '{variant_id}' was not materialized (no path recorded); "
            "it may have failed to build or was removed from the spec."
        )


def run_unit(unit, args, lock, counters):
    variant_id = unit["variant_id"]
    # Write per-unit JSONL (include rep/seed) so reps don't overwrite
    out_file = os.path.join(args.data_dir, f"{variant_id}_r{unit['rep']}_s{unit['seed']}.jsonl")
    worker_home = os.path.join(args.data_dir, f"worker_{variant_id}_r{unit['rep']}_s{unit['seed']}")
    os.makedirs(worker_home, exist_ok=True)
    try:
        focal_deck = focal_deck_for(variant_id, args)
    except KeyError as e:
        with lock:
            counters["error"] += 1
        print(f"[Error] {e}")
        return
    if not os.path.isfile(focal_deck):
        with lock:
            counters["error"] += 1
        print(f"[Error] Focal deck missing for {variant_id}: {focal_deck}")
        return
    cmd = pod_command(
        args.jar, focal_deck, unit["opponents"], ["Default"] * 4,
        args.games, unit["seed"], args.ai_timeout, args.clock, out_file, worker_home, args.java, args.sim_budget
    )
    try:
        subprocess.run(cmd, cwd=args.cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=args.clock + 60)
    except subprocess.TimeoutExpired:
        with lock:
            counters["timeout"] += 1
        return
    except Exception as e:
        with lock:
            counters["error"] += 1
        print(f"Error running {variant_id}: {e}")
        return

    # Forge returns exit code 0 even when it could not load a deck; the only
    # reliable signal is whether it produced any JSONL rows. Treat empty
    # output as a failure so it is never silently counted as a completed game.
    produced = count_jsonl_rows(out_file)
    if produced <= 0:
        with lock:
            counters["error"] += 1
        print(f"[Error] {variant_id} produced no games (deck load failed?): {focal_deck}")
        return
    with lock:
        counters["completed"] += 1
        counters["games"] = counters.get("games", 0) + produced


def count_jsonl_rows(path: str) -> int:
    if not os.path.isfile(path):
        return 0
    rows = 0
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows += 1
    return rows
def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--jar", default="/Users/teclas/Programming/forge/forge-gui-desktop/target/forge-gui-desktop-2.0.15-SNAPSHOT-jar-with-dependencies.jar")
    parser.add_argument("--decks-dir", required=True)
    parser.add_argument("--pool-dir", action="append", required=True)
    parser.add_argument("--cardsfolder", default="/Users/teclas/Programming/forge/forge-gui/res/cardsfolder")
    # FIX: Changed default from "scripts/simlab/keen_variants.yaml" to "keen_variants.yaml"
    parser.add_argument("--variants", default="keen_variants.yaml")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--reps", type=int, default=10)
    parser.add_argument("--games", type=int, default=1)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--ai-timeout", type=int, default=2)
    parser.add_argument("--clock", type=int, default=300)
    parser.add_argument("--java", default="java")
    parser.add_argument("--cwd", default="/Users/teclas/Programming/forge/forge-gui-desktop/target/jpackage-input")
    parser.add_argument("--sim-budget", type=int, default=-1)
    parser.add_argument("--max-variants", type=int, default=None, help="Limit how many variants from the YAML to test")
    args = parser.parse_args(argv)
    
    os.makedirs(args.data_dir, exist_ok=True)
    variants_dir = os.path.join(args.data_dir, "variants")
    os.makedirs(variants_dir, exist_ok=True)
    
    print("[1/4] Building opponent pool...")
    pool = op.build_pool(args.pool_dir, args.cardsfolder, exclude_names=["Keen Engineering"])
    print(f"      Found {len(pool)} valid opponent decks.")
    
    print("[2/4] Loading variant spec...")
    spec_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), args.variants) if not os.path.isabs(args.variants) else args.variants
    variants = op.load_variants(spec_path)
    
    if args.max_variants is not None:
        variants = variants[:args.max_variants]
        print(f"      Limited to {args.max_variants} variants as requested.")
        
    # load_variants resolves the base path (absolute) and attaches it to each
    # variant. Fall back to the spec's base when there are no variants.
    if variants:
        args.base_deck = variants[0]["base"]
    else:
        args.base_deck = op.resolve_base(spec_path)
    
    print("[3/4] Materializing variant decks...")
    args.variant_paths = {}
    materialized = []
    skipped = {}
    for v in variants:
        try:
            path = dv.make_variant(args.base_deck, args.cardsfolder, v["changes"], variants_dir)
            args.variant_paths[v["id"]] = path
            materialized.append(v)
        except ValueError as e:
            print(f"      Skipping {v['id']}: {e}")
            skipped[v["id"]] = str(e)
    variants = materialized
            
    print(f"[4/4] Running campaign ({len(variants)+1} arms x {args.reps} reps = {(len(variants)+1)*args.reps} units)...")
    units = variant_units(variants, args.reps, pool, seed_base=4242)
    
    lock = threading.Lock()
    counters = {"completed": 0, "timeout": 0, "error": 0, "games": 0}
    
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(run_unit, u, args, lock, counters) for u in units]
        for f in as_completed(futures):
            pass # Just wait for completion
            
    manifest = {
        "variants": [v["id"] for v in variants] + ["baseline"],
        "skipped": skipped,
        "variant_paths": dict(args.variant_paths),
        "reps": args.reps,
        "games": args.games,
        "workers": args.workers,
        "pool_size": len(pool),
        "counters": counters
    }
    with open(os.path.join(args.data_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)
        
    print(f"[Done] Campaign complete. Completed units: {counters['completed']}, Games: {counters['games']}, Timeouts: {counters['timeout']}, Errors: {counters['error']}")
    print(f"       Run: python3 report.py --data-dir {args.data_dir}")

if __name__ == "__main__":
    main()
