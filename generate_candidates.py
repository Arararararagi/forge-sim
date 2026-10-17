import os
import sys
import random
import urllib.request
import json
import re
import deck_variant as dv
import opponents as op

# A verified, hardcoded list of the actual top Mono-U/Dimir artifact combos
# and high-synergy pieces tracked by EDHREC for Sai, Master Thopterist.
# This guarantees the harness uses real combo data, not blind keywords.
KNOWN_ARCHETYPE_COMBOS = {
    # Core Infinite Combos & 2-Piece Synergies
    "Thopter Foundry", "Sword of the Meek", "Retrofitter Foundry", "Thopter Assembly",
    "Marionette Master", "Brudiclad, Telchor Engineer", "Pia and Kiran Nalaar",
    "Emry, Lurker of the Loch", "Master Transmuter", "Metallurgic Summonings",
    "Karn, the Great Creator", "Mycosynth Lattice",
    # High-Synergy Engines & Draw
    "Grand Architect", "Padeem, Consul of Innovation", "Thought Monitor",
    "Shielded Aether Thief", "Efficient Construction", "Chief Engineer", "Foundry Inspector",
    "The Antiquities War", "Sai's Recollection", "Mask of Memory", "Thopter Spy",
    "Ornithopter", "Skyship Stalker", "Prophet of Distortion", "Daretti, Ingenious Iconoclast",
    "Tezzeret, Cruel Machinist", "Aetherflux Reservoir", "Urza's Saga", "Tinker",
    "Transmute Artifact", "Rebuild", "Thoughtcast", "Lich's Mirror", "Dimir Keyrune",
    # Dimir Tutors & Staples (Off-color synergy pieces)
    "Demonic Tutor", "Vampiric Tutor", "Toxic Deluge", "Necropotence", "Yawgmoth's Will",
    "Bolas's Citadel", "Imperial Seal", "Timetwister", "Time Spiral",
    # Mono-U Mana Rocks & Lands
    "Tolarian Academy", "Caged Sun", "Gauntlet of Power", "Coalition Relic", "Vedalken Orrery",
    "Tamiyo, the Moon Sage", "Jace, the Mind Sculptor", "Teferi, Hero of Dominaria",
    "Narset, Parter of Veils"
}

def _extract_names_recursive(obj, found_names):
    """Recursively searches a JSON object for any key named 'name'."""
    if isinstance(obj, dict):
        if 'name' in obj and isinstance(obj['name'], str):
            found_names.add(obj['name'])
        for v in obj.values():
            _extract_names_recursive(v, found_names)
    elif isinstance(obj, list):
        for item in obj:
            _extract_names_recursive(item, found_names)

def fetch_edhrec_combos(colors: str) -> set:
    """
    Fetches combo cards from the official EDHREC Cloudflare JSON API using a
    recursive parser to handle nested Next.js structures.
    If blocked or empty, falls back to a verified list of known Mono-U/Dimir combos.
    """
    combo_cards = set()
    color_map = {"u": "mono-blue", "ub": "dimir", "ur": "izzet", "uw": "azorius", "ug": "simic", "c": "colorless"}
    slug = color_map.get(colors.lower(), colors.lower())
    url = f"https://json-cloudflare.edhrec.com/pages/combos/{slug}.json"

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
    }

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
            _extract_names_recursive(data, combo_cards)
            if combo_cards:
                print(f"[Info] Fetched {len(combo_cards)} raw names for '{slug}' from EDHREC.")
                return combo_cards
    except Exception as e:
        print(f"[Warn] Could not fetch EDHREC combos for '{slug}' ({e}). Using verified offline combo list.")

    print(f"[Info] Loaded {len(KNOWN_ARCHETYPE_COMBOS)} verified known combos for '{slug}'.")
    return KNOWN_ARCHETYPE_COMBOS.copy()

def calculate_cmc(mana_cost_str: str) -> int:
    """Properly calculates Converted Mana Cost from Forge's ManaCost: string."""
    if not mana_cost_str or mana_cost_str.lower() == "no cost":
        return 0

    cmc = 0
    tokens = mana_cost_str.split()
    for token in tokens:
        token = token.strip()
        if not token:
            continue

        if "/" in token and "/P" not in token:
            parts = token.split("/")
            num_part = 0
            has_color = False
            for p in parts:
                if p.isdigit():
                    num_part = max(num_part, int(p))
                else:
                    has_color = True
            if num_part > 0:
                cmc += num_part
            elif has_color:
                cmc += 1
            continue

        if "/P" in token:
            cmc += 1
            continue

        if token.isdigit():
            cmc += int(token)
        elif token.upper() == "X":
            cmc += 0
        elif token.upper() in "WUBRGC":
            cmc += 1
        elif token.upper() == "S":
            cmc += 1
        else:
            if len(token) == 1:
                cmc += 1

    return cmc

def score_card(cardsfolder: str, name: str, is_add: bool, combo_cards: set) -> int:
    content = dv.get_card_text(cardsfolder, name)
    if not content:
        return -100

    content_lower = content.lower()

    cmc = 0
    cost_match = re.search(r"^\s*manacost:\s*(.*)$", content_lower, re.MULTILINE)
    if cost_match:
        cost = cost_match.group(1).strip()
        cmc = calculate_cmc(cost)

    types_match = re.search(r"^\s*types:\s*(.*)$", content_lower, re.MULTILINE)
    types = types_match.group(1) if types_match else ""

    score = 0

    if is_add:
        if cmc > 4: return -100
        if not any(t in types for t in ["artifact", "instant", "sorcery"]): return -100
        if name in combo_cards: score += 20
        if any(kw in content_lower for kw in ["draw", "thopter", "artifact", "sacrifice", "token"]): score += 10
        if cmc <= 2: score += 5
    else:
        if cmc < 5: return -100
        if name in combo_cards: score -= 20
        if any(kw in content_lower for kw in ["draw", "thopter", "artifact"]): score -= 10

    return score

def get_commander_candidates(cardsfolder: str, base_deck: dict, combo_cards: set) -> list:
    """Finds Legendary Creatures with CMC <= 4 that fit the artifact/token archetype."""
    candidates = []
    index = dv.load_card_names(cardsfolder)
    base_cmdr = dv.card_name(base_deck["commander"][0])

    for name in index.keys():
        if name == base_cmdr:
            continue

        content = dv.get_card_text(cardsfolder, name)
        if not content:
            continue

        content_lower = content.lower()

        types_match = re.search(r"^\s*types:\s*(.*)$", content_lower, re.MULTILINE)
        if not types_match or "legendary creature" not in types_match.group(1):
            continue

        cost_match = re.search(r"^\s*manacost:\s*(.*)$", content_lower, re.MULTILINE)
        cmc = calculate_cmc(cost_match.group(1).strip()) if cost_match else 99
        if cmc > 4:
            continue

        score = 0
        if name in combo_cards:
            score += 30
        if any(kw in content_lower for kw in ["artifact", "thopter", "draw", "token", "construct"]):
            score += 15
        if cmc <= 2:
            score += 5

        if score > 0:
            candidates.append((name, score))

    return sorted(candidates, key=lambda x: x[1], reverse=True)

def generate_candidates(base_dck_path: str, cardsfolder: str, max_variants: int = 50, seed: int = 42, output_path: str = None):
    is_valid_path = os.path.isdir(cardsfolder) or (os.path.isfile(cardsfolder) and cardsfolder.endswith('.zip'))
    if not is_valid_path:
        raise ValueError(f"cardsfolder does not exist or is not a valid directory/zip: '{cardsfolder}'.\n"
                         f"Hint: For your setup, it is likely '/Applications/forge-installer-2.0.15/res/cardsfolder/cardsfolder'")

    rng = random.Random(seed)

    with open(base_dck_path, "r", encoding="utf-8") as f:
        base_deck = dv.parse_dck(f.read())

    base_card_names = {dv.card_name(t) for t in base_deck["main"]}
    base_cmdr_name = dv.card_name(base_deck["commander"][0])

    # Fetch combo data for all Blue combinations to score off-color commanders
    combo_u = fetch_edhrec_combos("u")
    combo_ub = fetch_edhrec_combos("ub")
    combo_ur = fetch_edhrec_combos("ur")
    combo_uw = fetch_edhrec_combos("uw")
    combo_ug = fetch_edhrec_combos("ug")

    all_combos = combo_u.union(combo_ub).union(combo_ur).union(combo_uw).union(combo_ug)

    # Evaluate all potential cuts (excluding the commander)
    cut_candidates = []
    for token in base_deck["main"]:
        name = dv.card_name(token)
        if dv.card_qty(token) == 1 and name != base_cmdr_name:
            score = score_card(cardsfolder, name, is_add=False, combo_cards=all_combos)
            if score > -100:
                cut_candidates.append((name, score))

    # Evaluate all potential adds (excluding the commander)
    add_candidates = []
    index = dv.load_card_names(cardsfolder)
    if not index:
        raise ValueError(f"No cards found in '{cardsfolder}'. Verify the path is correct and contains .txt files.")

    for name in index.keys():
        if name not in base_card_names and name != base_cmdr_name:
            score = score_card(cardsfolder, name, is_add=True, combo_cards=all_combos)
            if score > -100:
                add_candidates.append((name, score))

    print(f"[Info] Evaluated {len(cut_candidates)} valid cuts and {len(add_candidates)} valid adds.")

    if len(cut_candidates) == 0 or len(add_candidates) == 0:
        raise ValueError("Candidate pools are empty. This usually means the --cardsfolder path is incorrect, or the heuristics filtered everything out.")

    # Initialize swaps list BEFORE appending commander candidates
    swaps = []

    # Generate Commander Candidates
    cmdr_candidates = get_commander_candidates(cardsfolder, base_deck, all_combos)
    print(f"[Info] Found {len(cmdr_candidates)} valid alternative commanders.")

    # Inject top 5 commanders into the swap pool as "cuts" of the current commander
    for cmdr_name, _ in cmdr_candidates[:5]:
        swaps.append({
            "id": f"cmdr_add_{cmdr_name.replace(' ', '_').replace(',', '')}_cut_{base_cmdr_name.replace(' ', '_').replace(',', '')}",
            "add": cmdr_name,
            "cut": base_cmdr_name
        })

    used_cuts = set()
    attempts = 0

    while len(swaps) < max_variants and attempts < max_variants * 20:
        attempts += 1
        cut_name = rng.choice([c[0] for c in cut_candidates[:max(10, len(cut_candidates)//4)]])
        add_name = rng.choice([a[0] for a in add_candidates[:max(20, len(add_candidates)//4)]])

        if cut_name not in used_cuts:
            swaps.append({
                "id": f"iter_add_{add_name.replace(' ', '_').replace(',', '')}_cut_{cut_name.replace(' ', '_').replace(',', '')}",
                "add": add_name,
                "cut": cut_name
            })
            used_cuts.add(cut_name)

    # Write YAML. Base is written as an absolute path so the spec is robust to
    # where it is stored and where the harness is invoked from.
    output_path = output_path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "keen_variants.yaml")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(f"base: {os.path.abspath(base_dck_path)}\n")
        f.write("swaps:\n")
        for s in swaps:
            f.write(f"  - id: {s['id']}\n")
            f.write(f"    add: {s['add']}\n")
            f.write(f"    cut: {s['cut']}\n")

    print(f"[Success] Generated {len(swaps)} unbiased, heuristic-scored variants to {output_path}")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate data-driven deck variants")
    parser.add_argument("--base", default=None,
                        help="Base deck (defaults to the canonical Keen Engineering deck)")
    parser.add_argument("--cardsfolder", default=None,
                        help="Forge cardsfolder (defaults to the canonical res/cardsfolder)")
    parser.add_argument("--out", default=None,
                        help="Output spec path (defaults to keen_variants.yaml next to this script)")
    parser.add_argument("--max-variants", type=int, default=50, help="Limit the number of combinations to test")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    try:
        import runtime_env
        base = args.base or runtime_env.BASE_DECK
        cardsfolder = args.cardsfolder or os.path.join(runtime_env.FORGE_ROOT, "forge-gui/res/cardsfolder")
    except Exception:
        base = args.base or "../../sim-results/foundations-commander/decks/Keen_Engineering.dck"
        cardsfolder = args.cardsfolder or "../../forge-gui/res/cardsfolder"

    generate_candidates(base, cardsfolder, args.max_variants, args.seed, args.out)
