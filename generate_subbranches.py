import os
import csv
import argparse
import deck_variant as dv
import opponents as op
import generate_candidates as gc  # Reuse the EDHREC/heuristic logic


def _load_graduates(csv_path: str, min_games: int):
    """Read the iteration-1 report CSV and return (baseline_share, [variant ids])."""
    if not os.path.exists(csv_path):
        raise ValueError(f"Iteration report not found at {csv_path}")

    rows = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    baseline_share = 0.0
    for row in rows:
        if row["variant"] == "baseline":
            baseline_share = float(row.get("win_share") or 0.0)
            break

    graduates = []
    for row in rows:
        if row["variant"] == "baseline":
            continue
        games = int(float(row.get("games") or 0))
        ci_lo = float(row.get("ci_lo") or 0.0)
        if ci_lo > baseline_share and games >= min_games:
            graduates.append(row["variant"])
    return baseline_share, graduates


def _pick_new_swap(cardsfolder, grad_deck, all_combos, existing_names, existing_caps):
    """Pick one (add, cut) swap not already represented in the graduate deck."""
    cut_name = None
    for token in grad_deck["main"]:
        name = dv.card_name(token)
        if name in existing_caps:
            continue
        if dv.card_qty(token) == 1 and gc.score_card(cardsfolder, name, is_add=False, combo_cards=all_combos) > -100:
            cut_name = name
            break

    add_name = None
    index = dv.load_card_names(cardsfolder)
    scored = []
    for name in index.keys():
        if name in existing_names or name in (existing_caps or set()):
            continue
        score = gc.score_card(cardsfolder, name, is_add=True, combo_cards=all_combos)
        if score > -100:
            scored.append((name, score))
    scored.sort(key=lambda x: x[1], reverse=True)
    for name, _ in scored[:50]:
        if name not in existing_names:
            add_name = name
            break

    if cut_name and add_name:
        return {"add": add_name, "cut": cut_name}
    return None


def generate_subbranches(iter1_data_dir: str, cardsfolder: str, spec_path: str,
                         max_subbranches: int = 10, min_games: int = 20):
    csv_path = os.path.join(iter1_data_dir, "matrix-by-variant.csv")
    baseline_share, graduates = _load_graduates(csv_path, min_games)
    print(f"[Info] Baseline win share: {baseline_share:.3f}")
    if not graduates:
        print("[Warn] No graduates from iteration 1 to sub-branch from.")
        return

    print(f"[Info] Found {len(graduates)} graduates to sub-branch from.")

    # Load the iteration-1 spec so we know each graduate's exact change set.
    iter1_variants = {v["id"]: v for v in op.load_variants(spec_path)}
    base_deck_path = op.resolve_base(spec_path)

    with open(base_deck_path, "r", encoding="utf-8") as f:
        base_deck = dv.parse_dck(f.read())
    base_cmdr = dv.card_name(base_deck["commander"][0]) if base_deck["commander"] else ""

    all_combos = gc.fetch_edhrec_combos("u").union(gc.fetch_edhrec_combos("ub"))

    iter2_dir = os.path.join(os.path.dirname(os.path.abspath(iter1_data_dir)), "iter2")
    os.makedirs(iter2_dir, exist_ok=True)
    yaml_path = os.path.join(iter2_dir, "keen_variants.yaml")

    branch_count = 0
    written = []
    for grad_id in graduates:
        if branch_count >= max_subbranches:
            break
        parent = iter1_variants.get(grad_id)
        if parent is None:
            print(f"[Warn] Graduate '{grad_id}' not found in spec {spec_path}; skipping.")
            continue

        # Reconstruct the graduate deck on disk from the parent's changes.
        try:
            swaps = [dict(c) for c in parent["changes"]]
            # Apply existing changes onto a copy to know the current card set.
            grad_deck = dv.apply_swaps(base_deck, swaps)
        except ValueError as e:
            print(f"[Warn] Could not reconstruct '{grad_id}': {e}")
            continue

        existing_names = {dv.card_name(t) for t in grad_deck["main"] + grad_deck["commander"]}
        existing_caps = {c["cut"] for c in swaps} | {c["add"] for c in swaps}

        new_swap = _pick_new_swap(cardsfolder, grad_deck, all_combos, existing_names, existing_caps)
        if not new_swap:
            continue

        sub_id = f"iter2_{grad_id}__add_{new_swap['add'].replace(' ', '_').replace(',', '')}"
        written.append({"id": sub_id, "changes": swaps + [new_swap]})
        branch_count += 1

    with open(yaml_path, "w", encoding="utf-8") as f:
        f.write(f"base: {os.path.abspath(base_deck_path)}\n")
        f.write("swaps:\n")
        for w in written:
            f.write(f"  - id: {w['id']}\n")
            f.write("    changes:\n")
            for ch in w["changes"]:
                f.write(f"      - add: {ch['add']}\n")
                f.write(f"        cut: {ch['cut']}\n")

    print(f"[Success] Generated {branch_count} sub-branches to {yaml_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--iter1-data-dir", required=True)
    parser.add_argument("--cardsfolder", default=None)
    parser.add_argument("--spec", default=None,
                        help="Iteration-1 spec (defaults to keen_variants.yaml next to this script)")
    parser.add_argument("--max-subbranches", type=int, default=10)
    parser.add_argument("--min-games", type=int, default=20)
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    cardsfolder = args.cardsfolder
    if not cardsfolder:
        try:
            import runtime_env
            cardsfolder = os.path.join(runtime_env.FORGE_ROOT, "forge-gui/res/cardsfolder")
        except Exception:
            cardsfolder = os.path.join(script_dir, "../../forge-gui/res/cardsfolder")
    spec_path = args.spec or os.path.join(script_dir, "keen_variants.yaml")

    generate_subbranches(args.iter1_data_dir, cardsfolder, spec_path,
                         args.max_subbranches, args.min_games)
