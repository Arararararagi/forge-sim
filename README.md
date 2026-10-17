# forge-sim

Python harness for driving Forge's headless `sim` mode: run hundreds/thousands
of commander games across deck variants and aggregate win rates.

## Canonical runtime

The verified triple (JAR + cwd + base deck) is recorded in `runtime_env.py`:

| Role          | Value |
|---------------|-------|
| Harness JAR   | `/Users/teclas/Programming/forge/forge-gui-desktop/target/forge-gui-desktop-2.0.15-SNAPSHOT-jar-with-dependencies.jar` |
| cwd           | `/Users/teclas/Programming/forge/forge-gui-desktop/target/jpackage-input` (must contain `res/`) |
| Base deck     | `/Users/teclas/Programming/forge/sim-results/foundations-commander/decks/Keen_Engineering.dck` (100-card, Sai) |

The JAR must be launched with the cwd above so it finds `res/`.

## Manual smoke (one game)

```bash
cd /Users/teclas/Programming/forge/forge-gui-desktop/target/jpackage-input
java -Xmx2048m -Dfile.encoding=UTF-8 \
  -jar /Users/teclas/Programming/forge/forge-gui-desktop/target/forge-gui-desktop-2.0.15-SNAPSHOT-jar-with-dependencies.jar \
  sim -f commander \
  -d /Users/teclas/Programming/forge/sim-results/foundations-commander/decks/Keen_Engineering.dck \
  -a Default Default Default Default \
  -n 1 -s 42 -A 2 -c 300 -o /tmp/manual_test.jsonl -B 40000

python3 /Users/teclas/Programming/forge-sim/validate_jsonl.py /tmp/manual_test.jsonl
```

`-B` is the sim budget; `-o` is the JSONL sink (one row per finished game).

## Base deck & variants

`keen_variants.yaml` pins `base:` to the canonical deck and lists `swaps:`.
Each swap is materialized into a variant deck under
`<data-dir>/variants/keen__add_<A>__cut_<C>.dck` (spaces/commas stripped).

Two replacement forms (card names may contain commas — never split on them):

```yaml
base: /Users/teclas/Programming/forge/sim-results/foundations-commander/decks/Keen_Engineering.dck
swaps:
  # single-card replacement
  - add: Vnwxt, Verbose Host
    cut: Sai, Master Thopterist
  # multiple replacements in one deck
  - id: multi_add_Ponder_Preordain
    changes:
      - add: Ponder
        cut: Thought Monitor
      - add: Preordain
        cut: Thoughtcast
```

`id:` is optional — editor-generated swap ids are derived from add/cut.
Every variant deck (plus `baseline`) is recorded in `<data-dir>/manifest.json`,
including variants that failed to materialize (`skipped`).

## Opponent pool

The pool is just a directory of valid singleton commander `.dck` files. It is
built (and validated against the card DB) automatically at the start of every
run by `opponents.build_pool`; a pod of 3 opponents is sampled per rep, so the
pool needs **at least 3 valid decks**.

- Default pool dir: `/Users/teclas/Programming/forge/sim-results/foundations-commander/decks/`
- To use your own, drop `.dck` files in a directory and pass `--pool-dir <dir>`.
- Invalid decks (bad commander count, duplicates, cards missing from
  `--cardsfolder`) are skipped with a log line.

## Run a campaign (quick / medium / large)

```bash
cd /Users/teclas/Programming/forge-sim
python3 auto_variants.py --preset quick    # smoke: baseline only, 1 game, 40k sim budget
python3 auto_variants.py --preset medium   # 5 variants x 3 reps
python3 auto_variants.py --preset large    # all variants x 10 reps
```

`auto_variants.py` fills in the canonical JAR/cwd/cardsfolder and
`keen_variants.yaml`; output lands in `data/auto_<preset>/` (per-unit JSONL +
`manifest.json`). Any `run_variants.py` knob/path can be overridden, e.g.:

```bash
python3 auto_variants.py --preset medium --reps 5 --workers 8 \
  --max-variants 3 --data-dir data/my_campaign
```

Note: `quick` is baseline-only by design — pass `--max-variants N` to include
variants in a quick run. Manifest counters (`completed/timeout/error/games`)
and per-variant JSONL (0 bytes ⇒ deck-load failure, counted as `error`) live
in `--data-dir`.

## Report

Aggregate every `.jsonl` in a campaign by canonical variant:

```bash
python3 report.py --data-dir data/auto_quick --out-csv report.csv --out-md report.md
```

Columns: `variant, games, wins, win_share, ci_lo, ci_hi (Wilson 95%),
timeouts, timeout_rate, lost_first_rate, avg_turns, winner`. Variants
declared in `manifest.json` (materialized or skipped) always appear, even
with 0 games.

## Iterating (sub-branches)

After a campaign with games, promote winners to a next-generation spec:
variants whose Wilson lower bound beats the baseline share become
"graduates", and for each graduate one new add/cut (EDHREC-informed combo
card) is stacked on top of its parent's changes:

```bash
python3 generate_subbranches.py --iter1-data-dir data/iter1
# -> data/iter2/keen_variants.yaml; run it as the next campaign:
python3 auto_variants.py --preset medium \
  --variants data/iter2/keen_variants.yaml --data-dir data/iter2
```

## Tests

```bash
python3 -m unittest discover . -p "test_*.py"
```

Stdlib only, no network required (EDHREC fetch falls back to an offline
combo list).
