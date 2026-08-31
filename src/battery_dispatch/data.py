"""Loaders for the battery specification, run configuration and prepared
market-price data.

Market-price data is read from data/processed/market_prices.csv, which is
produced once by scripts/prepare_data.py. This module does not re-read or
re-validate the original market workbook.
"""

from dataclasses import dataclass

import pandas as pd

BATTERY_SPEC_PATH = "data/raw/Attachment 1.xlsx"
RUN_CONFIG_PATH = "data/run_config.xlsx"
MARKET_PRICES_PATH = "data/processed/market_prices.csv"


@dataclass
class BatterySpec:
    max_charge_mw: float
    max_discharge_mw: float
    max_storage_mwh: float
    charging_loss_fraction: float  # Labelled "efficiency" in the supplied workbook; description defines a loss fraction.
    discharging_loss_fraction: float  # same as above
    lifetime_years: float
    lifetime_cycles: float
    degradation_pct_per_cycle: float  # Percentage loss per cycle, e.g. 0.001 means 0.001%.
    capex_gbp: float
    fixed_opex_gbp_per_year: float


def load_battery_spec(path: str = BATTERY_SPEC_PATH) -> BatterySpec:
    values = pd.read_excel(path, sheet_name="Data", index_col=0)["Values"]

    return BatterySpec(
        max_charge_mw=values["Max charging rate"],
        max_discharge_mw=values["Max discharging rate"],
        max_storage_mwh=values["Max storage volume"],
        charging_loss_fraction=values["Battery charging efficiency"],
        discharging_loss_fraction=values["Battery discharging efficiency"],
        lifetime_years=values["Lifetime (1)"],
        lifetime_cycles=values["Lifetime (2)"],
        degradation_pct_per_cycle=values["Storage volume degradation rate"],
        capex_gbp=values["Capex"],
        fixed_opex_gbp_per_year=values["Fixed Operational Costs"],
    )


@dataclass
class RunConfig:
    use_market_1_charging: bool
    use_market_2_charging: bool
    include_charging_loss: bool
    include_degradation: bool
    include_discharging_loss: bool
    use_market_1_discharging: bool
    use_market_2_discharging: bool


_RUN_CONFIG_FIELDS = {
    "Use Market 1 for charging": "use_market_1_charging",
    "Use Market 2 for charging": "use_market_2_charging",
    "Include charging loss": "include_charging_loss",
    "Include degradation": "include_degradation",
    "Include discharging loss": "include_discharging_loss",
    "Use Market 1 for discharging": "use_market_1_discharging",
    "Use Market 2 for discharging": "use_market_2_discharging",
}


def load_run_config(path: str = RUN_CONFIG_PATH) -> RunConfig:
    settings = pd.read_excel(path, header=None, index_col=0)[1]

    values = {}
    for label, field in _RUN_CONFIG_FIELDS.items():
        raw = str(settings[label]).strip()
        if raw not in ("Yes", "No"):
            raise ValueError(f"'{label}' must be 'Yes' or 'No', got {raw!r}.")
        values[field] = raw == "Yes"

    config = RunConfig(**values)

    if not (config.use_market_1_charging or config.use_market_2_charging):
        raise ValueError("At least one charging market must be enabled.")
    if not (config.use_market_1_discharging or config.use_market_2_discharging):
        raise ValueError("At least one discharging market must be enabled.")

    return config


def load_market_prices(path: str = MARKET_PRICES_PATH) -> pd.DataFrame:
    return pd.read_csv(path, parse_dates=["timestamp", "source_timestamp"])
