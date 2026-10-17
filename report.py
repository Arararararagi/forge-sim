import os
import sys
import json
import math
import argparse
import csv
import re


def wilson_interval(wins: int, n: int, z: float = 1.96) -> tuple:
    if n == 0:
        return (0.0, 0.0)
    p = wins / n
    denominator = 1 + z ** 2 / n
    centre_adjusted_probability = p + z ** 2 / (2 * n)
    adjusted_standard_deviation = math.sqrt((p * (1 - p) + z ** 2 / (4 * n)) / n)
    lower_bound = (centre_adjusted_probability - z * adjusted_standard_deviation) / denominator
    upper_bound = (centre_adjusted_probability + z * adjusted_standard_deviation) / denominator
    return (max(0.0, lower_bound), min(1.0, upper_bound))


def focal_outcomes(rows: list) -> list:
    outcomes = []
    for r in rows:
        if r.get("timeout") or r.get("winnerSeat", -1) == -1:
            continue  # Exclude draws/timeouts from win share
        outcomes.append(1 if r.get("winnerSeat") == 0 else 0)
    return outcomes


def failure_breakdown(rows: list) -> dict:
    bd = {"win": 0, "lost_first": 0, "lost_mid": 0, "lost_last": 0, "draw": 0}
    for r in rows:
        if r.get("timeout") or r.get("winnerSeat", -1) == -1:
            bd["draw"] += 1
            continue
        if r.get("winnerSeat") == 0:
            bd["win"] += 1
            continue
        elim = r.get("eliminationOrder", []) or []
        if len(elim) == 0 or elim[0] != 0:
            bd["lost_first"] += 1
        elif 0 not in elim:
            bd["lost_last"] += 1
        else:
            try:
                idx = elim.index(0)
                if idx == 0:
                    bd["lost_first"] += 1
                elif idx == len(elim) - 1:
                    bd["lost_last"] += 1
                else:
                    bd["lost_mid"] += 1
            except ValueError:
                bd["lost_mid"] += 1
    return bd


def canonical_variant_id(fname: str) -> str:
    """Extract canonical variant id by stripping _r/_s suffixes from .jsonl filename."""
    name = fname.replace(".jsonl", "")
    # Strip seed suffix _sN first, then rep suffix _rN
    name = re.sub(r"_s\d+$", "", name)
    name = re.sub(r"_r\d+$", "", name)
    return name


def load_results(data_dir: str) -> list:
    results = []
    baseline_share = 0.0
    # Aggregate all .jsonl files by canonical variant_id
    grouped = {}
    for fname in os.listdir(data_dir):
        if not fname.endswith(".jsonl"):
            continue
        cvid = canonical_variant_id(fname)
        if cvid not in grouped:
            grouped[cvid] = []
        grouped[cvid].append(os.path.join(data_dir, fname))

    # Record every variant declared in the manifest, even if it produced no
    # games (failed deck-load, skipped before materialisation, etc.) so that
    # no variant silently disappears from the report.
    manifest_path = os.path.join(data_dir, "manifest.json")
    if os.path.exists(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            for vid in manifest.get("variants", []):
                grouped.setdefault(vid, [])
            for vid in manifest.get("skipped", []):
                grouped.setdefault(vid, [])
        except Exception:
            pass

    for variant_id, fpaths in grouped.items():
        rows = []
        for fpath in fpaths:
            with open(fpath, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        try:
                            rows.append(json.loads(line))
                        except Exception:
                            continue

        outcomes = focal_outcomes(rows)
        wins = sum(outcomes)
        n = len(outcomes)
        share = wins / n if n > 0 else 0.0
        ci_lo, ci_hi = wilson_interval(wins, n)
        timeouts = sum(1 for r in rows if r.get("timeout"))
        total_games = len(rows)
        timeout_rate = timeouts / total_games if total_games > 0 else 0.0
        bd = failure_breakdown(rows)
        lost_first_rate = bd["lost_first"] / total_games if total_games > 0 else 0.0
        turns = [r.get("turns", 0) for r in rows if r.get("turns")]
        avg_turns = sum(turns) / len(turns) if turns else 0.0
        results.append(
            {
                "variant": variant_id,
                "games": total_games,
                "wins": wins,
                "win_share": share,
                "ci_lo": ci_lo,
                "ci_hi": ci_hi,
                "timeouts": timeouts,
                "timeout_rate": timeout_rate,
                "lost_first_rate": lost_first_rate,
                "avg_turns": avg_turns,
            }
        )
        if variant_id == "baseline":
            baseline_share = share

    # Mark winners
    for r in results:
        r["winner"] = (
            r["games"] >= 50
            and r["ci_lo"] > baseline_share
            and r["timeout_rate"]
            <= (
                next((x["timeout_rate"] for x in results if x["variant"] == "baseline"), 0.0)
                + 0.05
            )
        )
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--out-csv", required=True)
    ap.add_argument("--out-md", required=True)
    args = ap.parse_args()
    results = load_results(args.data_dir)
    # Sort: baseline first, then winners, then by win share desc
    baseline = [r for r in results if r["variant"] == "baseline"]
    others = [r for r in results if r["variant"] != "baseline"]
    others.sort(key=lambda x: (not x["winner"], -x["win_share"], x["variant"]))
    results_sorted = baseline + others
    # Write CSV
    with open(args.out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "variant",
                "games",
                "wins",
                "win_share",
                "ci_lo",
                "ci_hi",
                "timeouts",
                "timeout_rate",
                "lost_first_rate",
                "avg_turns",
                "winner",
            ],
        )
        w.writeheader()
        for r in results_sorted:
            w.writerow(r)
    # Write MD
    with open(args.out_md, "w", encoding="utf-8") as f:
        f.write("# Sim Report\n\n")
        f.write("| Variant | Games | Wins | Win Share | 95% CI | Timeouts | TO Rate | Lost First | Avg Turns | Winner |\n")
        f.write("|---|---|---|---|---|---|---|---|---|---|\n")
        for r in results_sorted:
            ci = f"[{r['ci_lo']:.3f}, {r['ci_hi']:.3f}]"
            f.write(
                f"| {r['variant']} | {r['games']} | {r['wins']} | {r['win_share']:.3f} | {ci} | {r['timeouts']} | {r['timeout_rate']:.3f} | {r['lost_first_rate']:.3f} | {r['avg_turns']:.1f} | {'✓' if r['winner'] else ''} |\n"
            )


if __name__ == "__main__":
    main()
