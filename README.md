# Battery Market Dispatch

Models a battery charging and discharging against two wholesale electricity
markets to maximise profit, subject to the battery's physical constraints.

## Setup

```bash
python -m venv .venv
.venv/Scripts/activate   # or source .venv/bin/activate on macOS/Linux
pip install -e .
```

## Usage

Prepare the market-price dataset once:

```bash
python scripts/prepare_data.py
```

This is being developed incrementally; optimisation and reporting are not
implemented yet.
