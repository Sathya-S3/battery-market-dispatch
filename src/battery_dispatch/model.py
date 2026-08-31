"""Single-window battery dispatch optimisation (MILP via PuLP/CBC).

Optimises charging/discharging power against Market 1 (half-hourly) and
Market 2 (hourly) prices over one supplied window of half-hour periods,
subject to the battery's physical limits and the two-market commitment
rules. This does not run the multi-day rolling simulation; that is a
later phase built on top of this single-window solve.
"""

from dataclasses import dataclass

import pandas as pd
import pulp

from battery_dispatch.data import BatterySpec, RunConfig

DELTA_T_HOURS = 0.5


@dataclass
class WindowResult:
    dispatch: pd.DataFrame
    status: str
    total_profit: float
    window_efc: float
    final_soc: float


def optimise_window(
    prices: pd.DataFrame,
    initial_soc: float,
    current_usable_capacity: float,
    remaining_cycles: float,
    battery: BatterySpec,
    config: RunConfig,
    force_terminal_empty: bool = False,
) -> WindowResult:
    n = len(prices)
    if n == 0:
        raise ValueError("Price window is empty.")
    if n % 2 != 0:
        raise ValueError(
            "Price window must contain an even number of half-hour periods "
            "so Market 2's hourly commitment pairs up cleanly."
        )
    if current_usable_capacity <= 0:
        raise ValueError("current_usable_capacity must be positive.")
    if not (0 <= initial_soc <= current_usable_capacity):
        raise ValueError("initial_soc must be between 0 and current_usable_capacity.")
    if remaining_cycles < 0:
        raise ValueError("remaining_cycles must be non-negative.")

    periods = range(n)
    m1_price = prices["market_1_price"].to_numpy()
    m2_price = prices["market_2_price"].to_numpy()

    eta_charge = 1 - battery.charging_loss_fraction if config.include_charging_loss else 1.0
    eta_discharge = 1 - battery.discharging_loss_fraction if config.include_discharging_loss else 1.0

    prob = pulp.LpProblem("battery_dispatch_window", pulp.LpMaximize)

    charge_m1 = pulp.LpVariable.dicts("charge_m1", periods, lowBound=0)
    charge_m2 = pulp.LpVariable.dicts("charge_m2", periods, lowBound=0)
    discharge_m1 = pulp.LpVariable.dicts("discharge_m1", periods, lowBound=0)
    discharge_m2 = pulp.LpVariable.dicts("discharge_m2", periods, lowBound=0)
    is_charging = pulp.LpVariable.dicts("is_charging", periods, cat="Binary")
    soc = pulp.LpVariable.dicts("soc", periods, lowBound=0, upBound=current_usable_capacity)

    # A market disabled in RunConfig is simply fixed to zero via its upper
    # bound, rather than branching into a separate optimiser variant.
    if not config.use_market_1_charging:
        for t in periods:
            charge_m1[t].upBound = 0
    if not config.use_market_2_charging:
        for t in periods:
            charge_m2[t].upBound = 0
    if not config.use_market_1_discharging:
        for t in periods:
            discharge_m1[t].upBound = 0
    if not config.use_market_2_discharging:
        for t in periods:
            discharge_m2[t].upBound = 0

    for t in periods:
        total_charge = charge_m1[t] + charge_m2[t]
        total_discharge = discharge_m1[t] + discharge_m2[t]
        previous_soc = initial_soc if t == 0 else soc[t - 1]

        # Charging decisions are MW bought from the grid, so charging losses
        # are taken before energy reaches storage. Discharging decisions are
        # MW delivered to the grid, so more stored energy leaves the battery
        # than is delivered when discharge losses apply.
        prob += (
            soc[t]
            == previous_soc
            + eta_charge * total_charge * DELTA_T_HOURS
            - total_discharge * DELTA_T_HOURS / eta_discharge
        ), f"soc_balance_{t}"

        # Both markets draw on the same physical battery, and the battery
        # cannot charge and discharge in the same half-hour.
        prob += total_charge <= battery.max_charge_mw * is_charging[t], f"charge_limit_{t}"
        prob += total_discharge <= battery.max_discharge_mw * (1 - is_charging[t]), f"discharge_limit_{t}"

    # Market 2 is hourly: once a MW amount is committed, it holds across
    # both half-hour periods of that hour. The committed amount can be any
    # feasible value, not necessarily full power - it just cannot change
    # between the two halves of the hour. Market 2's price has already been
    # repeated onto this half-hour grid purely as a data convenience; that
    # repetition does not shorten the commitment back to 30 minutes.
    for k in range(n // 2):
        prob += charge_m2[2 * k] == charge_m2[2 * k + 1], f"m2_charge_hold_{k}"
        prob += discharge_m2[2 * k] == discharge_m2[2 * k + 1], f"m2_discharge_hold_{k}"

    # Equivalent full cycles: storage-side discharged energy relative to the
    # battery's original nominal capacity (not the current usable one).
    storage_discharged = pulp.lpSum(
        (discharge_m1[t] + discharge_m2[t]) * DELTA_T_HOURS / eta_discharge for t in periods
    )
    window_efc = storage_discharged / battery.max_storage_mwh
    prob += window_efc <= remaining_cycles, "cycle_limit"

    if force_terminal_empty:
        prob += soc[n - 1] == 0, "terminal_empty"

    period_profit = [
        DELTA_T_HOURS
        * (
            m1_price[t] * (discharge_m1[t] - charge_m1[t])
            + m2_price[t] * (discharge_m2[t] - charge_m2[t])
        )
        for t in periods
    ]
    prob += pulp.lpSum(period_profit)

    prob.solve(pulp.PULP_CBC_CMD(msg=0))

    status = pulp.LpStatus[prob.status]
    if status != "Optimal":
        raise RuntimeError(f"Optimisation failed with solver status: {status}")

    dispatch = prices.reset_index(drop=True).copy()
    dispatch["charge_m1_mw"] = [pulp.value(charge_m1[t]) for t in periods]
    dispatch["charge_m2_mw"] = [pulp.value(charge_m2[t]) for t in periods]
    dispatch["discharge_m1_mw"] = [pulp.value(discharge_m1[t]) for t in periods]
    dispatch["discharge_m2_mw"] = [pulp.value(discharge_m2[t]) for t in periods]
    dispatch["soc_mwh"] = [pulp.value(soc[t]) for t in periods]
    dispatch["period_profit"] = [pulp.value(period_profit[t]) for t in periods]

    return WindowResult(
        dispatch=dispatch,
        status=status,
        total_profit=pulp.value(prob.objective),
        window_efc=pulp.value(window_efc),
        final_soc=pulp.value(soc[n - 1]),
    )
