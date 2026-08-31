# Battery Market Dispatch

Models a price-taking battery charging and discharging against two
wholesale electricity markets with different commitment intervals, to
maximise trading profit subject to the battery's physical, cycling and
degradation constraints.

## Setup

```bash
python -m venv .venv
.venv/Scripts/activate   # or source .venv/bin/activate on macOS/Linux
pip install -e .
```

To also run the test suite, install the `test` extra instead:

```bash
pip install -e ".[test]"
```

## Prepare data

The supplied battery specification and market-price workbooks are already
expected at `data/raw/Attachment 1.xlsx` and `data/raw/Attachment 2.xlsx`.
Run the one-time preparation step once, before the first run:

```bash
python scripts/prepare_data.py
```

This validates the raw market data and writes the reusable canonical
half-hour price table to `data/processed/market_prices.csv`.

## Run

```bash
python -m battery_dispatch.run
```

This loads the battery spec, the scenario switches in `data/run_config.xlsx`,
and the prepared price data; runs the full rolling-horizon simulation over
the entire ~3-year dataset; and writes a timestamped results workbook to
`outputs/optimisation_YYYYMMDD_HHMMSS.xlsx` (previous runs are never
overwritten). Solving the full dataset takes several minutes (roughly 6-7
on a typical laptop), since it repeatedly re-solves a MILP as it rolls
forward.

## Test

```bash
pytest
```

The suite is a small set of focused checks against the optimisation and
rolling-simulation logic (not the full multi-minute dataset run).

## Expected default result

With `run_config.xlsx` left at its all-`Yes` defaults, the verified
reference result for the complete supplied dataset is approximately:

```text
52,608 executed half-hour periods
Total trading profit:     £208,430.46
Cumulative EFC:            3,468.0
Final SoC:                  0.0 MWh
Final usable capacity:      3.86128 MWh
```

## Model notes

See [MODEL_NOTES.md](MODEL_NOTES.md) for the modelling decisions,
conventions and known limitations.

## Approach summary

The model represents both markets on a common half-hour simulation grid
and jointly optimises charging/discharging across them with a MILP,
while preserving Market 2's one-hour commitment requirement, the shared
physical power limit, no-simultaneous-charge/discharge, and battery
losses, state of charge and cycle-count limits. Because solving the whole
dataset at once is impractical, the full simulation is built from a
rolling 48-hour look-ahead / 24-hour executed-dispatch scheme, so each
day's decisions have visibility just beyond the following midnight
without ever committing to more than a day at a time; only the executed
portion of each solve updates the battery's carried state of charge,
cycle usage and degraded capacity. The result is a reproducible half-hour
dispatch schedule together with summary profit, energy and
cycle/degradation metrics.
