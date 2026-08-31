"""Excel workbook reporting for a completed battery dispatch simulation.

Writes one timestamped workbook per run (never overwriting a previous
run) containing a Summary, Inputs, Dispatch and Period Summary sheet,
computed from the executed dispatch returned by run_simulation().
"""

from datetime import datetime
from pathlib import Path

import pandas as pd

from battery_dispatch.data import BatterySpec, RunConfig, _RUN_CONFIG_FIELDS
from battery_dispatch.model import DELTA_T_HOURS
from battery_dispatch.simulation import SimulationResult

OUTPUT_DIR = "outputs"
CHARGING_POWER_TOLERANCE_MW = 1e-6

# The rolling simulation always starts the battery new and empty (see
# simulation.py); this is a fixed modelling assumption, not a computed
# result, so it is recorded here rather than read back from the dispatch.
INITIAL_SOC_MWH = 0.0


def _energy_mwh(power_mw: pd.Series) -> float:
    return float((power_mw * DELTA_T_HOURS).sum())


def observed_years(dispatch: pd.DataFrame) -> float:
    """Duration spanned by the dispatch, in years.

    Complete calendar years (e.g. the supplied 2018-01-01 to 2021-01-01
    dataset) are counted exactly as whole years, so a leap year inside the
    span does not turn "3 complete years" into 3.00068 years. Any other
    (e.g. partial-year) span falls back to a simple day-count
    approximation.
    """
    start = dispatch["timestamp"].iloc[0]
    end_exclusive = dispatch["timestamp"].iloc[-1] + pd.Timedelta(hours=DELTA_T_HOURS)

    def _is_year_boundary(ts: pd.Timestamp) -> bool:
        return (ts.month, ts.day, ts.hour, ts.minute, ts.second) == (1, 1, 0, 0, 0)

    if _is_year_boundary(start) and _is_year_boundary(end_exclusive) and end_exclusive.year > start.year:
        return float(end_exclusive.year - start.year)

    return (end_exclusive - start).total_seconds() / (365.25 * 24 * 3600)


def _summary_frame(dispatch: pd.DataFrame, result: SimulationResult, battery: BatterySpec) -> pd.DataFrame:
    charging_expenditure = float(
        (dispatch["market_1_price"] * dispatch["charge_m1_mw"] * DELTA_T_HOURS)
        .add(dispatch["market_2_price"] * dispatch["charge_m2_mw"] * DELTA_T_HOURS)
        .sum()
    )
    discharge_revenue = float(
        (dispatch["market_1_price"] * dispatch["discharge_m1_mw"] * DELTA_T_HOURS)
        .add(dispatch["market_2_price"] * dispatch["discharge_m2_mw"] * DELTA_T_HOURS)
        .sum()
    )

    m1_charge_energy = _energy_mwh(dispatch["charge_m1_mw"])
    m2_charge_energy = _energy_mwh(dispatch["charge_m2_mw"])
    m1_discharge_energy = _energy_mwh(dispatch["discharge_m1_mw"])
    m2_discharge_energy = _energy_mwh(dispatch["discharge_m2_mw"])

    # Market-specific: a period only counts if the battery actually charged
    # from a market that was negatively priced, not merely because some
    # other, unused market happened to be negative at the same time.
    m1_charging_negative = (dispatch["charge_m1_mw"] > CHARGING_POWER_TOLERANCE_MW) & (dispatch["market_1_price"] < 0)
    m2_charging_negative = (dispatch["charge_m2_mw"] > CHARGING_POWER_TOLERANCE_MW) & (dispatch["market_2_price"] < 0)
    negative_price_charging_periods = int((m1_charging_negative | m2_charging_negative).sum())

    # Fixed OPEX does not affect the optimal dispatch because it is
    # independent of the charging/discharging decisions. It is deducted
    # afterwards to report operating profit over the simulated period.
    fixed_opex = battery.fixed_opex_gbp_per_year * observed_years(dispatch)

    rows = [
        ("Simulation start", dispatch["timestamp"].iloc[0]),
        ("Simulation end", dispatch["timestamp"].iloc[-1]),
        ("Number of half-hour periods", len(dispatch)),
        ("Total trading profit (GBP)", result.total_profit),
        ("Fixed operating cost (GBP)", fixed_opex),
        ("Operating profit after fixed OPEX (GBP)", result.total_profit - fixed_opex),
        ("Charging expenditure (GBP)", charging_expenditure),
        ("Discharge revenue (GBP)", discharge_revenue),
        ("Total purchased energy (MWh)", m1_charge_energy + m2_charge_energy),
        ("Total delivered energy (MWh)", m1_discharge_energy + m2_discharge_energy),
        ("Market 1 charging energy (MWh)", m1_charge_energy),
        ("Market 2 charging energy (MWh)", m2_charge_energy),
        ("Market 1 discharging energy (MWh)", m1_discharge_energy),
        ("Market 2 discharging energy (MWh)", m2_discharge_energy),
        ("Cumulative EFC", result.cumulative_efc),
        ("Cycle lifetime", battery.lifetime_cycles),
        ("Cycle lifetime used (%)", 100 * result.cumulative_efc / battery.lifetime_cycles),
        ("Initial usable capacity (MWh)", battery.max_storage_mwh),
        ("Final usable capacity (MWh)", result.final_usable_capacity),
        ("Capacity degradation (%)", 100 * (1 - result.final_usable_capacity / battery.max_storage_mwh)),
        ("Initial SoC (MWh)", INITIAL_SOC_MWH),
        ("Final SoC (MWh)", result.final_soc),
        ("Negative-price charging periods", negative_price_charging_periods),
    ]
    return pd.DataFrame(rows, columns=["Metric", "Value"])


def _inputs_frame(battery: BatterySpec, config: RunConfig) -> pd.DataFrame:
    battery_rows = [
        ("Maximum charging rate (MW)", battery.max_charge_mw),
        ("Maximum discharging rate (MW)", battery.max_discharge_mw),
        ("Nominal storage capacity (MWh)", battery.max_storage_mwh),
        ("Charging loss fraction (-)", battery.charging_loss_fraction),
        ("Discharging loss fraction (-)", battery.discharging_loss_fraction),
        ("Calendar lifetime (years)", battery.lifetime_years),
        ("Cycle lifetime (cycles)", battery.lifetime_cycles),
        ("Degradation (% per cycle)", battery.degradation_pct_per_cycle),
        ("CAPEX (GBP)", battery.capex_gbp),
        ("Fixed annual operating cost (GBP/year)", battery.fixed_opex_gbp_per_year),
    ]
    config_rows = [(label, "Yes" if getattr(config, field) else "No") for label, field in _RUN_CONFIG_FIELDS.items()]
    return pd.DataFrame(battery_rows + config_rows, columns=["Field", "Value"])


def _period_summary_frame(dispatch: pd.DataFrame) -> pd.DataFrame:
    working = dispatch.copy()
    working["year"] = pd.to_datetime(working["timestamp"]).dt.year
    working["purchased_mwh"] = (working["charge_m1_mw"] + working["charge_m2_mw"]) * DELTA_T_HOURS
    working["delivered_mwh"] = (working["discharge_m1_mw"] + working["discharge_m2_mw"]) * DELTA_T_HOURS
    working["m1_charge_mwh"] = working["charge_m1_mw"] * DELTA_T_HOURS
    working["m2_charge_mwh"] = working["charge_m2_mw"] * DELTA_T_HOURS
    working["m1_discharge_mwh"] = working["discharge_m1_mw"] * DELTA_T_HOURS
    working["m2_discharge_mwh"] = working["discharge_m2_mw"] * DELTA_T_HOURS

    annual = (
        working.groupby("year")
        .agg(
            **{
                "Trading profit (GBP)": ("period_profit", "sum"),
                "Purchased energy (MWh)": ("purchased_mwh", "sum"),
                "Delivered energy (MWh)": ("delivered_mwh", "sum"),
                "Market 1 charge energy (MWh)": ("m1_charge_mwh", "sum"),
                "Market 2 charge energy (MWh)": ("m2_charge_mwh", "sum"),
                "Market 1 discharge energy (MWh)": ("m1_discharge_mwh", "sum"),
                "Market 2 discharge energy (MWh)": ("m2_discharge_mwh", "sum"),
            }
        )
        .reset_index()
        .rename(columns={"year": "Year"})
    )
    return annual


def _autosize_columns(worksheet, frame: pd.DataFrame) -> None:
    for col_idx, column in enumerate(frame.columns, start=1):
        longest = len(str(column))
        if len(frame):
            longest = max(longest, frame[column].astype(str).str.len().max())
        column_letter = worksheet.cell(row=1, column=col_idx).column_letter
        worksheet.column_dimensions[column_letter].width = min(40, longest + 2)


def write_report(
    result: SimulationResult,
    battery: BatterySpec,
    config: RunConfig,
    output_dir: str = OUTPUT_DIR,
) -> Path:
    dispatch = result.dispatch

    sheets = {
        "Summary": _summary_frame(dispatch, result, battery),
        "Inputs": _inputs_frame(battery, config),
        "Dispatch": dispatch,
        "Period Summary": _period_summary_frame(dispatch),
    }

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = Path(output_dir) / f"optimisation_{timestamp}.xlsx"

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for sheet_name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=sheet_name, index=False)
            _autosize_columns(writer.sheets[sheet_name], frame)

    return path
