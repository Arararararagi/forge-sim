import os
import csv
import argparse
import deck_variant as dv
import generate_candidates as gc # Reuse the EDHREC/heuristic logic

def generate_subbranches(iter1_data_dir: str, cardsfolder: str, max_subbranches: int = 10):
    csv_path = os.path.join(iter1_data_dir, "matrix-by-variant.csv")
    if not os.path.exists(csv_path):
        raise ValueError(f"Iteration 1 report not found at {csv_path}")

    # 1. Find the graduates from Iteration 1
    graduates = []
    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        baseline_share = next((float(r['share']) for r in reader if r['variant'] == 'baseline'), 0.0)
        f.seek(0)
        next(reader) # skip header
        for row in reader:
            if row['variant'] == 'baseline': continue
            if float(row['ci_lo']) > baseline_share and int(row['completed']) >= 50:
                graduates.append({
                    'id': row['variant'],
                    'path': os.path.join(iter1_data_dir, "variants", f"{row['variant']}.dck")
                })

    if not graduates:
        print("[Warn] No graduates from Iteration 1 to sub-branch from.")
        return

    print(f"[Info] Found {len(graduates)} graduates to sub-branch from.")

    # 2. Generate new candidates based on the original base deck's color identity
    # (Or you could re-evaluate based on the new commander's identity!)
    base_dck_path = "/Users/teclas/Library/Application Support/Forge/decks/commander/Keen Engineering Modded.dck"
    with open(base_dck_path, "r") as f:
        base_deck = dv.parse_dck(f.read())

    all_combos = gc.fetch_edhrec_combos("u").union(gc.fetch_edhrec_combos("ub"))

    # Get top adds/cuts (excluding cards already in the graduate decks)
    add_candidates = []
    index = dv.load_card_names(cardsfolder)
    for name in index.keys():
        score = gc.score_card(cardsfolder, name, is_add=True, combo_cards=all_combos)
        if score > -100:
            add_candidates.append((name, score))
    add_candidates.sort(key=lambda x: x[1], reverse=True)

    # 3. Write the Iteration 2 YAML
    iter2_dir = os.path.join(os.path.dirname(iter1_data_dir), "iter2")
    os.makedirs(iter2_dir, exist_ok=True)
    yaml_path = os.path.join(iter2_dir, "keen_variants.yaml")

    with open(yaml_path, "w") as f:
        f.write("variants:\n")
        f.write("  - id: baseline\n")
        f.write(f"    base: {os.path.abspath(base_dck_path)}\n")
        f.write("    swaps: []\n")

        branch_count = 0
        for grad in graduates:
            if branch_count >= max_subbranches: break

            # Pick a new add/cut that isn't already in the graduate deck
            with open(grad['path'], "r") as gf:
                grad_deck = dv.parse_dck(gf.read())
            grad_names = {dv.card_name(t) for t in grad_deck["main"] + grad_deck["commander"]}

            # Find a valid cut from the graduate deck (CMC >= 5)
            cut_name = None
            for token in grad_deck["main"]:
                name = dv.card_name(token)
                if dv.card_qty(token) == 1 and gc.score_card(cardsfolder, name, is_add=False, combo_cards=all_combos) > -100:
                    cut_name = name
                    break

            # Find a valid add
            add_name = None
            for name, _ in add_candidates[:50]:
                if name not in grad_names:
                    add_name = name
                    break

            if cut_name and add_name:
                f.write(f"  - id: iter2_{grad['id']}__{add_name.replace(' ', '_')}\n")
                f.write(f"    base: {os.path.abspath(grad['path'])}\n") # <-- THE SUB-BRANCH MAGIC
                f.write("    swaps:\n")
                f.write(f"      - add: {add_name}\n")
                f.write(f"        cut: {cut_name}\n")
                branch_count += 1

    print(f"[Success] Generated {branch_count} sub-branches to {yaml_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--iter1-data-dir", required=True)
    parser.add_argument("--cardsfolder", required=True)
    parser.add_argument("--max-subbranches", type=int, default=10)
    args = parser.parse_args()
    generate_subbranches(args.iter1_data_dir, args.cardsfolder, args.max_subbranches)
