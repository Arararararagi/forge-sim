import os
import random
import deck_variant as dv

def parse_simple_yaml(path: str) -> dict:
    result = {"base": "", "swaps": []}
    current_swap = None
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("base:"):
                result["base"] = line.split(":", 1)[1].strip()
            elif line.strip().startswith("- id:"):
                if current_swap:
                    result["swaps"].append(current_swap)
                current_swap = {"id": line.split(":", 1)[1].strip()}
            elif current_swap is not None and ":" in line:
                key, val = line.strip().split(":", 1)
                current_swap[key.strip()] = val.strip().strip('"').strip("'")
        if current_swap:
            result["swaps"].append(current_swap)
    return result

def build_pool(pool_dirs: list, cardsfolder: str, exclude_names: list) -> list:
    valid_decks = []
    basics = {"island", "plains", "swamp", "mountain", "forest", "wastes"}
    basics.update({f"snow-covered {b}" for b in ["island", "plains", "swamp", "mountain", "forest"]})
    
    for pdir in pool_dirs:
        if not os.path.isdir(pdir):
            print(f"Warning: Pool dir not found: {pdir}")
            continue
        for fname in os.listdir(pdir):
            if not fname.lower().endswith(".dck"):
                continue
            
            # Exclude check
            skip = False
            for ex in exclude_names:
                if ex.lower() in fname.lower():
                    skip = True
                    break
            if skip:
                continue
                
            fpath = os.path.join(pdir, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    deck = dv.parse_dck(f.read())
                
                if len(deck["commander"]) not in (1, 2):
                    print(f"Skip {fname}: Invalid commander count ({len(deck['commander'])})")
                    continue
                if dv.total_qty(deck["main"]) < 1:
                    print(f"Skip {fname}: Empty main deck")
                    continue
                    
                seen = {}
                valid = True
                for token in deck["main"] + deck["commander"]:
                    name = dv.card_name(token).lower()
                    if name not in basics:
                        seen[name] = seen.get(name, 0) + 1
                        if seen[name] > 1:
                            print(f"Skip {fname}: Duplicate non-basic '{name}'")
                            valid = False
                            break
                    if not dv.forge_has_card(cardsfolder, dv.card_name(token)):
                        print(f"Skip {fname}: Card '{dv.card_name(token)}' not in DB")
                        valid = False
                        break
                if valid:
                    valid_decks.append(os.path.abspath(fpath))
            except Exception as e:
                print(f"Skip {fname}: Parse error ({e})")
                
    return sorted(valid_decks)

def sample_pod(pool: list, k: int, rng: random.Random) -> list:
    if len(pool) < k:
        raise ValueError(f"Pool size {len(pool)} is less than required pod size {k}")
    return rng.sample(pool, k)

def load_variants(path: str) -> list[dict]:
    variants = []
    base_path = ""
    current_swap = None
    in_swaps = False

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip()
            if not line or line.startswith("#"):
                continue

            if line.startswith("base:"):
                base_path = line.split(":", 1)[1].strip()
            elif line.strip() == "swaps:":
                in_swaps = True
            elif in_swaps and line.strip().startswith("- id:"):
                if current_swap:
                    variants.append(current_swap)
                current_swap = {"id": line.split(":", 1)[1].strip(), "add": "", "cut": ""}
            elif in_swaps and current_swap is not None:
                if line.strip().startswith("add:"):
                    current_swap["add"] = line.split(":", 1)[1].strip()
                elif line.strip().startswith("cut:"):
                    current_swap["cut"] = line.split(":", 1)[1].strip()

        if current_swap:
            variants.append(current_swap)

    if not base_path:
        raise ValueError(f"No 'base:' found in {path}")

    # Resolve base path relative to the spec file's directory
    spec_dir = os.path.dirname(os.path.abspath(path))
    base_path = os.path.normpath(os.path.join(spec_dir, base_path))

    if not os.path.exists(base_path):
        raise ValueError(f"Base deck not found: {base_path}")

    # Validation
    with open(base_path, "r", encoding="utf-8") as f:
        base_deck = dv.parse_dck(f.read())
    base_names = {dv.card_name(t) for t in base_deck["main"] + base_deck["commander"]}

    seen_ids = set()
    for v in variants:
        if v["id"] in seen_ids:
            raise ValueError(f"Duplicate variant id: {v['id']}")
        seen_ids.add(v["id"])

        if v["add"] in base_names:
            raise ValueError(f"Add card '{v['add']}' already in base deck.")
        if v["cut"] not in base_names:
            raise ValueError(f"Cut card '{v['cut']}' not in base deck.")

        # Attach the resolved base path to each variant for consistency
        v["base"] = base_path

    return variants
