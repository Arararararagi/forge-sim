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

def resolve_base(path: str) -> str:
    """Resolve only the ``base:`` value of a spec, relative to the spec dir."""
    base_path = ""
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            stripped = raw.strip()
            if stripped.startswith("base:"):
                base_path = _parse_value(stripped.split(":", 1)[1])
                break
    if not base_path:
        raise ValueError(f"No 'base:' found in {path}")
    spec_dir = os.path.dirname(os.path.abspath(path))
    resolved = os.path.normpath(os.path.join(spec_dir, base_path))
    if not os.path.exists(resolved):
        raise ValueError(f"Base deck not found: {resolved}")
    return resolved


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _parse_value(raw: str) -> str:
    return raw.strip().strip('"').strip("'")


def load_variants(path: str) -> list[dict]:
    """Parse a variant spec.

    Supported schema (indentation-sensitive, 2-space steps)::

        base: <path to base deck>
        swaps:
          - id: <variant id>
            add: <single card to add>     # a card name MAY contain commas
            cut: <single card to cut>
          - id: <variant id>
            changes:                      # multi-card replacement
              - add: <card>
                cut: <card>
              - add: <card>
                cut: <card>

    Every returned variant has ``id``, ``base`` (resolved, absolute) and
    ``changes`` (a non-empty list of ``{"add", "cut"}`` single-card swaps).
    The flat ``add``/``cut`` form is normalised into a one-element
    ``changes`` list. Comma-separated card names are NOT split: a card name
    like "Vnwxt, Verbose Host" is a single card. Use the nested ``changes``
    form for genuine multi-card replacements.
    """
    base_path = ""
    variants = []
    current = None       # the variant dict currently being built
    change = None        # the change currently being built (nested form)
    section = None       # "swaps" once seen

    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            indent = _indent(line)

            if indent == 0 and stripped.startswith("base:"):
                base_path = _parse_value(stripped.split(":", 1)[1])
                continue
            if indent == 0 and stripped == "swaps:":
                section = "swaps"
                continue
            if section != "swaps":
                continue

            if indent <= 2 and stripped.startswith("- id:"):
                if current is not None:
                    variants.append(current)
                current = {"id": _parse_value(stripped.split(":", 1)[1]), "changes": []}
                change = None
                continue
            if current is None:
                continue

            # nested change entries live at deeper indentation
            if indent >= 6 and stripped.startswith("- add:"):
                change = {"add": _parse_value(stripped.split(":", 1)[1]), "cut": ""}
                current["changes"].append(change)
                continue

            key, _, raw_val = stripped.partition(":")
            key = key.strip()
            val = _parse_value(raw_val)

            if key == "add":
                # flat form -> start a single change
                change = {"add": val, "cut": ""}
                current["changes"].append(change)
            elif key == "cut":
                if change is None:
                    change = {"add": "", "cut": ""}
                    current["changes"].append(change)
                change["cut"] = val

    if current is not None:
        variants.append(current)

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

        if not v["changes"]:
            raise ValueError(f"Variant '{v['id']}' has no add/cut changes.")

        for ch in v["changes"]:
            if not ch["add"] or not ch["cut"]:
                raise ValueError(f"Variant '{v['id']}' has an incomplete add/cut change: {ch}")
            if ch["add"] == ch["cut"]:
                raise ValueError(f"Variant '{v['id']}' adds and cuts the same card: {ch['add']}")
            if ch["add"] in base_names and ch["add"] not in {c["cut"] for c in v["changes"]}:
                raise ValueError(f"Add card '{ch['add']}' already in base deck.")
            if ch["cut"] not in base_names and ch["cut"] not in {c["add"] for c in v["changes"]}:
                raise ValueError(f"Cut card '{ch['cut']}' not in base deck.")

        # Attach the resolved base path to each variant for consistency
        v["base"] = base_path

    return variants
