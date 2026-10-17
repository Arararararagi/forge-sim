import os
import re
import zipfile

_CARD_INDEX_CACHE = {}

def load_card_names(cardsfolder: str) -> dict:
    global _CARD_INDEX_CACHE
    real_path = os.path.realpath(cardsfolder)
    if real_path in _CARD_INDEX_CACHE:
        return _CARD_INDEX_CACHE[real_path]

    index = {}
    if real_path.endswith('.zip'):
        try:
            with zipfile.ZipFile(real_path, 'r') as zf:
                for file_info in zf.infolist():
                    if file_info.filename.endswith('.txt'):
                        try:
                            with zf.open(file_info.filename) as f:
                                content = f.read().decode('utf-8', errors='ignore')
                                # Read ALL Name: lines in the file (handles split/MDFC cards)
                                names_in_file = []
                                for line in content.split('\n'):
                                    line = line.strip()
                                    if line.startswith("Name:"):
                                        names_in_file.append(line.split(":", 1)[1].strip())

                                if names_in_file:
                                    loc = f"zip:{real_path}::{file_info.filename}"
                                    for name in names_in_file:
                                        if name not in index:
                                            index[name] = loc
                        except Exception:
                            pass
        except Exception as e:
            print(f"[Warn] Failed to read zip file {real_path}: {e}")
    else:
        # Fallback for extracted directories
        for root, _, files in os.walk(real_path):
            for file in files:
                if file.endswith(".txt"):
                    path = os.path.join(root, file)
                    try:
                        with open(path, "r", encoding="utf-8", errors="ignore") as f:
                            content = f.read()
                            names_in_file = []
                            for line in content.split('\n'):
                                line = line.strip()
                                if line.startswith("Name:"):
                                    names_in_file.append(line.split(":", 1)[1].strip())

                            if names_in_file:
                                loc = f"dir:{real_path}::{path}"
                                for name in names_in_file:
                                    if name not in index:
                                        index[name] = loc
                    except Exception:
                        pass

    _CARD_INDEX_CACHE[real_path] = index
    return index

def get_card_text(cardsfolder: str, name: str) -> str:
    index = load_card_names(cardsfolder)

    # Handle split/MDFC names that might be requested as "A // B" but indexed as "A" and "B"
    if name not in index and " // " in name:
        parts = name.split(" // ")
        if parts[0] in index:
            name = parts[0]
        elif parts[1] in index:
            name = parts[1]
        else:
            return ""

    if name not in index:
        return ""

    loc = index[name]
    if loc.startswith("zip:"):
        _, rest = loc.split("zip:", 1)
        zip_path, internal_path = rest.split("::", 1)
        try:
            with zipfile.ZipFile(zip_path, 'r') as zf:
                with zf.open(internal_path) as f:
                    return f.read().decode('utf-8', errors='ignore')
        except Exception:
            return ""
    elif loc.startswith("dir:"):
        _, rest = loc.split("dir:", 1)
        _, file_path = rest.split("::", 1)
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        except Exception:
            return ""
    return ""

def forge_has_card(cardsfolder: str, name: str) -> bool:
    index = load_card_names(cardsfolder)
    if name in index:
        return True

    # Heuristic for split cards and MDFCs (e.g., "Wear // Tear", "Mirror Room // Fractured Realm")
    if " // " in name:
        parts = name.split(" // ")
        if len(parts) == 2:
            path1 = index.get(parts[0])
            path2 = index.get(parts[1])

            # Case 1: Both parts are explicitly named in the same file
            if path1 and path2 and path1 == path2:
                return True

            # Case 2: Only one part is named, but the file is a split/fuse/alternate card
            check_name = parts[0] if path1 else parts[1]
            if check_name:
                text = get_card_text(cardsfolder, check_name)
                if text and ("AlternateMode:Split" in text or "K:Fuse" in text or "ALTERNATE" in text or "Modal" in text):
                    return True

    return False

def parse_dck(text: str) -> dict:
    result = {"metadata": {}, "main": [], "commander": [], "meta_order": []}
    current_section = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        lower_line = line.lower()
        if lower_line.startswith("[metadata]"):
            current_section = "metadata"
        elif lower_line.startswith("[main]"):
            current_section = "main"
        elif lower_line.startswith("[commander]"):
            current_section = "commander"
        elif lower_line.startswith("["):
            current_section = "ignore"
        elif current_section == "metadata":
            if "=" in line:
                k, v = line.split("=", 1)
                result["metadata"][k.strip()] = v.strip()
                result["meta_order"].append(line)
        elif current_section in ("main", "commander"):
            result[current_section].append(line)
    return result

def card_qty(token: str) -> int:
    match = re.match(r"^(\d+)", token.strip())
    return int(match.group(1)) if match else 1

def card_name(token: str) -> str:
    token = token.strip()
    token = re.sub(r"^\d+\s*", "", token)
    return token.split("|")[0].strip()

def total_qty(tokens: list) -> int:
    return sum(card_qty(t) for t in tokens)

def apply_swaps(base: dict, swaps: list[dict]) -> dict:
    """Applies a list of {add, cut} swaps sequentially."""
    current = {
        "metadata": dict(base["metadata"]),
        "main": list(base["main"]),
        "commander": list(base["commander"]),
        "meta_order": list(base["meta_order"])
    }

    for swap in swaps:
        add = swap["add"]
        cut = swap["cut"]

        is_cmdr_swap = any(card_name(t) == cut for t in current["commander"])

        if is_cmdr_swap:
            new_cmdr = [f"1 {add}" if card_name(t) == cut else t for t in current["commander"]]
            island_idx = next((i for i, t in enumerate(current["main"]) if card_name(t) == "Island"), -1)
            if island_idx == -1:
                raise ValueError("Cannot swap commander: no Island to cut for card count.")
            island_qty = card_qty(current["main"][island_idx])
            if island_qty <= 1:
                current["main"].pop(island_idx)
            else:
                current["main"][island_idx] = f"{island_qty - 1} Island"
            current["main"].append(f"1 {cut}")
            current["commander"] = new_cmdr
        else:
            cut_found = 0
            temp_main = []
            for token in current["main"]:
                if card_name(token) == cut:
                    if card_qty(token) != 1:
                        raise ValueError(f"Cannot cut bulk token: {token}")
                    cut_found += 1
                    continue
                temp_main.append(token)
            if cut_found == 0:
                raise ValueError(f"Cut card '{cut}' not found in deck.")
            if cut_found > 1:
                raise ValueError(f"Cut card '{cut}' found multiple times.")
            temp_main.append(f"1 {add}")
            current["main"] = temp_main

    return current

def make_variant(base_path: str, cardsfolder: str, swaps: list[dict], out_dir: str) -> str:
    """Materializes a variant with multiple swaps and auto-fixes mana."""
    if not swaps:
        # Baseline deck, just copy it
        out_path = os.path.join(out_dir, "baseline.dck")
        with open(base_path, "r") as f_in, open(out_path, "w") as f_out:
            f_out.write(f_in.read())
        return out_path

    for s in swaps:
        if not forge_has_card(cardsfolder, s["add"]):
            raise ValueError(f"Add card '{s['add']}' not found in Forge DB.")

    with open(base_path, "r", encoding="utf-8") as f:
        base = parse_dck(f.read())

    new_deck = apply_swaps(base, swaps)

    # Check missing colors on the FINAL deck state
    all_missing = set()
    for s in swaps:
        all_missing.update(missing_colors(cardsfolder, new_deck, s["add"]))

    for color in sorted(all_missing):
        if color not in FIX_LANDS:
            raise ValueError(f"No curated fix lands for color {color}.")

        valid_fixes = [land for land in FIX_LANDS[color] if forge_has_card(cardsfolder, land) and color in produced_colors(cardsfolder, land)]
        if len(valid_fixes) < SPLASH_SOURCES:
            raise ValueError(f"Cannot fix color {color}: insufficient valid dual lands.")

        island_idx = next((i for i, t in enumerate(new_deck["main"]) if card_name(t) == "Island"), -1)
        if island_idx == -1 or card_qty(new_deck["main"][island_idx]) < SPLASH_SOURCES:
            raise ValueError(f"Cannot fix color {color}: not enough Island copies.")

        island_qty = card_qty(new_deck["main"][island_idx])
        new_deck["main"][island_idx] = f"{island_qty - SPLASH_SOURCES} Island"
        for land in valid_fixes:
            new_deck["main"].append(f"1 {land}")
        print(f"fix: color {color}, -{SPLASH_SOURCES} Island, +{valid_fixes}")

    if total_qty(new_deck["main"]) != total_qty(base["main"]):
        raise ValueError("Variant total card count does not match base deck.")

    os.makedirs(out_dir, exist_ok=True)

    # Generate a clean ID from the swaps
    parts = []
    for s in swaps:
        parts.append(f"add_{s['add'].replace(' ', '_').replace(',', '')}")
        parts.append(f"cut_{s['cut'].replace(' ', '_').replace(',', '')}")
    variant_name = "keen__" + "__".join(parts)

    out_path = os.path.join(out_dir, variant_name + ".dck")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("[metadata]\n")
        f.write(f"Name={variant_name}\n")
        f.write("[Main]\n")
        for t in new_deck["main"]: f.write(f"{t}\n")
        f.write("[Commander]\n")
        for t in new_deck["commander"]: f.write(f"{t}\n")

    return out_path

def produced_colors(cardsfolder: str, name: str) -> set:
    content = get_card_text(cardsfolder, name)
    if not content:
        return set()

    colors = set()
    types_match = re.search(r"^Types:(.*)$", content, re.MULTILINE | re.IGNORECASE)
    if types_match:
        types_line = types_match.group(1).lower()
        if "plains" in types_line: colors.add("W")
        if "island" in types_line: colors.add("U")
        if "swamp" in types_line: colors.add("B")
        if "mountain" in types_line: colors.add("R")
        if "forest" in types_line: colors.add("G")
        if "wastes" in types_line: colors.add("C")

    for match in re.finditer(r"Produced\$\s*([^|]+)", content, re.IGNORECASE):
        val = match.group(1).strip()
        tokens = val.replace("Combo", "").replace("Special", "").replace("Chosen", "").split()
        for t in tokens:
            t = t.strip()
            if t == "Any":
                colors.update({"W", "U", "B", "R", "G"})
            elif t.isdigit():
                colors.add("C")
            elif t in "WUBRGC":
                colors.add(t)
    return colors

def deck_land_colors(cardsfolder: str, deck: dict) -> set:
    colors = set()
    all_tokens = deck["main"] + deck["commander"]
    for token in all_tokens:
        name = card_name(token)
        content = get_card_text(cardsfolder, name)
        if content and re.search(r"^Types:.*\bLand\b", content, re.MULTILINE | re.IGNORECASE):
            colors.update(produced_colors(cardsfolder, name))
    return colors

def missing_colors(cardsfolder: str, deck: dict, add: str) -> set:
    content = get_card_text(cardsfolder, add)
    if not content:
        raise ValueError(f"Card '{add}' not found or unreadable.")

    deck_colors = deck_land_colors(cardsfolder, deck)
    required = set()

    mana_cost_match = re.search(r"^ManaCost:\s*(.*)$", content, re.MULTILINE | re.IGNORECASE)
    if mana_cost_match:
        cost = mana_cost_match.group(1).strip()
        if cost.lower() == "no cost":
            colors_match = re.search(r"^Colors:\s*(.*)$", content, re.MULTILINE | re.IGNORECASE)
            if colors_match:
                color_names = colors_match.group(1).lower().split(",")
                mapping = {"white":"W", "blue":"U", "black":"B", "red":"R", "green":"G", "colorless":"C"}
                for cn in color_names:
                    c = mapping.get(cn.strip())
                    if c and c not in deck_colors:
                        required.add(c)
        else:
            for token in re.findall(r"[0-9XWUBRGC]+P?", cost):
                if "P" in token: continue
                if token.isdigit() or token == "X" or token == "C": continue
                for char in token:
                    if char in "WUBRG" and char not in deck_colors:
                        required.add(char)
    return required

SPLASH_SOURCES = 6
FIX_LANDS = {
    # Dimir (UB)
    "B": ["Watery Grave", "Sunken Hollow", "Drowned Catacomb",
          "Underground River", "Morphic Pool", "Shipwreck Marsh"],
    # Izzet (UR)
    "R": ["Steam Vents", "Sulfur Falls", "Shivan Reef",
          "Izzet Boilerworks", "Stormcarved Coast", "Fiery Islet"],
    # Azorius (UW)
    "W": ["Hallowed Fountain", "Glacial Fortress", "Azorius Chancery",
          "Tundra", "Port Town", "Adarkar Wastes"],
    # Simic (UG)
    "G": ["Breeding Pool", "Tropical Island", "Simic Growth Chamber",
          "Yavimaya Coast", "Botanical Sanctum", "Waterlogged Grove"],
}

def variant_id(add: str, cut: str) -> str:
    return f"keen__add_{add.replace(' ', '_').replace(',', '')}__cut_{cut.replace(' ', '_').replace(',', '')}"

def write_variant(base: dict, out_path: str, name: str):
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("[metadata]\n")
        f.write(f"Name={name}\n")
        f.write("[Main]\n")
        for t in base["main"]:
            f.write(f"{t}\n")
        f.write("[Commander]\n")
        for t in base["commander"]:
            f.write(f"{t}\n")

