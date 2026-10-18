# forge-sim

Python harness for driving Forge's headless `sim` mode: materialize deck
variants of a commander precon, run hundreds to thousands of commander games
against an opponent pool, aggregate win rates by variant, and iterate —
promoting measured winners into sub-branch variants.

All commands below are run from the repo root (`forge-sim/`). Stdlib Python
only (no pip installs); network is optional (EDHREC fetches fall back to a
curated offline combo list).

## Canonical runtime

The verified triple (JAR + JVM cwd + base deck) is recorded in
`runtime_env.py` and used as the default for every script:

| Role        | Value |
|-------------|-------|
| Harness JAR | `/Users/teclas/Programming/forge/forge-gui-desktop/target/forge-gui-desktop-2.0.15-SNAPSHOT-jar-with-dependencies.jar` |
| JVM cwd     | `/Users/teclas/Programming/forge/forge-gui-desktop/target/jpackage-input` (must contain `res/`) |
| Base deck   | `/Users/teclas/Programming/forge/sim-results/foundations-commander/decks/Keen_Engineering.dck` (100-card mono-U artifacts, `Sai, Master Thopterist`) |
| Cardsfolder | `/Users/teclas/Programming/forge/forge-gui/res/cardsfolder` |

Important runtime facts baked into the harness:

- Forge returns exit code 0 even when a deck fails to load; the only
  reliable signal is whether the `-o` JSONL got any rows. A 0-byte JSONL is
  therefore counted as an **error** unit, never as games.
- Forge runs with a *different cwd* than the launching shell. Every path
  handed to the java process (focal deck, opponent decks, JSONL sink, worker
  home) is absolutized by the runner, so relative `--data-dir` / `--pool-dir`
  paths are safe from any directory.
- Variant deck filenames are sanitized to `[A-Za-z0-9_-]` (apostrophes,
  commas, spaces stripped), e.g. `Raff Capashen, Ship's Mage` →
  `Raff_Capashen_Ships_Mage`. Spec ids stay human-readable.

## The workflow (end to end)

```text
                       ┌────────────────────────────────────────────────┐
 1. sanity             │ unittest suite, quick smoke (baseline, 1 game) │
 2. generate variants  │ generate_candidates.py -> keen_variants.yaml   │
 3. smoke any variant  │ quick preset with --max-variants               │
 4. campaign           │ auto_variants.py (medium / large)              │
 5. aggregate          │ report.py -> matrix-by-variant.csv + REPORT.md │
 6. iterate            │ generate_subbranches.py -> gen2 spec, goto 4   │
                       └────────────────────────────────────────────────┘
```

### 1. Sanity checks

```bash
python3 -m unittest discover . -p "test_*.py"        # full suite (~30 s)
python3 auto_variants.py --preset quick --data-dir /tmp/smoke   # baseline-only smoke
python3 validate_jsonl.py /tmp/smoke/baseline_r0_s4242.jsonl
```

Expect the smoke run to print `[Done] ... Errors: 0` and the JSONL to
validate OK.

### 2. Generate variants (or skip: a working `keen_variants.yaml` ships)

```bash
python3 generate_candidates.py --out keen_variants.yaml --max-variants 50
```

Three layers of variants, all written uniformly as `changes:` lists:

| Layer | Default count | Content |
|-------|---------------|---------|
| `deep_`  | 8 | Commander swap (`Sai` → alternative legendary creature, CMC ≤ 4) **plus** 2 identity-appropriate add/cut package pairs. commanders are picked round-robin across identities (ub/ur/uw/ug first), so a Dimir commander ships with Dimir tools, an Izzet one with Izzet tools, etc. New colors are fixed automatically at materialization (6 Islands → corresponding dual lands). |
| `theme_` | up to 3 | One standalone package-card swap with `Sai` still commanding — measures a package's contribution independently of the commander swap (generation-time sub-branches). |
| `payoff_` | up to 3 | `Sai` still commanding; adds the top-ranked **payoff** card(s) — cards whose text scales with the board (anthems/broad pumps/presence-damage triggers detected generically, no hard-coded names). There is no CMC cap: relevance is enforced by monotonic scoring (cost penalty + payoff/engine/combo bonuses), so a 5-CMC `Coat of Arms`-class card ranks on merit, and a big do-nothing spell sinks out. v1 = top payoff alone, v2/v3 add the next-ranked ones. |
| `add_`   | rest of budget | Plain single swaps: highest-synergy adds over the weakest-synergy cuts. |

Generation-time guarantees:

- **Per-identity combo routing**: EDHREC combos are fetched separately for
  mono-blue/dimir/izzet/azorius/simic, and each commander's package is scored
  against its own identity's set (identity-specific pieces outrank generic
  mono-U ones). Offline fallback lists exist for every identity and are
  verified against the cardsfolder.
- **Materialization smoke**: every variant is built with
  `deck_variant.make_variant` into a temp dir before the spec is written;
  unbuildable variants are dropped with a printed reason. A generated spec
  therefore never contains variants that skip at run time.
- **Change-set dedupe**: two variants resolving to the identical deck are
  collapsed (first wins) so ids/files/game-slots are never wasted.

Flags: `--max-variants N` (total budget, default 50), `--commanders N`
(deep variants, default 8), `--package-cards K` (adds per deep variant,
default 2), `--payoff-themes N` (payoff showcases, default 3, `0` disables),
`--seed`. Card names may contain commas — never split on them.

### 3. Smoke variants before a long campaign

`quick` is baseline-only by design (`max_variants: 0`); pass
`--max-variants N` to sample N variants with a bounded sim budget:

```bash
python3 auto_variants.py --preset quick --max-variants 6 --data-dir /tmp/smoke6
```

### 4. Run a campaign

```bash
python3 auto_variants.py --preset medium   # 5 variants x 3 reps
python3 auto_variants.py --preset large    # ALL variants x 10 reps
```

| Preset | reps | games/unit | workers | max variants | sim budget | clock | notes |
|--------|------|-----------|---------|--------------|------------|-------|-------|
| `quick`  | 1  | 1 | 1 | 0 (baseline-only) | 40000 | 300 s | hard-bounded smoke |
| `medium` | 3  | 1 | 4 | 5 | unbounded | ∞ | first comparison |
| `large`  | 10 | 1 | 6 | all | unbounded | ∞ | full sweep, hours |

`medium`/`large` games have an effectively unlimited wall clock (~63-year
Forge sim timeout): **games play out to a real winner** — no 5-minute draw
cap. True runaway loops are still bounded by Forge's per-decision
`SIM_BUDGET`/`AI_TIMEOUT` and rule-726 loop handling; the runner keeps a
24 h anti-deadlock kill per unit (`--clock` can override at any tier).

Any knob/path can be overridden:

```bash
python3 auto_variants.py --preset large --workers 8 --reps 5 \
  --variants keen_variants.yaml --data-dir data/gen1
```

Relative or absolute `--data-dir` both work. For long runs use `nohup`:

```bash
nohup python3 auto_variants.py --preset large --data-dir data/gen1 > gen1.log 2>&1 &
tail -f gen1.log     # [Done] line terminates
```

Rough throughput: ~1 game per minute per worker (each unit is one JVM running
one game of ~2-4 min). `--preset large` (50 variants × 10 reps ≈ 500 games)
≈ 1.5-3 h at 6 workers. Watch system RAM when raising `--workers` (each JVM
`-Xmx2048m`, so 6 workers ≈ 12 GB).

### Campaign output layout

```text
<data-dir>/
  keen__add_<A>__cut_<C>.dck  variants/     # materialized variant decks
  <variant_id>_r<rep>_s<seed>.jsonl         # one file per unit, 1 row per game
  worker_<variant_id>_r<rep>_s<seed>/       # per-unit Forge user.home
  manifest.json
  matrix-by-variant.csv   REPORT.md         # after report.py
```

`manifest.json` records every variant declared by the spec (materialized or
skipped with reason), the deck path each variant id maps to, and counters
(`completed`, `timeout`, `error`, `games`).

### 5. Aggregate / report

```bash
python3 report.py --data-dir data/gen1 \
  --out-csv data/gen1/matrix-by-variant.csv --out-md data/gen1/REPORT.md
```

CSV columns: `variant, games, wins, win_share, decided, decided_win_share,
draws, draw_rate, ci_lo, ci_hi (Wilson 95%), timeouts, timeout_rate,
lost_first_rate, avg_turns, winner`. Semantics:

- `win_share` = wins over **all games** — draws count as non-wins, so a
  stall deck that wins 1 and draws 7 of 8 games shows 0.125, not 1.0.
- `decided_win_share` / `draw_rate` keep the old decided-only view
  (informational; diagnostics for sizing the next campaign's `--reps`).
- `winner` is the promotion flag for the sub-branch loop: games ≥
  `--min-games` (default 3), `wins ≥ 1`, Wilson `ci_lo` above the
  baseline share, and timeout rate within 0.05 of the baseline's.

Variants declared in `manifest.json` always appear — even with 0 games —
so nothing ever silently disappears.

### 6. Iterate (sub-branches)

Variants whose Wilson lower bound beats the baseline share (and enough
recorded games) become *graduates*; each graduate gets one new EDHREC-informed add/cut
**stacked on top of its parent's changes**, producing the next-generation
spec:

```bash
python3 generate_subbranches.py --iter1-data-dir data/gen1
# -> data/gen2/keen_variants.yaml  (sibling dir named iterN)
python3 auto_variants.py --preset medium \
  --variants data/gen2/keen_variants.yaml --data-dir data/gen2
```

Flags: `--max-subbranches` (default 10), `--min-games` (default 20),
`--spec` (defaults to the repo `keen_variants.yaml`), `--cardsfolder`.

## Variants spec format

```yaml
base: /Users/teclas/Programming/forge/sim-results/foundations-commander/decks/Keen_Engineering.dck
swaps:
  # uniform form (generator output): every entry is a changes: list
  - id: deep_add_Rona_Disciple_of_Gix
    changes:
      - add: Rona, Disciple of Gix        # commander swap first
        cut: Sai, Master Thopterist
      - add: Time Sieve                   # package cards follow
        cut: Aetherize
      - add: Sacrifice
        cut: Fall from Favor
  # compact single-swap form is also accepted
  - add: Vnwxt, Verbose Host
    cut: Sai, Master Thopterist
```

- `id:` optional (auto-derived, must be unique).
- Commas are part of card names — never split on commas.
- Cuts may be the commander (`Sai` swap) or any 1-copy main-deck card; land
  cuts are never auto-selected.
- Missing colors are auto-fixed at materialization: the 6 most-Island copies
  become 6 curated dual lands (`FIX_LANDS` covers W/R/B/G in
  `deck_variant.py`).

## Opponent pool

Default pool: `/Users/teclas/Library/Application Support/Forge/decks/net/EDHREC Average Decks`
(~6.6k EDHREC Average commander decks; ~6.65k validate, ~25 s validation at
campaign start). Pods of 3 random opponents are sampled per rep from the full
pool. Validate any candidate pool with:

```python
import opponents
pool = opponents.build_pool(['/path/to/pool'], cardsfolder, exclude_names=['Keen Engineering'])
```

A pod of 3 opponents is sampled per rep; the pool needs **≥ 3 valid decks**.
Invalid decks (bad commander count, duplicates, cards missing from the card
DB) are skipped with a log line. Avoid single-deck pools (e.g. 4 copies of
one deck): identical pods made every focal deck lose 6/6 in testing —
pod diversity matters as much as variant quality.

## Diagnostics

| Symptom | Meaning / fix |
|---------|---------------|
| `[Error] <variant> produced no games (deck load failed?)` with 0-byte JSONL | Forge could not load the deck. Historically: stock JAR without `-o`/`-B`/absolute-deck support (use the SNAPSHOT JAR above — do **not** modify Forge) or pre-fix relative paths (now fixed). Campaign keeps going; counters record the error. |
| `[Skipping <id>: ...]` at materialization | Deck could not be built (add missing from DB, cut not found, unfixable color). Skipped variants are still listed in the manifest and report with 0 games. |
| Timeout counters > 0 | Runner-level kill fired (anti-deadlock insurance at 24 h, or `clock + 60 s` when a small explicit clock is set). Rare under the unlimited-by-default clock; the JSONL still holds any completed games. |
| All variants 0% vs strong baseline | Check pod diversity (above). Focal is always seat 0. |

## Manual single game (no harness)

```bash
cd /Users/teclas/Programming/forge/forge-gui-desktop/target/jpackage-input
java -Xmx2048m -Dfile.encoding=UTF-8 \
  -jar /Users/teclas/Programming/forge/forge-gui-desktop/target/forge-gui-desktop-2.0.15-SNAPSHOT-jar-with-dependencies.jar \
  sim -f commander \
  -d /Users/teclas/Programming/forge/sim-results/foundations-commander/decks/Keen_Engineering.dck \
  -a Default Default Default Default \
  -n 1 -s 42 -A 2 -c 300 -o /tmp/manual_test.jsonl -B 40000

python3 validate_jsonl.py /tmp/manual_test.jsonl
```

`-o` JSONL sink (one row per finished game), `-B` sim budget, `-A` AI
timeout, `-c` clock, `-n` games. Forge exit code 0 does *not* mean the game
ran — check the JSONL.

## Scripts

| Script | Role |
|--------|------|
| `runtime_env.py` | canonical JAR/cwd/base-deck constants |
| `auto_variants.py` | preset wrapper + overrides around `run_variants.main` |
| `run_variants.py` | campaign driver: pool → spec → materialize → units → manifest |
| `deck_variant.py` | deck parser/swapper/mana-fixer (`make_variant`, `FIX_LANDS`) |
| `opponents.py` | spec loader (`changes:` lists), pool builder, pod sampler |
| `generate_candidates.py` | variant generation (deep/theme/single layers) |
| `generate_subbranches.py` | winner-driven next-generation EDHREC-informed specs |
| `report.py` | aggregate JSONLs → CSV + Markdown |
| `validate_jsonl.py` | row/schema check for a single JSONL file |

## Tests

```bash
python3 -m unittest discover . -p "test_*.py"
```

Hermetic by construction: EDHREC fetches are mocked in tests; a couple of
materialization tests scan the real cardsfolder, so the suite takes ~1 min.
