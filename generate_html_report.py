"""Render a standalone HTML deck-lab report from campaign output.

Reads a data directory of Forge JSONL game logs (the layout produced by
``run_variants.py``), the variant spec that declared them, and the base deck,
then writes a single self-contained, print-friendly HTML dashboard:

  python3 generate_html_report.py --data-dir data/genA \
      --variants keen_variants.yaml --out docs/deck-report.html

Stdlib only. Wins/draws/timeouts are aggregated exactly like ``report.py``
(focal = seat 0; draws excluded from win share). Zero-byte JSONL files are
reported as deck-load errors, never as games.
"""
import argparse
import datetime
import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deck_variant as dv  # noqa: E402
import runtime_env as re_  # noqa: E402


# ---------------------------------------------------------------------------
# Spec parsing (uniform changes: list format from generate_candidates.py)
# ---------------------------------------------------------------------------


def load_spec(path: str) -> dict:
    """Return {"base": path, "variants": {id: [ {"add":.., "cut":..}, ... ]}}."""
    spec = {"base": "", "variants": {}}
    if not path or not os.path.exists(path):
        return spec
    cur_id = None
    cur_changes: list = []
    pending: dict = {}
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip()
            if not line or line.lstrip().startswith("#"):
                continue
            if line.startswith("base:"):
                spec["base"] = line.split(":", 1)[1].strip()
                continue
            m_id = re.match(r"^(\s*)- id:\s*(.+)$", line)
            if m_id:
                if cur_id is not None:
                    spec["variants"][cur_id] = cur_changes
                cur_id = m_id.group(2).strip()
                cur_changes = []
                pending = {}
                continue
            if cur_id is None:
                continue
            m_entry = re.match(r"^(\s*)- (add|cut):\s*(.+)$", line)
            if m_entry:
                pending = {m_entry.group(2): m_entry.group(3).split("#")[0].strip()}
                cur_changes.append(pending)
                continue
            m_key = re.match(r"^(\s+)(add|cut):\s*(.+)$", line)
            if m_key and pending and m_key.group(2) not in pending:
                pending[m_key.group(2)] = m_key.group(3).split("#")[0].strip()
            elif m_key and not pending:
                # compact single-swap form: add/cut directly under the swap
                pending = {m_key.group(2): m_key.group(3).split("#")[0].strip()}
                cur_changes.append(pending)
        if cur_id is not None:
            spec["variants"][cur_id] = cur_changes
    return spec


def variant_layer(vid: str) -> str:
    if vid == "baseline":
        return "baseline"
    for layer in ("deep", "theme", "payoff"):
        if vid.startswith(layer + "_"):
            return layer
    return "add"


LAYER_TITLE = {
    "baseline": "Baseline — stock precon",
    "deep": "Commander swaps",
    "theme": "Theme packages",
    "payoff": "Payoff showcases",
    "add": "Single swaps",
}


# ---------------------------------------------------------------------------
# Card database (offline, from Forge cardsfolder)
# ---------------------------------------------------------------------------


CATEGORY_ORDER = ["Land", "Creature", "Artifact", "Enchantment", "Instant",
                  "Sorcery", "Planeswalker", "Other"]


def card_category(rec: dict) -> str:
    types = rec["types"]
    if "Land" in types:
        return "Land"
    for cat in ("Creature", "Planeswalker", "Instant", "Sorcery", "Enchantment", "Artifact"):
        if cat in types:
            return cat
    return "Other"


def parse_mana_cost(mc: str) -> tuple:
    """'2 U' -> (3, {'U':1}); 'no cost' -> (None, {})."""
    if not mc or mc == "no cost":
        return None, {}
    color_counts: dict = {}
    cmc = 0
    for tok in mc.split():
        tok = tok.strip("{}")
        if re.fullmatch(r"\d+", tok):
            cmc += int(tok)
        elif tok == "X":
            pass
        else:
            if tok.endswith("P") and len(tok) == 2:  # phyrexian mana
                tok = tok[0]
            if len(tok) == 1:
                cmc += 1
                color_counts[tok] = color_counts.get(tok, 0) + 1
            else:
                # hybrid or 2-mask symbols: two generic pips, trailing color
                cmc += 2
                color_counts[tok[-1]] = color_counts.get(tok[-1], 0) + 1
    return cmc, color_counts


def load_card_db(cardsfolder: str, names: list) -> dict:
    """name -> {'types': set, 'cmc': int|None, 'colors': {sym: n},
    'oracle': str, 'mana_cost': str}. Missing cards resolve to blank records."""
    accr = {}
    wanted = sorted(set(n for n in names if n))
    index = dv.load_card_names(cardsfolder)
    for name in wanted:
        rec = {"types": set(), "cmc": None, "colors": {},
               "oracle": "", "mana_cost": "", "known": True}
        loc = index.get(name)
        txt = dv.get_card_text(cardsfolder, name)
        if not txt:
            # try MDFC/split front halves
            if " // " in name:
                for part in name.split(" // "):
                    txt = dv.get_card_text(cardsfolder, part)
                    if txt:
                        break
        if not txt:
            rec["known"] = bool(loc)
            accr[name] = rec
            continue
        keep_oracle = False
        for line in txt.splitlines():
            line = line.strip()
            if line.startswith("Types:"):
                rec["types"] = {t for t in line.split(":", 1)[1].strip().split() if t}
            elif line.startswith("ManaCost:"):
                mc = line.split(":", 1)[1].strip()
                rec["mana_cost"] = mc
                rec["cmc"], rec["colors"] = parse_mana_cost(mc)
            elif line.startswith("Oracle:"):
                keep_oracle = True
                rec["oracle"] += line.split(":", 1)[1].strip() + " "
            elif keep_oracle and not line.startswith(("SVar:", "Deck", "AI:", "R:",
                                                       "SetNumber", "Abilities", "Altern")):
                rec["oracle"] += line + " "
        accr[name] = rec
    return accr


# ---------------------------------------------------------------------------
# Deck facts
# ---------------------------------------------------------------------------


def deck_facts(base_deck: str, cardsfolder: str) -> dict:
    with open(base_deck, "r", encoding="utf-8") as f:
        parsed = dv.parse_dck(f.read())
    entries = []
    for sec in ("main", "commander"):
        for tok in parsed[sec]:
            entries.append({"name": dv.card_name(tok), "qty": dv.card_qty(tok), "sec": sec})
    db = load_card_db(cardsfolder, [e["name"] for e in entries])
    for e in entries:
        e.update(db[e["name"]])
        e["category"] = card_category(e)
    commander = [e for e in entries if e["sec"] == "commander"][0]
    main = [e for e in entries if e["sec"] == "main"]
    lands = [e for e in main if e["category"] == "Land"]
    nonlands = [e for e in main if e["category"] != "Land"]
    creatures = [e for e in main if e["category"] == "Creature"]
    artifacts = [e for e in main if e["category"] == "Artifact"]
    curve: dict = {}
    spell_qty = 0
    cmc_sum = 0.0
    for e in nonlands:
        cmc = e["cmc"] if e["cmc"] is not None else 0
        curve[min(cmc, 6)] = curve.get(min(cmc, 6), 0) + e["qty"]
        cmc_sum += cmc * e["qty"]
        spell_qty += e["qty"]
    pips: dict = {}
    for e in entries:
        for sym, c in e["colors"].items():
            pips[sym] = pips.get(sym, 0) + c * e["qty"]
    rocks = [e["name"] for e in main if "Artifact" in e["types"]
             and (e["cmc"] or 0) <= 2 and re.search(r"\{T\}:\s*Add|T:\s*Add", e["oracle"])]
    counters = [e["name"] for e in entries
                if "Instant" in e["types"] and "counter target spell" in e["oracle"].lower()]
    token_makers = [e["name"] for e in entries
                    if re.search(r"create[sd]?\s", e["oracle"].lower())
                    and "token" in e["oracle"].lower()]
    draw_engines = [e["name"] for e in entries
                    if re.search(r"\bdraw (a|one|up to)\b", e["oracle"].lower())]
    mass = [e["name"] for e in entries
            if "destroy all" in e["oracle"].lower() or "exile all" in e["oracle"].lower()]
    return {
        "name": parsed["metadata"].get("Name", os.path.basename(base_deck)),
        "commander": commander["name"],
        "commander_cmc": commander["cmc"],
        "entries": entries,
        "main": main,
        "lands": lands,
        "nonlands": nonlands,
        "creatures": creatures,
        "artifacts": artifacts,
        "curve": curve,
        "avg_spell_cmc": (cmc_sum / spell_qty) if spell_qty else 0.0,
        "pips": pips,
        "rocks": rocks,
        "counterspells": counters,
        "token_makers": token_makers,
        "draw_engines": draw_engines,
        "mass_sweepers": mass,
        "total_qty": sum(e["qty"] for e in entries),
    }


# ---------------------------------------------------------------------------
# Hypergeometric draw odds (commander in command zone -> pool of 99)
# ---------------------------------------------------------------------------


def hypergeom_pmf(pool: int, successes: int, draws: int, k: int) -> float:
    return math.comb(successes, k) * math.comb(pool - successes, draws - k) / math.comb(pool, draws)


def at_least(pool: int, successes: int, draws: int, k: int) -> float:
    """P(X >= k), X ~ Hypergeometric(pool, successes, draws)."""
    if successes == 0 or k > min(draws, successes):
        return 0.0 if successes < pool else 1.0
    p = 0.0
    for i in range(k, min(draws, successes) + 1):
        if draws - i > pool - successes:
            continue
        p += hypergeom_pmf(pool, successes, draws, i)
    return max(0.0, min(1.0, p))


# ---------------------------------------------------------------------------
# Game aggregation (mirrors report.py semantics)
# ---------------------------------------------------------------------------


def canonical_variant_id(fname: str) -> str:
    name = fname.replace(".jsonl", "")
    name = re.sub(r"_s\d+$", "", name)
    name = re.sub(r"_r\d+$", "", name)
    return name


def load_games(data_dir: str, spec_variants: dict) -> dict:
    """variant_id -> {'status': 'played'|'missing'|'error', 'rows': [...],
    'zero_byte': bool}. A 0-byte file is an error unit, never a game."""
    grouped: dict = {}
    for fname in sorted(os.listdir(data_dir)):
        if not fname.endswith(".jsonl"):
            continue
        cvid = canonical_variant_id(fname)
        entry = grouped.setdefault(cvid, {"status": "missing", "rows": [], "zero_byte": False})
        path = os.path.join(data_dir, fname)
        if os.path.getsize(path) == 0:
            entry["zero_byte"] = True
            continue
        entry["status"] = "played"
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    entry["rows"].append(json.loads(line))
                except Exception:
                    continue
    manifest_path = os.path.join(data_dir, "manifest.json")
    if os.path.exists(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            for vid in manifest.get("variants", []) + manifest.get("skipped", []):
                grouped.setdefault(vid, {"status": "missing", "rows": [], "zero_byte": False})
        except Exception:
            pass
    for vid in spec_variants:
        grouped.setdefault(vid, {"status": "missing", "rows": [], "zero_byte": False})
    return grouped


def wilson_interval(wins: int, n: int, z: float = 1.96) -> tuple:
    if n == 0:
        return (0.0, 0.0)
    p = wins / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    sd = math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)
    return (max(0.0, (centre - z * sd) / denom), min(1.0, (centre + z * sd) / denom))


def loss_depth(row: dict) -> str:
    """'win' | 'lost_first' | 'lost_mid' | 'lost_last' | 'draw'."""
    if row.get("timeout") or row.get("winnerSeat", -1) == -1:
        return "draw"
    if row.get("winnerSeat") == 0:
        return "win"
    elim = row.get("eliminationOrder", []) or []
    if 0 in elim:
        idx = elim.index(0)
        if idx == 0:
            return "lost_first"
        if idx == len(elim) - 1:
            return "lost_last"
        return "lost_mid"
    return "lost_last"


def clean_opp(opp: str) -> str:
    """'Kraum, Ludevic's Opus __ Toggo, Goblin Weaponsmith [June 2026]'
    -> "Kraum + Toggo [June 2026]"."""
    base = opp.split("[")[0].strip()
    parts = [p.strip() for p in base.split("__") if p.strip()]
    lead = parts[0].split(",")[0].strip() if parts else ""
    extra = parts[1].split(",")[0].strip() if len(parts) > 1 else ""
    label = lead if not extra else f"{lead} + {extra}"
    season = re.search(r"\[([^\]]+)\]", opp)
    if season:
        label += f" [{season.group(1)}]"
    return label


def aggregate_variant(rows: list) -> dict:
    n = len(rows)
    depth = {"win": 0, "lost_first": 0, "lost_mid": 0, "lost_last": 0, "draw": 0}
    cmd_turns = []
    turns, durations = [], []
    openers = []
    for r in rows:
        depth[loss_depth(r)] += 1
        if r.get("turns"):
            turns.append(r["turns"])
        if r.get("ms"):
            durations.append(r["ms"])
        ct = (r.get("commanderTurns") or [None])[0]
        if isinstance(ct, int) and ct >= 0:
            cmd_turns.append(ct)
        hands = r.get("hands") or []
        if hands and hands[0]:
            openers.append(hands[0])
    wins, draws = depth["win"], depth["draw"]
    decisive = n - draws
    share = wins / decisive if decisive else 0.0
    ci_lo, ci_hi = wilson_interval(wins, decisive)
    return {
        "n": n,
        "wins": wins,
        "losses": n - wins - draws,
        "draws": draws,
        "decisive": decisive,
        "win_share": share,
        "ci_lo": ci_lo,
        "ci_hi": ci_hi,
        "record": f"{wins}-{n - wins - draws}-{draws}",
        "lost_first": depth["lost_first"],
        "lost_mid": depth["lost_mid"],
        "lost_last": depth["lost_last"],
        "avg_turns": (sum(turns) / len(turns)) if turns else 0.0,
        "avg_minutes": (sum(durations) / len(durations) / 60000.0) if durations else 0.0,
        "cmd_avg_turn": (sum(cmd_turns) / len(cmd_turns)) if cmd_turns else None,
        "openers": openers,
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# Observed opener stats
# ---------------------------------------------------------------------------


def opener_stats(openers: list, land_set: set, artifact_set: set, commander: str) -> dict:
    n = len(openers)
    land_counts, art_counts = [], []
    two_plus = zero_lands = cmd_seen = 0
    for hand in openers:
        low = [c for c in hand if c]
        lands = sum(1 for c in low if c.lower() in land_set or c in land_set)
        arts = sum(1 for c in low if c in artifact_set)
        land_counts.append(lands)
        art_counts.append(arts)
        if lands >= 2:
            two_plus += 1
        if lands == 0:
            zero_lands += 1
        if commander in hand:
            cmd_seen += 1
    return {
        "n": n,
        "avg_lands": (sum(land_counts) / len(land_counts)) if land_counts else 0.0,
        "two_plus_obs": two_plus,
        "zero_obs": zero_lands,
        "avg_arts": (sum(art_counts) / len(art_counts)) if art_counts else 0.0,
        "cmd_seen": cmd_seen,
    }


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------


def esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


CSS = """
:root{--bg:#f5f5f2;--card:#fff;--ink:#1f2937;--muted:#6b7280;--line:#e5e7eb;
--blue:#2563eb;--blue-soft:#dbeafe;--blue-ink:#1e40af;--good:#15803d;--good-soft:#dcfce7;
--bad:#b91c1c;--bad-soft:#fee2e2;--warn:#92400e;--warn-soft:#fef3c7;--chip:#f3f4f6;--code:#1e3a8a}
*{box-sizing:border-box}
body{font:15px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
color:var(--ink);background:var(--bg);margin:0;padding:32px 16px}
.page{max-width:1100px;margin:0 auto}
header.masthead{background:linear-gradient(120deg,#1e3a8a,#2563eb);color:#fff;border-radius:14px;
padding:28px 30px;margin-bottom:22px;box-shadow:0 2px 10px rgba(30,58,138,.25)}
header.masthead .kicker{letter-spacing:.18em;text-transform:uppercase;font-size:11px;opacity:.85}
header.masthead h1{margin:6px 0 2px;font-size:28px;font-weight:700}
header.masthead .sub{opacity:.92;font-size:14px}
.metachips{margin-top:14px;display:flex;flex-wrap:wrap;gap:8px}
.metachips span{background:rgba(255,255,255,.16);border:1px solid rgba(255,255,255,.25);
border-radius:999px;padding:3px 12px;font-size:12px}
section{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:22px 24px;
margin-bottom:20px;break-inside:avoid}
h2{font-size:17px;margin:0 0 4px;color:var(--blue-ink);display:flex;align-items:center;gap:8px}
h2 .num{background:var(--blue);color:#fff;border-radius:6px;width:20px;height:20px;flex:0 0 20px;
display:inline-flex;align-items:center;justify-content:center;font-size:11px;font-weight:700}
.secdesc{color:var(--muted);font-size:13px;margin:0 0 16px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.tile{background:var(--bg);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.tile .v{font-size:24px;font-weight:700;line-height:1.15}
.tile .k{font-size:11.5px;color:var(--muted);margin-top:3px}
.tile.good .v{color:var(--good)}.tile.bad .v{color:var(--bad)}.tile.warn .v{color:var(--warn)}
.banner{border-radius:10px;padding:12px 16px;font-size:13.5px;margin:14px 0 2px}
.banner.note{background:var(--blue-soft);color:var(--blue-ink);border:1px solid #bfdbfe}
.banner.warnb{background:var(--warn-soft);color:var(--warn);border:1px solid #fde68a}
.findings{margin:10px 0 0;padding-left:20px}
.findings li{margin:5px 0}
table{border-collapse:collapse;width:100%;font-size:13px}
th{text-align:left;padding:7px 9px;border-bottom:2px solid var(--line);color:var(--muted);
font-size:11px;text-transform:uppercase;letter-spacing:.05em}
td{padding:7px 9px;border-bottom:1px solid var(--line);vertical-align:middle}
tr.zt:hover{background:var(--bg)}
.num-cell{font-variant-numeric:tabular-nums}
.chip{display:inline-block;background:var(--chip);border:1px solid var(--line);border-radius:999px;
padding:1px 9px;font-size:11.5px;margin:1px 3px 1px 0;white-space:nowrap}
.chip.addc{background:var(--good-soft);color:var(--good);border-color:#bbf7d0}
.chip.cutc{background:var(--bad-soft);color:var(--bad);border-color:#fecaca}
.chip.pilot{background:var(--blue-soft);color:var(--blue-ink);border-color:#bfdbfe}
.chip.err{background:var(--bad-soft);color:var(--bad);border-color:#fecaca}
.chip.win{background:var(--good-soft);color:var(--good);border-color:#bbf7d0}
.chip.draw{background:var(--warn-soft);color:var(--warn);border-color:#fde68a}
.chip.loss{background:var(--bad-soft);color:var(--bad);border-color:#fecaca}
.chip.cmc{background:#eff6ff;color:var(--blue-ink);border-color:#dbeafe;min-width:24px;text-align:center}
.layerhd{margin:18px 0 6px;font-size:13px;font-weight:700}
.layerhd small{color:var(--muted);font-weight:400}
.sharebar{position:relative;height:13px;background:var(--bg);border:1px solid var(--line);
border-radius:7px;overflow:hidden;min-width:130px;max-width:220px}
.sharebar i{position:absolute;top:0;bottom:0;border-radius:7px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:18px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(235px,1fr));gap:2px 14px}
.cardrow{display:flex;align-items:baseline;gap:7px;font-size:13px;padding:3px 0;border-bottom:
1px dotted var(--line)}
.cardrow .nm{flex:1}
.cardrow .qty{color:var(--muted);font-variant-numeric:tabular-nums}
.oppcard{border:1px solid var(--line);border-radius:10px;padding:12px 14px;background:var(--bg)}
footer{color:var(--muted);font-size:12px;margin-top:22px;line-height:1.6}
code{background:var(--chip);border-radius:5px;padding:1px 6px;color:var(--code);font-size:12px}
@media print{
 body{background:#fff;padding:0}
 section,header.masthead{box-shadow:none;border-radius:0;border-bottom:1px solid #ddd}
 header.masthead{background:#1e3a8a!important;-webkit-print-color-adjust:exact;print-color-adjust:exact}
 .page{max-width:none}
}
@media (prefers-color-scheme:dark){
 :root{--bg:#111827;--card:#1f2937;--ink:#e5e7eb;--muted:#9ca3af;--line:#374151;
 --blue-soft:#1e3a5f;--blue-ink:#bfdbfe;--good-soft:#14532d;--bad-soft:#7f1d1d;
 --warn-soft:#713f12;--warn:#fbbf24;--chip:#374151;--code:#93c5fd}
}
"""


def sharebar(share: float, decisive: int) -> str:
    if decisive <= 0:
        return '<span class="chip draw">no decisive games</span>'
    w = min(100, max(2, round(share * 100)))
    return f'<div class="sharebar"><i style="left:0;width:{w}%;background:var(--blue);opacity:.85"></i></div>'


def svg_curve(curve: dict) -> str:
    labels = ["0", "1", "2", "3", "4", "5", "6+"]
    vals = [curve.get(0, 0)] + [curve.get(i, 0) for i in range(1, 7)]
    mx = max(vals + [1])
    total = sum(vals) or 1
    W, H, pad, bw = 560, 200, 30, 72
    base_y = H - 30
    top_h = base_y - 30
    parts = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="mana curve" '
             f'style="width:100%;max-width:{W}px">']
    for i, v in enumerate(vals):
        h = 0 if v == 0 else max(4, round(top_h * v / mx))
        x = pad + i * bw + 12
        barw = bw - 24
        y = base_y - h
        pct = round(v / total * 100)
        parts.append(f'<rect x="{x}" y="{y}" width="{barw}" height="{h}" rx="4" '
                     f'fill="#2563eb" opacity="0.85"/>')
        parts.append(f'<text x="{x + barw / 2}" y="{y - 6}" text-anchor="middle" font-size="11" '
                     f'fill="currentColor">{v} <tspan font-size="10">{pct}%</tspan></text>')
        parts.append(f'<text x="{x + barw / 2}" y="{H - 10}" text-anchor="middle" font-size="11" '
                     f'fill="#6b7280">{labels[i]}</text>')
    parts.append(f'<line x1="{pad - 6}" y1="{base_y}" x2="{W - 12}" y2="{base_y}" '
                 f'stroke="#9ca3af" stroke-width="1"/>')
    parts.append("</svg>")
    return "".join(parts)


def chips_for_changes(changes: list) -> str:
    out = []
    for c in changes:
        if c.get("add"):
            out.append(f'<span class="chip addc">+ {esc(c["add"])}</span>')
        if c.get("cut"):
            out.append(f'<span class="chip cutc">&minus; {esc(c["cut"])}</span>')
    return "".join(out) or '<span class="chip">stock build</span>'


def human_changes(changes: list) -> str:
    if not changes:
        return "stock list"
    adds = [c["add"] for c in changes if c.get("add")]
    cuts = [c["cut"] for c in changes if c.get("cut")]
    bits = []
    if adds:
        bits.append("+" + ", ".join(adds))
    if cuts:
        bits.append("−" + ", ".join(cuts))
    return "; ".join(bits)


def short_label(vid: str, spec: dict) -> str:
    changes = spec.get("variants", {}).get(vid) or []
    if vid == "baseline":
        return "Stock precon"
    first = changes[0] if changes else {}
    if vid.startswith("deep_") and first.get("add"):
        return first["add"]
    if vid.startswith("theme_"):
        m = re.match(r"theme_(.*?)__add_(.+)", vid)
        if m:
            return f"{m.group(1).replace('_', ' ')} · + {m.group(2).replace('_', ' ')}"
    if vid.startswith("add_"):
        return "+ " + vid[4:].replace("_", " ")
    if vid.startswith("payoff_"):
        if changes and changes[0].get("add"):
            return "+ " + ", ".join(c["add"] for c in changes if c.get("add"))
        return vid
    return vid


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------


def build_html(args) -> str:
    spec = load_spec(args.variants)
    base_deck = args.base_deck or (spec.get("base") if spec.get("base") else "") or re_.BASE_DECK
    facts = deck_facts(base_deck, args.cardsfolder)

    grouped = load_games(args.data_dir, set(spec["variants"]))
    agg = {}
    pod: list = []
    all_rows: list = []
    first_seats = {}
    for vid, entry in grouped.items():
        if entry["status"] == "played" and entry["rows"]:
            agg[vid] = aggregate_variant(entry["rows"])
            all_rows.extend(entry["rows"])
            for r in entry["rows"]:
                d = r.get("decks", [])
                if len(d) >= 4 and not pod:
                    pod = d[1:]
                first_seats[r.get("firstSeat")] = \
                    first_seats.get(r.get("firstSeat"), 0) + 1
    baseline = agg.get("baseline") or aggregate_variant([])

    games_total = len(all_rows)
    focal_wins_n = sum(a["wins"] for a in agg.values())
    draws_n = sum(a["draws"] for a in agg.values())
    errors = [vid for vid, e in grouped.items() if e["zero_byte"] and vid not in agg]
    partial = [vid for vid, e in grouped.items() if e["zero_byte"] and vid in agg]
    missing = [vid for vid, e in grouped.items()
               if e["status"] == "missing" and not e["zero_byte"] and vid not in agg]
    decisive_total = sum(a["decisive"] for a in agg.values())

    # ---- pod census (one pod per rep seed) and opponent rollup across pods
    from collections import Counter
    pod_rows: dict = {}
    for r in all_rows:
        decks = r.get("decks", [])
        if len(decks) < 4:
            continue
        key = tuple(decks[1:])
        pr = pod_rows.setdefault(key, {"n": 0, "wins": 0, "losses": 0, "draws": 0,
                                       "seed": r.get("seed")})
        pr["n"] += 1
        if r.get("timeout") or r.get("winnerSeat", -1) == -1:
            pr["draws"] += 1
        elif r.get("winnerSeat") == 0:
            pr["wins"] += 1
        else:
            pr["losses"] += 1
    pods_list = sorted(pod_rows.items(), key=lambda kv: -kv[1]["n"])

    opp_rollup: dict = {}
    for r in all_rows:
        if r.get("timeout") or r.get("winnerSeat", -1) == -1:
            continue
        decks = r.get("decks", [])
        if len(decks) < 4:
            continue
        for i in range(1, 4):
            rec = opp_rollup.setdefault(decks[i], {
                "n": 0, "wins": 0, "pods": set()})
            rec["n"] += 1
            if r.get("winnerSeat") == i:
                rec["wins"] += 1
    pod_of = {tuple(r.get("decks", [])[1:]): True for r in all_rows if len(r.get("decks", [])) >= 4}
    for opp, rec in opp_rollup.items():
        rec["pod_count"] = sum(1 for p in pod_of if opp in p)
    top_opps = sorted(opp_rollup.items(), key=lambda kv: -kv[1]["n"])[:12]

    # ---- names seen anywhere (opener hands, spec changes) for land/artifact sets
    extra_names = []
    for e in spec["variants"].values():
        for c in e:
            extra_names.append(c.get("add", ""))
            extra_names.append(c.get("cut", ""))
    for a in agg.values():
        for h in a["openers"]:
            extra_names.extend(h)
    extra_db = load_card_db(args.cardsfolder, extra_names)
    land_set = {e["name"] for e in facts["lands"]}
    for n in extra_names:
        if n and n in extra_db and "Land" in extra_db[n]["types"]:
            land_set.add(n)
    artifact_set = {e["name"] for e in facts["artifacts"]}
    for n in extra_names:
        if n and n in extra_db and "Artifact" in extra_db[n]["types"]:
            artifact_set.add(n)

    # ---- exact draw odds
    pool = facts["total_qty"] - 1  # commander in command zone
    land_qty_total = sum(e["qty"] for e in facts["lands"])
    qty_by_name = {e["name"]: e["qty"] for e in facts["main"]}
    rock_qty = sum(qty_by_name.get(r, 0) for r in facts["rocks"])
    art_qty = sum(e["qty"] for e in facts["artifacts"])
    p_ge2 = at_least(pool, land_qty_total, 7, 2)
    p_ge3 = at_least(pool, land_qty_total, 7, 3)
    p_zero = 1 - at_least(pool, land_qty_total, 7, 1)
    p_rock = at_least(pool, rock_qty, 7, 1)
    p_art = at_least(pool, art_qty, 7, 1)

    all_openers = [h for a in agg.values() for h in a["openers"] if h]
    obs = opener_stats(all_openers, land_set, artifact_set, facts["commander"])

    now = datetime.datetime.now()
    run_name = os.path.basename(os.path.normpath(args.data_dir))
    seeds_seen = [r.get("seed") for r in all_rows if r.get("seed") is not None]
    seed_lo, seed_hi = (min(seeds_seen), max(seeds_seen)) if seeds_seen else (0, 0)
    first_zero = first_seats.get(0, 0)
    focal_opens_pct = first_zero / games_total * 100 if games_total else 0.0

    h = []
    h.append(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Deck Lab Report — {esc(facts["name"])} · {esc(run_name)}</title>
<style>{CSS}</style>
</head>
<body>
<div class="page">
<header class="masthead">
  <div class="kicker">Forge Sim · Deck Lab Report</div>
  <h1>{esc(facts["name"])}</h1>
  <div class="sub">Commander: {esc(facts["commander"])} · Mono-Blue Artifacts ·
   run <b>{esc(run_name)}</b> · base deck {esc(os.path.basename(base_deck))}</div>
  <div class="metachips">
    <span>{games_total} games</span>
    <span>{len(agg)} variants played</span>
    <span>{len(errors) + len(missing)} untested</span>
    <span>{len(pods_list)} opponent pods · seeds {seed_lo}–{seed_hi}</span>
    <span>1 game per rep-unit</span>
    <span>focal opened {first_zero} of {games_total} games</span>
    <span>generated {now:%Y-%m-%d %H:%M}</span>
  </div>
</header>
""")

    # ================= 1. Executive summary =================
    winning_variants = [(v, a) for v, a in agg.items() if a["wins"] > 0]
    winning_variants.sort(key=lambda kv: (-kv[1]["win_share"], -kv[1]["decisive"], kv[0]))
    focal_share = focal_wins_n / decisive_total * 100 if decisive_total else 0.0
    h.append(f"""
<section>
  <h2><span class="num">1</span> Executive summary</h2>
  <p class="secdesc">Pilot campaign of the <b>{esc(facts["name"])}</b> test lab:
   {len(pods_list)} opponent pods (one per rep seed), {len(agg)} deck variants,
   played to a 300 s game clock. Focal = seat 0.</p>
  <div class="tiles">
    <div class="tile"><div class="v">{games_total}</div><div class="k">Games recorded</div></div>
    <div class="tile good"><div class="v">{focal_share:.0f}%</div><div class="k">Focal win rate (decisive games)</div></div>
    <div class="tile good"><div class="v">{focal_wins_n}</div><div class="k">Focal wins</div></div>
    <div class="tile warn"><div class="v">{draws_n}</div><div class="k">Draws / clock-outs ({draws_n / games_total * 100 if games_total else 0:.0f}%)</div></div>
    <div class="tile bad"><div class="v">{len(errors) + len(missing)}</div><div class="k">Untested variants</div></div>
    <div class="tile"><div class="v">{baseline["avg_turns"]:.0f} T</div><div class="k">Avg game (baseline)</div></div>
  </div>
  <div class="banner warnb"><b>Read with cap:</b> sample sizes per variant are still
   modest (seeded campaign mid-flight; {len(partial)} variants have newer reps still
   landing), so win shares are directional. The harness graduate rule requires ≥50
   games and a Wilson lower bound above baseline before promoting a variant — apply
   that bar before trusting orderings.</div>
  <ul class="findings">""")
    findings = []
    findings.append(
        f"<li><b>Experiment design:</b> the runner drew <b>{len(pods_list)} opponent pods</b>, "
        f"one per seed ({seed_lo}–{seed_hi}); the focal deck opened "
        f"{first_zero} of {games_total} games ({focal_opens_pct:.0f}%), so seat advantage "
        f"is randomised by the seed plan rather than pinned on the opposition.</li>")
    if winning_variants:
        winners_ui = ", ".join(
            f"<code>{esc(short_label(v, spec))}</code> ({a['win_share'] * 100:.0f}%, "
            f"{a['n']}g)" for v, a in winning_variants[:4])
        findings.append(
            f"<li><b>Leaders this run:</b> {len(winning_variants)} variants won at least one "
            f"game; strongest rows so far — {winners_ui}.</li>")
    if errors:
        names = ", ".join(f"<code>{esc(short_label(v, spec))}</code>" for v in errors[:8])
        findings.append(
            f"<li><b>{len(errors)} variants failed deck-load</b> (0-byte JSONLs): {names}. "
            f"Fix materialisation issues before the next campaign"
            + (f"; {len(partial)} more variants had bundled units still writing when this "
               f"report was generated." if partial else "") + ".</li>")
    findings.append(
        f"<li><b>Draw pressure is real:</b> {draws_n} of {games_total} games ended in the "
        f"300 s clock cap ({draws_n / games_total * 100 if games_total else 0:.0f}%); "
        f"these are excluded from every win share, so the effective record is "
        f"{focal_wins_n}-{games_total - focal_wins_n - draws_n}-{draws_n}.</li>")
    findings.append(
        f"<li><b>Mulligan texture:</b> across {obs['n']} sampled openers, "
        f"{obs['two_plus_obs']}/{obs['n'] or 1} had ≥2 lands — above the "
        f"{p_ge2 * 100:.0f}% theoretical rate. Expected: the AI keep policy biases kept "
        f"hands (no mulligan decisions are recorded in the JSONLs), and every "
        f"land-free opener ({p_zero * 100:.1f}% theory) was shipped.</li>")
    h.append("".join(findings))
    h.append("</ul>\n</section>")

    # ================= 2. Variant leaderboard =================
    h.append("""
<section>
  <h2><span class="num">2</span> Variant leaderboard</h2>
  <p class="secdesc">Win share counts decisive games only (draws/timeouts excluded).
   “Depth” = focal seat's exit position: first / mid / last victim. Swap chips are the
   materialized changes per variant.</p>
""")
    by_layer: dict = {}
    for vid, a in agg.items():
        by_layer.setdefault(variant_layer(vid), {})[vid] = a
    for layer in ("baseline", "deep", "theme", "payoff", "add"):
        group = by_layer.get(layer) or {}
        if not group:
            continue
        h.append(f'<div class="layerhd">{LAYER_TITLE[layer]} '
                 f'<small>· {len(group)} variants · ordered by win share</small></div>')
        h.append("<table><tr><th>Variant</th><th>W-L-D</th><th>Win share (Wilson 95%)"
                 "</th><th>Depth</th><th>Turns</th><th>Cmd</th><th>Swaps</th></tr>")
        for vid, a in sorted(group.items(), key=lambda kv: (-kv[1]["win_share"], -kv[1]["n"], kv[0])):
            if a["wins"]:
                rec_chip, tag = "win", "win"
            elif a["draws"] and a["losses"] == 0:
                rec_chip, tag = "draw", "draw"
            else:
                rec_chip, tag = "loss", "loss"
            depth_bits = []
            for key, word in (("lost_first", "1st"), ("lost_mid", "mid"), ("lost_last", "last")):
                depth_bits.append(f"{word}×{a[key]}" if a[key] > 1 else word if a[key] == 1 else "")
            depth_str = ", ".join(b for b in depth_bits if b) or "—"
            cmd_txt = f"T{a['cmd_avg_turn']:.0f}" if a["cmd_avg_turn"] is not None else "never"
            ci_txt = f"{a['ci_lo'] * 100:.0f}–{a['ci_hi'] * 100:.0f}%" if a["decisive"] else "—"
            share_txt = f"{a['win_share'] * 100:.0f}%" if a["decisive"] else "n/a"
            h.append(
                f'<tr class="zt"><td><b>{esc(short_label(vid, spec))}</b>'
                f'<br><span style="font-size:11px;color:var(--muted)">{esc(vid)}</span></td>'
                f'<td><span class="chip {tag}">{a["record"]}</span></td>'
                f'<td class="num-cell">{share_txt}<br>{sharebar(a["win_share"], a["decisive"])}'
                f'<span style="font-size:10.5px;color:var(--muted)">{ci_txt}</span></td>'
                f'<td>{esc(depth_str)}</td>'
                f'<td class="num-cell">{a["avg_turns"]:.0f}</td>'
                f'<td class="num-cell">{esc(cmd_txt)}</td>'
                f'<td>{chips_for_changes(spec["variants"].get(vid, []))}</td></tr>')
        h.append("</table>")
    unplayed = errors + missing
    if unplayed:
        h.append(f'<div class="layerhd">Untested <small>· {len(unplayed)} variants declared '
                 f'but no games recorded</small></div><p>')
        for vid in unplayed:
            reason = "deck failed to load (0-byte JSONL)" if vid in errors \
                else "no output file for this id"
            h.append(f'<span class="chip err" title="{esc(reason)}">'
                     f'{esc(short_label(vid, spec))} — {esc(human_changes(spec["variants"].get(vid, [])))}'
                     f'</span> ')
        h.append("</p>")
    h.append("</section>")

    # ================= 3. Pods & opponent matchup =================
    h.append(f"""
<section>
  <h2><span class="num">3</span> Opponent pods &amp; matchup rollup</h2>
  <p class="secdesc">The runner samples a random pod of three opponents per rep seed, so
   the focal deck faced <b>{len(pods_list)} different pods</b> in this run. Left: each pod
   with the focal deck's record against it. Right: opponent commanders that recurred
   across pods, with how often they beat the focal deck.</p>
 """)
    # ---- pod census
    h.append(f'<div class="layerhd">Pod census <small>· {len(pods_list)} pods · '
             f'{games_total} games overall</small></div>')
    h.append("<table><tr><th>Seed</th><th>Opponents (1 → 3)</th><th>Games</th>"
             "<th>Focal W-L-D</th><th>W-L-D vs decisive only</th></tr>")
    for key, pr in pods_list:
        opps_ui = " ".join(f'<span class="chip">{esc(clean_opp(o))}</span>' for o in key)
        dd = pr["wins"] + pr["losses"]
        rate = pr["wins"] / dd * 100 if dd else 0.0
        pod_tag = "win" if rate >= 50 else ("loss" if rate < 25 else "draw")
        h.append(
            f'<tr class="zt"><td><code>{esc(str(pr["seed"]))}</code></td>'
            f'<td>{opps_ui}</td>'
            f'<td class="num-cell">{pr["n"]}</td>'
            f'<td><span class="chip {pod_tag}">{pr["wins"]}-{pr["losses"]}-{pr["draws"]}</span></td>'
            f'<td class="num-cell">{rate:.0f}% wins ({dd} decisive)</td></tr>')
    h.append("</table>")
    # ---- recurring opponents
    if top_opps:
        h.append(f'<div class="layerhd">Most-recurring opponents <small>· pools '
                 f'combined; decisive games only</small></div>')
        h.append("<table><tr><th>Opponent</th><th>In pods</th><th>Games vs focal</th>"
                 "<th>Beat the focal deck</th><th>Raw deck entry</th></tr>")
        for deck_str, rec in top_opps:
            beats = rec["wins"] / rec["n"] * 100 if rec["n"] else 0.0
            tag = "win" if beats <= 25 else ("loss" if beats > 50 else "draw")
            h.append(
                f'<tr class="zt"><td><b>{esc(clean_opp(deck_str))}</b></td>'
                f'<td class="num-cell">{rec["pod_count"]}</td>'
                f'<td class="num-cell">{rec["n"]}</td>'
                f'<td><span class="chip {tag}">{rec["wins"]}</span> '
                f'<span style="font-size:11px;color:var(--muted)">({beats:.0f}%)</span></td>'
                f'<td style="font-size:11px;color:var(--muted)">{esc(deck_str)}</td></tr>')
        h.append("</table>")
        h.append('<div class="banner note"><b>Matchup note:</b> “beat the focal deck” = '
                 'share of decisive games that opponent\'s seat won. Beats-25%-or-less '
                 'means the focal deck has the upper hand across pods; flag anything '
                 'above ~50% as the recurring danger card to answer next campaign</div>')
    h.append("</section>")

    # ================= 4. Game flow =================
    timeline = [(short_label(v, spec), r.get("turns", 0))
                for v, a in agg.items() for r in a["rows"] if r.get("turns")]
    avg_all = sum(t for _, t in timeline) / len(timeline) if timeline else 0.0
    elim_depth = {"first": 0, "mid": 0, "last": 0, "survived": 0, "draw": 0}
    for a in agg.values():
        key = a["lost_first"], a["lost_mid"], a["lost_last"], a["wins"], a["draws"]
        elim_depth["first"] += a["lost_first"]
        elim_depth["mid"] += a["lost_mid"]
        elim_depth["last"] += a["lost_last"]
        elim_depth["survived"] += a["wins"]
        elim_depth["draw"] += a["draws"]
    decided = (elim_depth["first"] + elim_depth["mid"] + elim_depth["last"]
               + elim_depth["survived"]) or 1
    h.append(f"""
<section>
  <h2><span class="num">4</span> Game flow &amp; eliminations</h2>
  <p class="secdesc">How the {games_total} games typically unfold: length, who goes out
   first, and how deep the focal deck rides into the pod. The longest games show why the
   300 s clock generates so many draws.</p>
  <div class="tiles">
    <div class="tile"><div class="v">{avg_all:.0f} T</div><div class="k">Avg game length (all games)</div></div>
    <div class="tile warn"><div class="v">{elim_depth["draw"]}</div><div class="k">Clock draws</div></div>
    <div class="tile good"><div class="v">{elim_depth["survived"]}</div><div class="k">Focal survived (won outright)</div></div>
    <div class="tile bad"><div class="v">{elim_depth["first"]} / {elim_depth["mid"]} / {elim_depth["last"]}</div><div class="k">Elims: 1st / mid / last out</div></div>
    <div class="tile"><div class="v">{max((t for _, t in timeline), default=0)}</div><div class="k">Longest decisive game (turns)</div></div>
  </div>
  <div class="layerhd">Elimination depth <small>· share of {decided} non-draw games</small></div>
  <div style="display:flex;height:20px;border-radius:6px;overflow:hidden;border:1px solid var(--line);font-size:11px;color:#fff">
   <i style="width:{elim_depth['survived'] / decided * 100:.0f}%;background:#22c55e;display:inline-flex;align-items:center;justify-content:center">won {elim_depth['survived']}</i>
   <i style="width:{elim_depth['last'] / decided * 100:.0f}%;background:#60a5fa;display:inline-flex;align-items:center;justify-content:center">out 3rd {elim_depth['last']}</i>
   <i style="width:{elim_depth['mid'] / decided * 100:.0f}%;background:#f87171;display:inline-flex;align-items:center;justify-content:center">out 2nd {elim_depth['mid']}</i>
   <i style="width:{elim_depth['first'] / decided * 100:.0f}%;background:#ef4444;display:inline-flex;align-items:center;justify-content:center">out 1st {elim_depth['first']}</i>
  </div>""")
    h.append('<div class="layerhd">Longest games <small>· turns, top 12</small></div>')
    mx_turns = max((t for _, t in timeline), default=1)
    for label, turns in sorted(timeline, key=lambda t: -t[1])[:12]:
        h.append(f'<div style="display:flex;align-items:center;gap:8px;font-size:12px;margin:2px 0">'
                 f'<span style="width:250px;text-align:right;overflow:hidden;'
                 f'text-overflow:ellipsis;white-space:nowrap;color:var(--muted)">{esc(label)}</span>'
                 f'<span style="flex:1;height:10px;background:var(--bg);border:1px solid var(--line);'
                 f'border-radius:5px;overflow:hidden"><i style="display:block;height:100%;'
                 f'width:{min(100, turns / max(2, mx_turns) * 100):.0f}%;'
                 f'background:var(--blue)"></i></span><b>{turns}</b></div>')
    h.append("</section>")

    # ================= 5. Hand quality & draw odds =================
    h.append(f"""
<section>
  <h2><span class="num">5</span> Hand quality &amp; draw odds</h2>
  <p class="secdesc">Left column = observed opening hands from the {len(all_openers)}
   recorded games. Right column = <b>exact</b> hypergeometric probabilities computed
   from the stock list itself (99-card drawable pool: the commander is always available
   from the command zone, so it is not in the math).</p>
  <div class="grid2">
   <div>
    <div class="layerhd">Observed openers <small>· n = {obs["n"]}</small></div>
    <table>
     <tr><th>Metric</th><th>Observed</th></tr>
     <tr><td>Average lands in opener</td><td class="num-cell"><b>{obs["avg_lands"]:.2f}</b> of 7</td></tr>
     <tr><td>Openers with ≥2 lands</td><td class="num-cell">{obs["two_plus_obs"]} / {obs["n"]}</td></tr>
     <tr><td>Openers with 0 lands (auto-mulligan)</td><td class="num-cell">{obs["zero_obs"]} / {obs["n"]}</td></tr>
     <tr><td>Average artifacts in opener</td><td class="num-cell">{obs["avg_arts"]:.2f}</td></tr>
     <tr><td>Commander seen in opener</td><td class="num-cell">{obs["cmd_seen"]} / {obs["n"]}</td></tr>
    </table>
    <div class="banner note" style="margin-top:10px"><b>Mulligan guidance</b> (from the
     exact math, not this sample): keep solid with lands + any 0–2 cmc rock; a
     2-land opener is fine given {p_ge2 * 100:.0f}% of hands reach ≥2 lands; a
     1-land hand without a rock is a free-mulligan candidate. Observed land-free
     openers so far: <b>{obs["zero_obs"]}</b> of {obs["n"] or 1}
     — the deck's land base ({land_qty_total} lands to {art_qty} artifacts)
     is doing its job.</div>
   </div>
   <div>
    <div class="layerhd">Exact draw probabilities <small>· start 7 / pool {pool}</small></div>
    <table>
     <tr><th>Event (opening 7)</th><th>Probability</th></tr>""")
    odds = [
        ("At least 2 lands (a keepable start)", p_ge2),
        ("At least 3 lands", p_ge3),
        ("0 lands (shipped hand)", p_zero),
        ("At least one mana rock (≤2 cmc, taps for mana)", p_rock),
        ("At least one artifact spell of any kind", p_art),
    ]
    for label, p in odds:
        h.append(f'<tr><td>{esc(label)}</td><td class="num-cell"><b>{p * 100:.1f}%</b>'
                 f'<div style="margin-top:3px">{sharebar(p, 1)}</div></td></tr>')
    h.append(f"""    </table>
    <p style="font-size:12px;color:var(--muted)">Mana-rock count used in the math:
     {rock_qty} copies ({esc(", ".join(facts["rocks"][0:4]))}…). Land count: {land_qty_total}.</p>
   </div>
  </div>
</section>""")

    # ================= 6. Deck appendix =================
    curve_svg = svg_curve(facts["curve"])
    art_creatures = [e for e in facts["creatures"] if "Artifact" in e["types"]]
    art_all = len(facts["artifacts"]) + len(art_creatures)
    pips_html = " ".join(f'<span class="chip pilot">{esc(k)} × {v}</span>' for k, v in sorted(facts["pips"].items()))
    lands_desc = ", ".join(f"{n} × {q}" for n, q in
                           sorted({e["name"]: e["qty"] for e in facts["lands"]}.items(),
                                  key=lambda kv: -kv[1]))
    h.append(f"""
<section>
  <h2><span class="num">6</span> Deck appendix — {esc(facts["name"])}</h2>
  <p class="secdesc">The 100-card stock list the campaign started from.
   Strategy: {esc(facts["commander"])} pays you a Thopter for every artifact spell —
   the deck is a swarm engine that ramps on cheap artifacts, refills with
   draw engines, and closes with go-wide payoffs and big swingy finishers.</p>
  <div class="tiles" style="margin-bottom:16px">
   <div class="tile"><div class="v">100</div><div class="k">cards · {len({e["name"] for e in facts["entries"]})} unique names</div></div>
   <div class="tile"><div class="v">{land_qty_total}</div><div class="k">Lands ({len(facts["lands"])} names)</div></div>
   <div class="tile"><div class="v">{art_all}</div><div class="k">Artifacts ({len(facts["artifacts"])} spells + {len(art_creatures)} creatures)</div></div>
   <div class="tile"><div class="v">{facts["avg_spell_cmc"]:.2f}</div><div class="k">Average spell CMC</div></div>
   <div class="tile"><div class="v">{facts["commander_cmc"]}</div><div class="k">Commander CMC</div></div>
  </div>
  <div style="margin-bottom:14px">
   {pips_html}
   <span class="chip">lands — {esc(lands_desc)}</span>
  </div>
  {curve_svg}
  <div class="banner note" style="margin-top:8px"><b>Curve read:</b> the deck runs a
   {facts["curve"].get(1, 0)}-card one-drop suite and leans its cheap ramp on {rock_qty}
   mana rocks; big plays start at CMC 4 (Nevinyrral's Disk, Meteor Golem, Shimmer Myr,
   Steel Hellkite …), consistent with long games that pay off late booms.</div>
""")
    groups: dict = {}
    for e in facts["main"]:
        groups.setdefault(e["category"], []).append(e)
    counts_html = " ".join(
        f'<span class="chip">{cat} × {sum(e["qty"] for e in groups[cat])}</span>'
        for cat in CATEGORY_ORDER if groups.get(cat))
    h.append(f'<div class="layerhd">Grouped card list <small>· {counts_html}</small></div>')
    for cat in CATEGORY_ORDER:
        entries = groups.get(cat) or []
        if not entries:
            continue
        copies = sum(e["qty"] for e in entries)
        title = f"{cat}s" if (copies > 1 or len(entries) > 1) else cat
        h.append(f'<div style="margin-top:14px;font-size:12.5px;font-weight:700">'
                 f'{esc(title)}'
                 f'<span style="color:var(--muted);font-weight:400"> · {copies} copies</span></div>'
                 f'<div class="cards">')
        for e in sorted(entries, key=lambda e: (-(e["cmc"] or 0), e["name"])):
            mc = e.get("mana_cost") or "—"
            h.append(f'<div class="cardrow"><span class="qty">{e["qty"]}×</span>'
                     f'<span class="nm">{esc(e["name"])}</span>'
                     f'<span class="chip cmc">{esc(mc)}</span></div>')
        h.append("</div>")
    h.append("</section>")

    # ================= 7. Methodology =================
    h.append(f"""
<section>
  <h2><span class="num">7</span> Methodology</h2>
  <ul class="findings">
   <li><b>Focal seat.</b> The deck under test is seat 0. A win is
    <code>winnerSeat == 0</code> in a non-timeout game.</li>
   <li><b>Win share.</b> wins ÷ decisive games (draws/timeouts excluded); the band
    next to it is the Wilson 95% interval — extremely wide at this sample size.</li>
   <li><b>Depth.</b> Position of seat 0 in <code>eliminationOrder</code>: eliminated
    first / in the middle / last (survived).</li>
   <li><b>Draw odds.</b> Exact hypergeometric on the {pool}-card pool; “mana rock” =
    artifact CMC ≤ 2 whose Oracle text contains an {esc("{T}")}: Add ability.</li>
   <li><b>Errors.</b> Zero-byte JSONL ⇒ Forge could not load that deck; it is recorded
    as an error unit and never as a game (harness invariant).</li>
   <li><b>Life -1.</b> Some scorers encode eliminated seats as −1; life cells are
    shown raw, not normalized.</li>
   <li><b>Reproducibility.</b> Regenerate with:
    <code>python3 generate_html_report.py --data-dir {esc(args.data_dir)} --out {esc(args.out)}</code>.</li>
  </ul>
  <footer>
   Data: {esc(args.data_dir)} · base deck: {esc(base_deck)} · spec:
   {esc(args.variants)} · generated {now:%Y-%m-%d %H:%M} by
   <code>generate_html_report.py</code> (stdlib-only Forge sim reporter).
  </footer>
</section>
</div>
</body>
</html>""")
    return "".join(h)


def main() -> None:
    ap = argparse.ArgumentParser(description="Deck-lab HTML report from campaign JSONLs")
    ap.add_argument("--data-dir", default="data/genA")
    ap.add_argument("--variants", default="keen_variants.yaml")
    ap.add_argument("--base-deck", default="")
    ap.add_argument("--out", default="docs/deck-report.html")
    ap.add_argument("--cardsfolder", default=os.path.join(
        re_.FORGE_ROOT, "forge-gui", "res", "cardsfolder"))
    args = ap.parse_args()
    html = build_html(args)
    out_dir = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[Done] wrote {args.out} ({len(html) / 1024:.0f} KiB)")


if __name__ == "__main__":
    main()
