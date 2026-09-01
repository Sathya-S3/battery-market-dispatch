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
        ("Market data start", dispatch["timestamp"].iloc[0]),
        ("Market data end", dispatch["timestamp"].iloc[-1]),
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
        ("Equivalent full cycles (EFC)", result.cumulative_efc),
        ("Cycle lifetime limit (EFC)", battery.lifetime_cycles),
        ("Cycle lifetime used (%)", 100 * result.cumulative_efc / battery.lifetime_cycles),
        ("Initial usable capacity (MWh)", battery.max_storage_mwh),
        ("Final usable capacity (MWh)", result.final_usable_capacity),
        ("Capacity degradation (%)", 100 * (1 - result.final_usable_capacity / battery.max_storage_mwh)),
        ("Initial SoC (MWh)", INITIAL_SOC_MWH),
        ("Final SoC (MWh)", result.final_soc),
        ("Negative-price charging periods (half-hours)", negative_price_charging_periods),
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


# Renamed, human-readable export of the Dispatch sheet only - the
# internal dataframe columns (used elsewhere by simulation/reporting code)
# are left unchanged. source_timestamp is intentionally omitted here; it
# is retained internally for traceability but not exported.
_DISPATCH_EXPORT_COLUMNS = {
    "period": "Period",
    "timestamp": "Timestamp",
    "market_1_price": "Market 1 price (£/MWh)",
    "market_2_price": "Market 2 price (£/MWh)",
    "charge_m1_mw": "Market 1 charge (MW)",
    "charge_m2_mw": "Market 2 charge (MW)",
    "discharge_m1_mw": "Market 1 discharge (MW)",
    "discharge_m2_mw": "Market 2 discharge (MW)",
    "soc_mwh": "State of charge (MWh)",
    "period_profit": "Period trading profit (£)",
}


def _dispatch_export_frame(dispatch: pd.DataFrame) -> pd.DataFrame:
    return dispatch[list(_DISPATCH_EXPORT_COLUMNS)].rename(columns=_DISPATCH_EXPORT_COLUMNS)


# Column -> Excel number format, applied where a sheet's columns each have
# one consistent unit (Dispatch, Period Summary).
_DISPATCH_NUMBER_FORMATS = {
    "Market 1 price (£/MWh)": "0.00",
    "Market 2 price (£/MWh)": "0.00",
    "Market 1 charge (MW)": "0.000",
    "Market 2 charge (MW)": "0.000",
    "Market 1 discharge (MW)": "0.000",
    "Market 2 discharge (MW)": "0.000",
    "State of charge (MWh)": "0.000",
    "Period trading profit (£)": "0.00",
}

_PERIOD_SUMMARY_NUMBER_FORMATS = {
    "Trading profit (GBP)": "0.00",
    "Purchased energy (MWh)": "0.00",
    "Delivered energy (MWh)": "0.00",
    "Market 1 charge energy (MWh)": "0.00",
    "Market 2 charge energy (MWh)": "0.00",
    "Market 1 discharge energy (MWh)": "0.00",
    "Market 2 discharge energy (MWh)": "0.00",
}

# Metric label -> Excel number format, for the Summary sheet's "Value"
# column, which mixes several units row-by-row. Rows not listed here
# (counts, dates) are left in their default format.
_SUMMARY_NUMBER_FORMATS = {
    "Total trading profit (GBP)": "0.00",
    "Fixed operating cost (GBP)": "0.00",
    "Operating profit after fixed OPEX (GBP)": "0.00",
    "Charging expenditure (GBP)": "0.00",
    "Discharge revenue (GBP)": "0.00",
    "Total purchased energy (MWh)": "0.00",
    "Total delivered energy (MWh)": "0.00",
    "Market 1 charging energy (MWh)": "0.00",
    "Market 2 charging energy (MWh)": "0.00",
    "Market 1 discharging energy (MWh)": "0.00",
    "Market 2 discharging energy (MWh)": "0.00",
    "Equivalent full cycles (EFC)": "0.00",
    "Cycle lifetime used (%)": "0.00",
    "Initial usable capacity (MWh)": "0.000",
    "Final usable capacity (MWh)": "0.000",
    "Capacity degradation (%)": "0.00",
    "Initial SoC (MWh)": "0.000",
    "Final SoC (MWh)": "0.000",
}

_SHEET_NUMBER_FORMATS = {
    "Dispatch": _DISPATCH_NUMBER_FORMATS,
    "Period Summary": _PERIOD_SUMMARY_NUMBER_FORMATS,
}


def _apply_number_formats(worksheet, frame: pd.DataFrame, formats: dict) -> None:
    for col_idx, column in enumerate(frame.columns, start=1):
        fmt = formats.get(column)
        if fmt is None:
            continue
        column_letter = worksheet.cell(row=1, column=col_idx).column_letter
        for row_idx in range(2, len(frame) + 2):
            worksheet[f"{column_letter}{row_idx}"].number_format = fmt


def _apply_row_number_formats(worksheet, frame: pd.DataFrame, formats: dict) -> None:
    """Same idea as _apply_number_formats, but keyed by row label (the
    first column) rather than by column - for Metric/Value style sheets
    where the "Value" column mixes several units."""
    label_column = frame.columns[0]
    value_column_letter = worksheet.cell(row=1, column=2).column_letter
    for row_idx, label in enumerate(frame[label_column], start=2):
        fmt = formats.get(label)
        if fmt is not None:
            worksheet[f"{value_column_letter}{row_idx}"].number_format = fmt


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
        "Dispatch": _dispatch_export_frame(dispatch),
        "Period Summary": _period_summary_frame(dispatch),
    }

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = Path(output_dir) / f"optimisation_{timestamp}.xlsx"

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for sheet_name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=sheet_name, index=False)
            worksheet = writer.sheets[sheet_name]
            _autosize_columns(worksheet, frame)
            number_formats = _SHEET_NUMBER_FORMATS.get(sheet_name)
            if number_formats:
                _apply_number_formats(worksheet, frame, number_formats)
            if sheet_name == "Summary":
                _apply_row_number_formats(worksheet, frame, _SUMMARY_NUMBER_FORMATS)

    return path
