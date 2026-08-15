# Pokémon TCG AI Battle Challenge — Strategy Track Submission

**Report:** [`FINAL_REPORT.md`](FINAL_REPORT.md) — read this first.

## What's in this submission

```
FINAL_REPORT.md          The strategy report (start here)
requirements.txt         Pinned Python dependencies
notebooks/
  01_card_data_eda.ipynb   Exploratory analysis of the full card pool
src/
  data_loader.py            Loads and parses the competition's card dataset
  decks.py                   Builds the two candidate 60-card decklists from real card data
  simulator.py                The battle engine: typed energy, named Trainer effects,
                               move-specific side effects, weakness/resistance, prize rules
  run_simulations.py          Runs a batch of simulated games and reports win rates
  test_simulator.py           20 automated tests covering game-rule correctness
```

## Reproducing the results

This project uses the Kaggle competition's own card dataset
(`EN_Card_Data.csv`), which is not redistributed here per the
competition's data-use terms. Download it first:

```powershell
python -m pip install kaggle
python -m kaggle competitions download pokemon-tcg-ai-battle-challenge-strategy -f "EN_Card_Data.csv" -p data/raw
```

(Requires a Kaggle API token — see
https://www.kaggle.com/docs/api#authentication)

Then, from this folder:

```powershell
python -m pip install -r requirements.txt
python -m pytest src/test_simulator.py -v      # 20 tests, verifies game-rule correctness
python src/run_simulations.py 1000             # reproduces the headline 85.9% result
```

`notebooks/01_card_data_eda.ipynb` reproduces the card-pool analysis in
Section 2 of the report and requires the same `data/raw/EN_Card_Data.csv`
file, plus `jupyter` (included in `requirements.txt`).

## Summary of findings

A Dragon-type control deck (Dreepy → Drakloak → Dragapult ex, plus Mega
Latias ex) won 85.9% of 1,000 simulated games against a Fire-type
aggro alternative (Charcadet → Ceruledge, plus Sizzlipede →
Centiskorch). Full reasoning, deck lists, and the simulation
methodology are in `FINAL_REPORT.md`.
