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

`keen_variants.yaml` pins `base:` to the canonical deck and lists `swaps:`
(`add`/`cut` pairs). During a run each swap is materialized into a variant deck
under `<data-dir>/variants/<id>.dck`.

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
python3 auto_variants.py --preset quick    # 1 baseline game, 40k sim budget (smoke)
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

## Report

Aggregate every `.jsonl` in a campaign by canonical variant:

```bash
python3 report.py --data-dir data/auto_quick --out-csv report.csv --out-md report.md
```
