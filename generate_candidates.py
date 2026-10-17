import os
import sys
import io
import re
import random
import contextlib
import tempfile
import urllib.request
import json
import deck_variant as dv
import opponents as op

# Per-color-identity offline fallback (used when EDHREC is unreachable).
# Mono-U pieces are legal in every identity; pair entries add the identity's
# own staples. Names are verified against the real cardsfolder by the
# generation-time materialization smoke, so a bad guess can never reach a
# spec that runs.
OFFLINE_COMBOS = {
    "u": {
        # Core infinite combos & 2-piece synergies
        "Thopter Foundry", "Sword of the Meek", "Retrofitter Foundry", "Thopter Assembly",
        "Marionette Master", "Brudiclad, Telchor Engineer", "Pia and Kiran Nalaar",
        "Emry, Lurker of the Loch", "Master Transmuter", "Metallurgic Summonings",
        "Karn, the Great Creator", "Mycosynth Lattice",
        # High-synergy engines & draw
        "Grand Architect", "Padeem, Consul of Innovation", "Thought Monitor",
        "Shielded Aether Thief", "Efficient Construction", "Chief Engineer", "Foundry Inspector",
        "The Antiquities War", "Mask of Memory",
        "Ornithopter", "Skyship Stalker", "Prophet of Distortion", "Daretti, Ingenious Iconoclast",
        "Tezzeret, Cruel Machinist", "Aetherflux Reservoir", "Urza's Saga", "Tinker",
        "Transmute Artifact", "Rebuild", "Thoughtcast", "Lich's Mirror", "Dimir Keyrune",
        # Mono-U mana rocks & lands
        "Tolarian Academy", "Caged Sun", "Gauntlet of Power", "Coalition Relic", "Vedalken Orrery",
        "Tamiyo, the Moon Sage", "Jace, the Mind Sculptor", "Teferi, Hero of Dominaria",
        "Narset, Parter of Veils",
    },
    "ub": {
        "Thopter Foundry", "Sword of the Meek", "Marionette Master", "Emry, Lurker of the Loch",
        "Dimir Keyrune", # Dimir tutors & staples
        "Demonic Tutor", "Vampiric Tutor", "Toxic Deluge", "Necropotence", "Yawgmoth's Will",
        "Bolas's Citadel", "Imperial Seal", "Timetwister", "Time Spiral",
    },
    "ur": {
        "Thopter Foundry", "Sword of the Meek", "Retrofitter Foundry", "Pia and Kiran Nalaar",
        "Daretti, Ingenious Iconoclast", # Izzet artifact-storm & copy synergies
        "Krark, the Thumbless", "Faithless Looting", "Empty the Warrens", "Galvanic Iteration",
    },
    "uw": {
        "Thopter Foundry", "Sword of the Meek", "Master Transmuter", # Azorius artifact-lifegain/tax engines
        "Sram, Senior Edificer", "Esper Sentinel", "Smothering Tithe",
        "Tameshi, Reality Architect", "Heliod's Intervention",
    },
    "ug": {
        "Thopter Foundry", "Sword of the Meek", "Thopter Assembly", "Sludge Monster",
        # Simic artifact-toughness & architect synergies
        "Urtet, Remnant of Memnarch", "Doubling Chant", "Green Sun's Twilight",
    },
}

# Backwards-compatible union (old code/tests treat this as a flat set).
KNOWN_ARCHETYPE_COMBOS = set().union(*OFFLINE_COMBOS.values())

COLOR_NAME_TO_LETTER = {"white": "W", "blue": "U", "black": "B", "red": "R", "green": "G", "colorless": "C"}
IDENTITY_KEYS = ("u", "ub", "ur", "uw", "ug")
COMBO_SLUGS = {"u": "mono-blue", "ub": "dimir", "ur": "izzet", "uw": "azorius", "ug": "simic"}


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
    Fetches combo cards from the official EDHREC Cloudflare JSON API for one
    color identity (mono-blue/dimir/izzet/azorius/simic). If blocked or empty,
    falls back to that identity's verified offline list.
    """
    key = colors.lower()
    slug = COMBO_SLUGS.get(key, key)
    url = f"https://json-cloudflare.edhrec.com/pages/combos/{slug}.json"

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
    }

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
            combo_cards = set()
            _extract_names_recursive(data, combo_cards)
            if combo_cards:
                print(f"[Info] Fetched {len(combo_cards)} raw names for '{slug}' from EDHREC.")
                return combo_cards
    except Exception as e:
        fallback = OFFLINE_COMBOS.get(key, set())
        if not fallback:
            print(f"[Warn] Could not fetch EDHREC combos for '{slug}' ({e}).")
            return set()
        print(f"[Warn] Could not fetch EDHREC combos for '{slug}' ({e}). Using per-identity offline list.")

    fallback = OFFLINE_COMBOS.get(key, set())
    print(f"[Info] Loaded {len(fallback)} verified offline combos for '{key}'.")
    return set(fallback)


def fetch_all_combos() -> dict:
    """Returns {identity_key: combo set} for every supported identity."""
    return {k: fetch_edhrec_combos(k) for k in IDENTITY_KEYS}


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


def card_color_identity(cardsfolder: str, name: str) -> set:
    """Color identity letters of a card: Colors: line + mana cost letters."""
    content = dv.get_card_text(cardsfolder, name)
    if not content:
        return set()

    ident = set()
    colors = re.search(r"^Colors:\s*(.*)$", content, re.MULTILINE | re.IGNORECASE)
    if colors:
        for cn in colors.group(1).lower().split(","):
            cn = cn.strip()
            if cn in COLOR_NAME_TO_LETTER:
                ident.add(COLOR_NAME_TO_LETTER[cn])

    mc = re.search(r"^ManaCost:\s*(.*)$", content, re.MULTILINE | re.IGNORECASE)
    if mc:
        cost = mc.group(1).strip()
        if cost.lower() != "no cost":
            for ch in cost.upper():
                if ch in "WUBRG":
                    ident.add(ch)
    return ident


def identity_key(ident: set):
    """Maps a color set to a supported identity key, or None.

    Supported: mono-U, or U + exactly one of B/R/G/W  ("u", "ub", "ur", ...).
    Non-color letters (colorless C, snow S) do not expand identity.
    """
    if not ident or "U" not in ident:
        return None
    extra = ident & {"W", "B", "R", "G"}
    if len(extra) <= 1:
        return "u" if not extra else "u" + extra.pop().lower()
    return None


def _rules_sentences(content_lower: str) -> list:
    """Human rules text (Oracle:/Description$ fields), sentence-split."""
    sentences = []
    for line in content_lower.split("\n"):
        line = line.strip()
        if line.startswith("oracle:") or "description$" in line:
            text = line.split("description$", 1)[-1]
            if line.startswith("oracle:"):
                text = line.split(":", 1)[1]
            text = text.replace("[", "").replace("]", "")
            sentences.extend(re.split(r"(?<=[.!?])\s+", text))
    return [s for s in sentences if s]


PAYOFF_NEGATIVE_RE = re.compile(r"\b(destroy|exile|sacrific|deathtouch)\b|\-1/\-1", re.IGNORECASE)
PUMP_RE = re.compile(r"\+[\dxX]/\+[\dxX]", re.IGNORECASE)
REMOVAL_ALL_RE = re.compile(r"\b(all permanents|destroy all|all creatures|exile all)\b", re.IGNORECASE)


def _payoff_info(content_lower: str) -> tuple:
    """(payoff_signal_count, is_anthem) from affirmative rules text.

    A payoff card scales with board state (anthems, damage-on-presence
    triggers) rather than being an engine/combo piece. Detection is generic
    - no card names are hard-coded.
    """
    count = 0
    anthem = False
    for sentence in _rules_sentences(content_lower):
        if PAYOFF_NEGATIVE_RE.search(sentence):
            continue  # wrath/removal phrasing around the same rule kills the hit
        pump = PUMP_RE.search(sentence)
        if pump and not re.search(r"\btarget\b", sentence):
            count += 1  # broad pump (targeted combat tricks don't scale)
            if "creature type" in sentence:
                anthem = True
        elif ("whenever" in sentence
              and ("enters" in sentence or "attacks" in sentence)
              and "damage" in sentence):
            count += 1
    return count, anthem


def _engine_keyword_hits(content_lower: str) -> list:
    """Engine keywords, with wholesale-removal phrasing discounting the
    sacrifice/token/draw hits (an 'All Is Dust' shape is not an engine)."""
    hits = [kw for kw in ["draw", "thopter", "artifact", "sacrifice", "token"] if kw in content_lower]
    if REMOVAL_ALL_RE.search(content_lower):
        hits = [k for k in hits if k not in ("sacrifice", "token", "draw")]
    return hits


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

    if is_add:
        # No CMC cap: relevance is enforced by monotonic scoring, not by a
        # hard filter, so a high-cost card can still surface if it ranks.
        if not any(t in types for t in ["artifact", "instant", "sorcery", "enchantment"]):
            return -100

        in_combo = name in combo_cards
        engine = bool(_engine_keyword_hits(content_lower))
        payoff_count, anthem = _payoff_info(content_lower)

        if not (in_combo or engine or payoff_count):
            # Nothing the archetype cares about: the deeper the cost, the
            # further below the pool threshold it lands.
            return -50 - cmc

        score = 0
        if in_combo: score += 20
        if engine: score += 10
        score += 8 * payoff_count
        if anthem: score += 15
        if cmc <= 2: score += 5
        # Cost penalty - payoffs tolerate 2 CMC more than engines.
        score -= max(0, cmc - 4) if payoff_count else max(0, cmc - 2)
        return score
    else:
        # Cut-side contract used by generate_subbranches: a card is only a
        # valid cut candidate when it is expensive (CMC >= 5); engine/combo
        # cards are additionally penalized. Do not "improve" without
        # updating that consumer.
        if cmc < 5: return -100
        if name in combo_cards: score = -20
        else: score = 0
        if any(kw in content_lower for kw in ["draw", "thopter", "artifact"]): score -= 10
        return score


def _affinity_keywords(content_lower: str) -> int:
    """How aligned with the deck's artifact engine a card's text is."""
    hits = 0
    for kw in ["draw", "thopter", "artifact", "sacrifice", "token", "construct", "metalcraft", "improvise"]:
        if kw in content_lower:
            hits += 1
    payoff_count, _ = _payoff_info(content_lower)
    if payoff_count:
        hits += 2  # payoff cards scale with the engine's board presence
    return hits


def get_commander_candidates(cardsfolder: str, base_deck: dict, combos_by_identity: dict) -> list:
    """Legendary creatures, CMC <= 4, identity mono-U or U+one other color.

    Returns [(name, id_key, score)] sorted by score desc.
    """
    candidates = []
    index = dv.load_card_names(cardsfolder)
    base_cmdr = dv.card_name(base_deck["commander"][0])

    for name in list(index.keys()):
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

        key = identity_key(card_color_identity(cardsfolder, name))
        if key is None:
            continue

        score = 0
        if name in combos_by_identity.get(key, set()) or name in combos_by_identity.get("u", set()):
            score += 30
        if any(kw in content_lower for kw in ["artifact", "thopter", "draw", "token", "construct"]):
            score += 15
        if cmc <= 2:
            score += 5

        if score > 0:
            candidates.append((name, key, score))

    return sorted(candidates, key=lambda x: x[1], reverse=True)


def pick_commanders_diverse(candidates: list, limit: int) -> list:
    """Round-robins pair identities (ub/ur/uw/ug) before letting mono-U flood."""
    groups = {}
    for c in candidates:
        groups.setdefault(c[1], []).append(c)

    picked = []
    keys_round = [k for k in ("ub", "ur", "uw", "ug", "u") if k in groups]
    while len(picked) < limit:
        progressed = False
        for k in keys_round:
            if len(picked) >= limit:
                break
            if groups.get(k):
                picked.append(groups[k][0])
                groups[k] = groups[k][1:]
                progressed = True
        if not progressed:
            break
    return picked


def package_adds_for(cardsfolder: str, cmdr_key: str, combos_by_identity: dict,
                     base_card_names: set, k: int) -> list:
    """Top-k on-identity synergy adds for a commander with identity key.

    A card qualifies if its identity ⊆ {U} ∪ commander's extra color.
    """
    combo_cards = set(combos_by_identity.get(cmdr_key, set())) | set(combos_by_identity.get("u", set()))
    allowed = {"U"}
    if len(cmdr_key) == 2:
        allowed.add(cmdr_key[1].upper())

    index = dv.load_card_names(cardsfolder)
    scored = []
    for name in index:
        if name in base_card_names:
            continue
        if not card_color_identity(cardsfolder, name) <= allowed:
            continue
        score = score_card(cardsfolder, name, is_add=True, combo_cards=combo_cards)
        if score <= -100:
            continue
        # Identity-specific combo pieces outrank generic mono-U pieces so
        # e.g. a UB commander surfaces Dimir tools instead of the same
        # mono-U artifacts every commander would otherwise get.
        in_id_combo = name in combos_by_identity.get(cmdr_key, set())
        in_u_combo = name in combos_by_identity.get("u", set())
        if in_id_combo and not in_u_combo:
            score += 12
        elif in_u_combo and not in_id_combo:
            score -= 4
        scored.append((name, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:k]


def weakest_cuts(cardsfolder: str, base_deck: dict, combos_by_identity: dict, count: int) -> list:
    """Cards least aligned with the artifact engine, to cut (excludes lands
    and the commander). Sorted weakest-first; may include 'strong-ish' cards
    if the pool is thin, but always the least relevant to the archetype."""
    super_combos = set()
    for s in combos_by_identity.values():
        super_combos |= set(s)

    base_cmdr = dv.card_name(base_deck["commander"][0])
    scored = []
    for token in base_deck["main"]:
        name = dv.card_name(token)
        if dv.card_qty(token) != 1 or name == base_cmdr:
            continue
        content = dv.get_card_text(cardsfolder, name)
        if not content:
            continue
        content_lower = content.lower()
        types_match = re.search(r"^\s*types:\s*(.*)$", content_lower, re.MULTILINE)
        types = types_match.group(1) if types_match else ""
        if re.search(r"\bland\b", types):
            continue  # Never auto-cut a mana source.

        weakness = _affinity_keywords(content_lower)
        if name in super_combos:
            weakness += 5
        cost_match = re.search(r"^\s*manacost:\s*(.*)$", content_lower, re.MULTILINE)
        cmc = calculate_cmc(cost_match.group(1).strip()) if cost_match else 0
        weakness += (1 if cmc <= 2 else 0)
        scored.append((weakness, name))

    scored.sort(key=lambda x: (x[0], x[1]))
    return [name for _, name in scored]


def build_changes_for_variant(cmdr_name: str, base_cmdr: str, package_adds: list,
                              package_cuts: list) -> list:
    """One variant's changes: commander swap first, then each add/cut."""
    changes = [{"add": cmdr_name, "cut": base_cmdr}]
    cuts_used = set()
    for add_name in package_adds:
        cut = next((c for c in package_cuts if c not in cuts_used), None)
        if cut is None:
            break
        cuts_used.add(cut)
        changes.append({"add": add_name, "cut": cut})
    if len(changes) < 2:
        return None  # Could not find cuts - skip this commander.
    return changes


def dedupe_change_sets(variants: list) -> tuple:
    """Keeps the first variant for each distinct change-set signature.

    Returns (kept_variants, dropped{id: reason}). Two variants with the
    same set of (add, cut) pairs are literally the same deck; the writer
    would also map them onto one filename.
    """
    keep = []
    dropped = {}
    seen_sets = {}
    for v in variants:
        sig = tuple(sorted((c["add"], c["cut"]) for c in v["changes"]))
        if sig in seen_sets:
            dropped[v["id"]] = f"duplicate of {seen_sets[sig]}"
            print(f"[Skip] {v['id']}: duplicate deck of {seen_sets[sig]}")
            continue
        seen_sets[sig] = v["id"]
        keep.append(v)
    return keep, dropped


def generate_candidates(base_dck_path: str, cardsfolder: str, max_variants: int = 50,
                        seed: int = 42, output_path: str = None,
                        commanders: int = 8, package_cards: int = 2,
                        payoff_themes: int = 3):
    is_valid_path = os.path.isdir(cardsfolder) or (os.path.isfile(cardsfolder) and cardsfolder.endswith('.zip'))
    if not is_valid_path:
        raise ValueError(f"cardsfolder does not exist or is not a valid directory/zip: '{cardsfolder}'.\n"
                         f"Hint: For your setup, it is likely '/Applications/forge-installer-2.0.15/res/cardsfolder/cardsfolder'")

    rng = random.Random(seed)

    with open(base_dck_path, "r", encoding="utf-8") as f:
        base_deck = dv.parse_dck(f.read())

    base_card_names = {dv.card_name(t) for t in base_deck["main"]}
    base_cmdr_name = dv.card_name(base_deck["commander"][0])

    combos_by_identity = fetch_all_combos()

    # Weakest base cards, reused across variants (each variant is an
    # independent deck copy - cuts may repeat between variants).
    weak_pool = weakest_cuts(cardsfolder, base_deck, combos_by_identity, count=None)
    print(f"[Info] {len(weak_pool) if weak_pool else 0} valid weak cut pools." if weak_pool else "[Warn] No weak cut pool.")

    # ---- Layer 1: commander-anchored deep variants ----
    cmdr_candidates = get_commander_candidates(cardsfolder, base_deck, combos_by_identity)
    print(f"[Info] Found {len(cmdr_candidates)} valid alternative commanders across identities.")
    picked = pick_commanders_diverse(cmdr_candidates, commanders)
    print(f"[Info] Selected {len(picked)} commanders for deep variants: "
          + ", ".join(f"{n}[{k}]" for n, k, _ in picked))

    deep_variants = []
    used_thm_adds = set()
    for cmdr_name, cmdr_key, _ in picked:
        adds = package_adds_for(cardsfolder, cmdr_key, combos_by_identity, base_card_names, package_cards)
        if not adds:
            print(f"[Skip] No package adds for {cmdr_name} ({cmdr_key}).")
            continue
        pkg_cts = list(weak_pool)
        changes = build_changes_for_variant(cmdr_name, base_cmdr_name,
                                            [a for a, _ in adds], pkg_cts)
        if changes is None:
            print(f"[Skip] Could not build cuts for {cmdr_name}.")
            continue
        deep_variants.append({
            "id": "deep_add_" + cmdr_name.replace(' ', '_').replace(",", "").replace("'", ""),
            "changes": changes,
            "_cmdr": cmdr_name,
        })
        usedThmAdd = adds[0][0]
        if usedThmAdd not in used_thm_adds:
            used_thm_adds.add(usedThmAdd)

    # ---- Layer 2: generation-time sub-branches (theme singles) ----
    theme_variants = []
    used_theme_adds = set()
    for v in deep_variants[:3]:
        cmdr_name = v["_cmdr"]
        cmdr_key = next(k for n, k, _ in picked if n == cmdr_name)
        adds = package_adds_for(cardsfolder, cmdr_key, combos_by_identity, base_card_names, package_cards + 2)
        add_pick = next((a for a, _ in adds if a not in {c["add"] for c in v["changes"]}
                         and a not in used_theme_adds), None)
        if add_pick is None:
            continue
        used_theme_adds.add(add_pick)
        cut_pick = next((c for c in weak_pool if c not in {ch["cut"] for ch in v["changes"]}), None)
        if cut_pick is None:
            continue
        cmdr_tag = cmdr_name.replace(' ', '_').replace(",", "").replace("'", "")
        theme_variants.append({
            "id": "theme_" + cmdr_tag + "__add_" + add_pick.replace(' ', '_').replace(",", "").replace("'", ""),
            "changes": [{"add": add_pick, "cut": cut_pick}],
        })

    # ---- Layer 3: plain single swaps (mono-U cards while Sai is in command) ----
    union_combos = set().union(*combos_by_identity.values())
    u_combos = set(combos_by_identity.get("u", set())) | union_combos
    add_candidates = []
    index = dv.load_card_names(cardsfolder)
    for name in index:
        if name not in base_card_names and name != base_cmdr_name:
            score = score_card(cardsfolder, name, is_add=True, combo_cards=u_combos)
            if score > -100:
                add_candidates.append((name, score))
    print(f"[Info] {len(add_candidates)} candidates for single swaps.")

    # ---- Layer: payoff showcases (Sai stays commander) ----
    # Variants that add the top-ranked payoff card(s) directly to the base
    # deck so the payoff class is measurable in isolation. v1 = top payoff
    # alone; v2/v3 = leader + next payoffs, so the change-sets stay distinct.
    remaining_budget = max(0, max_variants - len(deep_variants) - len(theme_variants))
    payoff_variants = []
    payoff_themes_eff = min(payoff_themes, remaining_budget)
    if payoff_themes_eff > 0:
        payoffs_ranked = [
            (n, s) for n, s in add_candidates
            if _payoff_info(dv.get_card_text(cardsfolder, n).lower())[0] > 0
        ]
        payoffs_ranked.sort(key=lambda x: x[1], reverse=True)
        top = [n for n, _ in payoffs_ranked[: payoff_themes_eff]]
        for i in range(len(top)):
            changes = []
            cuts_used = set()
            for add_name in top[: i + 1]:
                cut = next((c for c in weak_pool if c not in cuts_used), None)
                if cut is None:
                    break
                cuts_used.add(cut)
                changes.append({"add": add_name, "cut": cut})
            if changes:
                payoff_variants.append({
                    "id": "payoff_" + "__".join(
                        "add_" + a.replace(' ', '_').replace(",", "").replace("'", "") for a, _ in changes
                    ),
                    "changes": changes,
                })
        print(f"[Info] payoff layer: {len(payoff_variants)} showcase variants "
              f"(top payoffs: {', '.join(top)})")

    singles_budget = max(0, remaining_budget - len(payoff_variants))
    singles = []
    used_adds = set()
    attempts = 0
    wide_pool = [n for n, s in add_candidates if s >= 0]
    while len(singles) < singles_budget and attempts < singles_budget * 60:
        attempts += 1
        add_name = rng.choice(wide_pool) if wide_pool else None
        if add_name is None or add_name in used_adds:
            continue
        used_adds.add(add_name)
        cut_name = rng.choice(weak_pool[:max(1, min(len(weak_pool), 8))])
        singles.append({
            "id": "add_" + add_name.replace(' ', '_').replace(",", "").replace("'", ""),
            "changes": [{"add": add_name, "cut": cut_name}],
        })

    all_variants = deep_variants + theme_variants + payoff_variants + singles
    for v in all_variants:
        v.pop("_cmdr", None)

    # Two variants must never materialize to the same deck: identical
    # change-sets (e.g. two theme singles converging on the same package
    # add) share one filename and would silently test one variant twice.
    all_variants, dropped = dedupe_change_sets(all_variants)
    # ---- Materialization smoke: drop variants the harness cannot build ----
    survivors = []
    with tempfile.TemporaryDirectory() as smoke_dir:
        for v in all_variants:
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    dv.make_variant(base_dck_path, cardsfolder, v["changes"], smoke_dir)
                survivors.append(v)
            except ValueError as e:
                dropped[v["id"]] = str(e)
                print(f"[Skip] {v['id']}: {e}")

    # ---- Write spec ----
    output_path = output_path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "keen_variants.yaml")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(f"base: {os.path.abspath(base_dck_path)}\n")
        f.write("swaps:\n")
        for v in survivors:
            f.write(f"  - id: {v['id']}\n")
            f.write("    changes:\n")
            for ch in v["changes"]:
                f.write(f"      - add: {ch['add']}\n")
                f.write(f"        cut: {ch['cut']}\n")

    deep_ct = sum(1 for v in survivors if v["id"].startswith("deep_"))
    theme_ct = sum(1 for v in survivors if v["id"].startswith("theme_"))
    payoff_ct = sum(1 for v in survivors if v["id"].startswith("payoff_"))
    single_ct = sum(1 for v in survivors if v["id"].startswith("add_"))
    print(f"[Success] Generated {len(survivors)} variants to {output_path} "
          f"(deep: {deep_ct}, theme: {theme_ct}, payoff: {payoff_ct}, singles: {single_ct}); "
          f"dropped {len(dropped)} (dedupe/smoke).")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate data-driven deck variants")
    parser.add_argument("--base", default=None,
                        help="Base deck (defaults to the canonical Keen Engineering deck)")
    parser.add_argument("--cardsfolder", default=None,
                        help="Forge cardsfolder (defaults to the canonical res/cardsfolder)")
    parser.add_argument("--out", default=None,
                        help="Output spec path (defaults to keen_variants.yaml next to this script)")
    parser.add_argument("--max-variants", type=int, default=50, help="Total budget of variants to emit")
    parser.add_argument("--commanders", type=int, default=8,
                        help="How many commander-anchored deep variants to build (identity-diverse)")
    parser.add_argument("--package-cards", type=int, default=2,
                        help="How many extra add/cut pairs each deep variant gets")
    parser.add_argument("--payoff-themes", type=int, default=3,
                        help="How many payoff showcase variants (Sai still commanding) to emit; 0 disables")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    try:
        import runtime_env
        base = args.base or runtime_env.BASE_DECK
        cardsfolder = args.cardsfolder or os.path.join(runtime_env.FORGE_ROOT, "forge-gui/res/cardsfolder")
    except Exception:
        base = args.base or "../../sim-results/foundations-commander/decks/Keen_Engineering.dck"
        cardsfolder = args.cardsfolder or "../../forge-gui/res/cardsfolder"

    generate_candidates(base, cardsfolder, args.max_variants, args.seed, args.out,
                        commanders=args.commanders, package_cards=args.package_cards,
                        payoff_themes=args.payoff_themes)
