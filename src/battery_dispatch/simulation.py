"""Full-dataset rolling-horizon battery dispatch simulation.

Repeatedly solves a 48-hour look-ahead window with optimise_window(),
executes only the first 24 hours of each solve, and carries the executed
battery state (SoC, cumulative EFC, degraded usable capacity) forward into
the next window. Provisional look-ahead decisions beyond the executed 24
hours are always discarded and re-optimised once that day becomes the
executed day, so tomorrow's prices inform today's decisions without
tomorrow's dispatch ever being counted.
"""

from dataclasses import dataclass

import pandas as pd

from battery_dispatch.data import BatterySpec, RunConfig
from battery_dispatch.model import DELTA_T_HOURS, optimise_window

LOOKAHEAD_PERIODS = 96  # 48 hours on the half-hourly grid
EXECUTE_PERIODS = 48  # 24 hours on the half-hourly grid


@dataclass
class SimulationResult:
    dispatch: pd.DataFrame
    total_profit: float
    cumulative_efc: float
    final_soc: float
    final_usable_capacity: float


def run_simulation(prices: pd.DataFrame, battery: BatterySpec, config: RunConfig) -> SimulationResult:
    eta_discharge = 1 - battery.discharging_loss_fraction if config.include_discharging_loss else 1.0

    initial_soc = 0.0
    cumulative_efc = 0.0
    current_usable_capacity = battery.max_storage_mwh

    executed_blocks = []
    n = len(prices)
    start = 0

    while start < n:
        window_len = min(LOOKAHEAD_PERIODS, n - start)
        if window_len % 2 != 0:
            raise ValueError(
                f"Remaining price data at row {start} has an odd length "
                f"({window_len}); cannot align to Market 2's hourly pairs."
            )

        window = prices.iloc[start : start + window_len]
        execute_len = min(EXECUTE_PERIODS, window_len)
        is_final_block = start + execute_len >= n

        remaining_cycles = max(0.0, battery.lifetime_cycles - cumulative_efc)

        result = optimise_window(
            window,
            initial_soc,
            current_usable_capacity,
            remaining_cycles,
            battery,
            config,
            force_terminal_empty=is_final_block,
        )

        # Only the first 24 hours are executed. The remaining look-ahead
        # decisions help inform the executed day but are then discarded;
        # they do not contribute to profit, cycle usage or carried state.
        executed = result.dispatch.iloc[:execute_len].copy()

        executed_storage_discharge = (
            (executed["discharge_m1_mw"] + executed["discharge_m2_mw"]) * DELTA_T_HOURS / eta_discharge
        ).sum()
        cumulative_efc += executed_storage_discharge / battery.max_storage_mwh

        if config.include_degradation:
            degradation_fraction = cumulative_efc * battery.degradation_pct_per_cycle / 100
            current_usable_capacity = battery.max_storage_mwh * (1 - degradation_fraction)
        else:
            current_usable_capacity = battery.max_storage_mwh

        # Capacity degradation is applied between executed daily blocks.
        # If the updated capacity falls slightly below the carried state of
        # charge, the small excess is treated as energy lost with the
        # capacity reduction.
        carried_soc = executed["soc_mwh"].iloc[-1]
        initial_soc = min(carried_soc, current_usable_capacity)

        executed_blocks.append(executed)
        start += execute_len

    dispatch = pd.concat(executed_blocks, ignore_index=True)

    return SimulationResult(
        dispatch=dispatch,
        total_profit=dispatch["period_profit"].sum(),
        cumulative_efc=cumulative_efc,
        final_soc=initial_soc,
        final_usable_capacity=current_usable_capacity,
    )
