# Forge-sim harness automation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the forge-sim harness runnable end-to-end with the correct Forge JAR/cwd/deck and a stable path for running thousands of games/variations.

**Architecture:** Fix the runtime configuration (JAR/cwd/base deck), correct relative paths in the workspace (yaml/spec), and ensure the Python CLI (`run_variants.py`) can drive the headless `sim` mode to produce valid JSONL consumed by `report.py`. Prefer explicit absolute paths or safe repo-relative ones that work from the workspace. Keep changes minimal and testable.

**Tech Stack:** Python 3 (stdlib only: os, sys, json, random, subprocess, argparse, concurrent.futures, threading), Forge headless CLI (`java -jar ... sim ...`).

**Spec:** This plan implements the user request "how can I use the python harness to automate thousands of games and variations?" based on the current workspace state and prior debug.

## Global Constraints
- Use the **repo-built harness JAR**: `/Users/teclas/Programming/forge/forge-gui-desktop/target/forge-gui-desktop-2.0.15-SNAPSHOT-jar-with-dependencies.jar` (has `-o` JSONL sink, `-B` sim budget, direct deck paths).
- Run the JAR with **cwd** = `/Users/teclas/Programming/forge/forge-gui-desktop/target/jpackage-input/` (contains `res/`).
- Base deck: `/Users/teclas/Programming/forge/sim-results/foundations-commander/decks/Keen_Engineering.dck` (100-card, Sai, Master Thopterist).
- Keep changes to `forge-sim/` only. Do not modify the forge repo unless necessary.
- Python standard library only.
- TDD where sensible.

## Review Focus
- **Deck loading with absolute paths:** `-d <absolute-path.dck>` must work with repo JAR (no "No deck found"/"Could not load deck").
- **JSONL output integrity:** `-o <path>.jsonl` must contain one line per finished game with required keys (`seed,game,ms,turns,winner,winnerSeat,decks,profiles,finalLife,commanderTurns,hands,eliminationOrder`).
- **Path robustness:** `keen_variants.yaml` base and relative resolution must work from `forge-sim/`.
- **Determinism/budgets:** `-B <sim_budget>` and `-A <ai_timeout>` honored; seeds propagate correctly.
- **Resource hygiene:** worker homes isolated per unit; no overwrite of JSONL across reps.

---

### Task 1: Verify runtime env (JAR + cwd + deck)

**Files:**
- Read: repo harness JAR, `jpackage-input/res/`, canonical deck

**Interfaces:**
- Consumes: none
- Produces: documented canonical triple (jar, cwd, base_deck)

- [ ] **Step 1: Sanity check files exist**
- [ ] **Step 2: Smoke test one game**
  ```bash
  cd /Users/teclas/Programming/forge/forge-gui-desktop/target/jpackage-input
  java -Xmx2048m -Dfile.encoding=UTF-8 \
    -jar /Users/teclas/Programming/forge/forge-gui-desktop/target/forge-gui-desktop-2.0.15-SNAPSHOT-jar-with-dependencies.jar \
    sim -f commander \
    -d /Users/teclas/Programming/forge/sim-results/foundations-commander/decks/Keen_Engineering.dck \
    /tmp/opp_clean.dck /tmp/opp_clean.dck /tmp/opp_clean.dck \
    -a Default Default Default Default -n 1 -s 42 -A 2 -c 300 -o /tmp/manual_test.jsonl -B 40000
  ```
- [ ] **Step 3: Validate JSONL schema**
- [ ] **Step 4: Record canonical triple**

---

### Task 2: Fix `forge-sim/keen_variants.yaml` base path

**Files:**
- Modify: `/Users/teclas/Programming/forge-sim/keen_variants.yaml`

- [ ] **Step 1: Write failing check (load variants)**
- [ ] **Step 2: Fix base path to canonical deck**
- [ ] **Step 3: Verify load passes**
- [ ] **Step 4: Sanity check loaded variants**

---

### Task 3: Make `run_variants.py` runtime-safe (jar/cwd + defaults)

**Files:**
- Modify: `/Users/teclas/Programming/forge-sim/run_variants.py`

- [ ] **Step 1: Identify defaults needed**
- [ ] **Step 2: Set safe defaults (jar, cwd, java, cardsfolder)**
- [ ] **Step 3: Tiny end-to-end run (baseline only, reps=1,games=1,workers=1,max-variants=0)**
- [ ] **Step 4: Validate artifacts**

---

### Task 4: Make `report.py` and `run_variants.py` aggregate per-unit JSONL correctly

**Files:**
- Modify: `/Users/teclas/Programming/forge-sim/run_variants.py`
- Modify: `/Users/teclas/Programming/forge-sim/report.py`

- [ ] **Step 1: Understand overwrite issue (same file per rep)**
- [ ] **Step 2: Write per-unit JSONL (include rep/seed) in run_variants**
- [ ] **Step 3: Make report.py aggregate by canonical variant across all .jsonl files**
- [ ] **Step 4: End-to-end test with reps>1, max-variants>=1**

---

### Task 5: Add `auto_variants.py` helper with presets

**Files:**
- Create: `/Users/teclas/Programming/forge-sim/auto_variants.py`

- [ ] **Step 1: Define presets (quick/medium/large)**
- [ ] **Step 2: Wrap run_variants (call or import main)**
- [ ] **Step 3: Smoke test preset quick**

---

### Task 6: Add README quickstart

**Files:**
- Create/Modify: `/Users/teclas/Programming/forge-sim/README.md`

- [ ] **Step 1: Write minimal quickstart with canonical paths**
- [ ] **Step 2: Verify commands copy-paste**

## Validation checklist (final)
- [ ] Task 1 smoke passes
- [ ] Task 2: variants load
- [ ] Task 3: tiny campaign produces valid JSONL+manifest
- [ ] Task 4: report aggregates correctly
- [ ] Task 5: preset runs end-to-end
- [ ] Task 6: README correct

**Plan complete and saved to `forge-sim/docs/plans/2025-10-17-automate-thousands-of-games.md`. Please review the plan. Does it capture what you want?**
