"""One-time preparation of the supplied market-price workbook.

Reads the raw Market 1 / Market 2 price series, checks the assumptions this
project relies on, and writes a canonical half-hourly simulation table to
data/processed/market_prices.csv. Run this once (or again if the raw
workbook is replaced); optimisation runs should load the processed file
rather than re-reading the workbook.

    python scripts/prepare_data.py
"""

from pathlib import Path

import pandas as pd

RAW_MARKET_WORKBOOK = "data/raw/Attachment 2.xlsx"
PROCESSED_MARKET_PRICES = "data/processed/market_prices.csv"

PERIOD_LENGTH = pd.Timedelta(minutes=30)


def load_market_1(path: str) -> pd.DataFrame:
    df = pd.read_excel(path, sheet_name="Half-hourly data")
    df.columns = ["source_timestamp", "market_1_price"]
    return df


def load_market_2(path: str) -> pd.DataFrame:
    df = pd.read_excel(path, sheet_name="Hourly data")
    df.columns = ["source_timestamp", "market_2_price"]
    return df


def validate(market_1: pd.DataFrame, market_2: pd.DataFrame) -> None:
    if len(market_1) == 0 or len(market_2) == 0:
        raise ValueError("Market 1 or Market 2 sheet contains no observations.")

    if len(market_1) != 2 * len(market_2):
        raise ValueError(
            f"Expected 2 Market 1 rows per Market 2 row, got "
            f"{len(market_1)} Market 1 rows and {len(market_2)} Market 2 rows."
        )

    for name, df, price_col in [
        ("Market 1", market_1, "market_1_price"),
        ("Market 2", market_2, "market_2_price"),
    ]:
        prices = pd.to_numeric(df[price_col], errors="coerce")
        if prices.isna().any():
            raise ValueError(f"{name} prices contain missing or non-numeric values.")


def build_canonical_table(market_1: pd.DataFrame, market_2: pd.DataFrame) -> pd.DataFrame:
    # The supplied clock-time timestamps are irregular around DST transitions.
    # We therefore treat the rows as an ordered sequence of settlement
    # periods and construct a new, regular simulation timeline rather than
    # trusting the source clock times. This canonical timestamp is a
    # simulation coordinate, not a reconstruction of historically correct
    # civil/DST time.
    start = market_1["source_timestamp"].iloc[0]
    canonical_timestamp = [start + i * PERIOD_LENGTH for i in range(len(market_1))]

    # Each Market 2 (hourly) price is repeated over its two corresponding
    # Market 1 (half-hourly) periods, giving every row a price for both
    # markets on a common half-hourly simulation grid.
    market_2_repeated = market_2["market_2_price"].repeat(2).reset_index(drop=True)

    return pd.DataFrame(
        {
            "period": range(len(market_1)),
            "timestamp": canonical_timestamp,
            "market_1_price": market_1["market_1_price"].values,
            "market_2_price": market_2_repeated.values,
            "source_timestamp": market_1["source_timestamp"].values,
        }
    )


def main() -> None:
    market_1 = load_market_1(RAW_MARKET_WORKBOOK)
    market_2 = load_market_2(RAW_MARKET_WORKBOOK)

    validate(market_1, market_2)

    canonical = build_canonical_table(market_1, market_2)

    Path(PROCESSED_MARKET_PRICES).parent.mkdir(parents=True, exist_ok=True)
    canonical.to_csv(PROCESSED_MARKET_PRICES, index=False)

    print(f"Wrote {len(canonical)} half-hourly periods to {PROCESSED_MARKET_PRICES}")


if __name__ == "__main__":
    main()
