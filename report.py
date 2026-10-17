import os
import sys
import json
import math
import argparse
import csv

def wilson_interval(wins: int, n: int, z: float = 1.96) -> tuple:
    if n == 0:
        return (0.0, 0.0)
    p = wins / n
    denominator = 1 + z**2 / n
    centre_adjusted_probability = p + z**2 / (2 * n)
    adjusted_standard_deviation = math.sqrt((p * (1 - p) + z**2 / (4 * n)) / n)
    lower_bound = (centre_adjusted_probability - z * adjusted_standard_deviation) / denominator
    upper_bound = (centre_adjusted_probability + z * adjusted_standard_deviation) / denominator
    return (max(0.0, lower_bound), min(1.0, upper_bound))

def focal_outcomes(rows: list) -> list:
    outcomes = []
    for r in rows:
        if r.get("timeout") or r.get("winnerSeat", -1) == -1:
            continue # Exclude draws/timeouts from win share
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
        else:
            elim = r.get("eliminationOrder", [])
            if 0 in elim:
                idx = elim.index(0)
                if idx == 0:
                    bd["lost_first"] += 1
                elif idx == len(elim) - 1:
                    bd["lost_last"] += 1
                else:
                    bd["lost_mid"] += 1
    return bd

def compare_variants(data_dir: str):
    results = []
    baseline_share = 0.0
    
    for fname in os.listdir(data_dir):
        if not fname.endswith(".jsonl"):
            continue
        variant_id = fname.replace(".jsonl", "")
        fpath = os.path.join(data_dir, fname)
        
        rows = []
        with open(fpath, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    rows.append(json.loads(line))
                    
        outcomes = focal_outcomes(rows)
        wins = sum(outcomes)
        n = len(outcomes)
        share = wins / n if n > 0 else 0.0
        ci_lo, ci_hi = wilson_interval(wins, n)
        
        timeouts = sum(1 for r in rows if r.get("timeout"))
        total_games = len(rows)
        
        bd = failure_breakdown(rows)
        lost_first_rate = bd["lost_first"] / total_games if total_games > 0 else 0.0
        
        turns = [r.get("turns", 0) for r in rows if r.get("turns")]
        avg_turns = sum(turns) / len(turns) if turns else 0.0
        
        results.append({
            "variant": variant_id,
            "games": total_games,
            "wins": wins,
            "completed": n,
            "share": share,
            "ci_lo": ci_lo,
            "ci_hi": ci_hi,
            "timeouts": timeouts,
            "timeout_rate": timeouts / total_games if total_games > 0 else 0.0,
            "avg_turns": avg_turns,
            "lost_first_rate": lost_first_rate
        })
        
        if variant_id == "baseline":
            baseline_share = share
            
    # Sort by ci_lo descending to find statistically significant winners
    results.sort(key=lambda x: x["ci_lo"], reverse=True)
    
    # Write CSV
    csv_path = os.path.join(data_dir, "matrix-by-variant.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["variant", "games", "completed", "wins", "share", "ci_lo", "ci_hi", "timeout_rate", "avg_turns", "lost_first_rate"])
        writer.writeheader()
        writer.writerows(results)
        
    # Write Markdown Report
    md_path = os.path.join(data_dir, "REPORT.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# Keen Engineering Variant Report\n\n")
        f.write("## Variant Comparison (Focal in Seat 0)\n\n")
        f.write("| Variant | Games | Completed | Win Share | 95% CI | Timeout Rate | Avg Turns | Lost First Rate |\n")
        f.write("|---|---|---|---|---|---|---|---|\n")
        for r in results:
            ci_str = f"[{r['ci_lo']:.3f}, {r['ci_hi']:.3f}]"
            f.write(f"| {r['variant']} | {r['games']} | {r['completed']} | {r['share']:.3f} | {ci_str} | {r['timeout_rate']:.3f} | {r['avg_turns']:.1f} | {r['lost_first_rate']:.3f} |\n")
            
        f.write("\n## Graduation Criteria\n")
        f.write(f"A variant is considered **successful** if:\n")
        f.write(f"1. `completed` games >= 50\n")
        f.write(f"2. `timeout_rate` <= baseline timeout rate + 0.05\n")
        f.write(f"3. `ci_lo` > baseline win share ({baseline_share:.3f})\n\n")
        
        f.write("### Graduated Variants\n")
        graduated = []
        for r in results:
            if r["variant"] == "baseline":
                continue
            if r["completed"] >= 50 and r["ci_lo"] > baseline_share and r["timeout_rate"] <= (next((x["timeout_rate"] for x in results if x["variant"]=="baseline"), 0.0) + 0.05):
                graduated.append(r)
                f.write(f"- **{r['variant']}** (Win Share: {r['share']:.3f}, CI: {ci_str})\n")
                
        if not graduated:
            f.write("- *None met all statistical graduation criteria in this iteration.*\n")
            
    print(f"Report generated: {md_path}")
    print(f"Data matrix: {csv_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--variants-data-dir", required=True)
    args = parser.parse_args()
    compare_variants(args.variants_data_dir)