"""Focused test for run_simulation()'s rolling state carry-forward."""

import pandas as pd
import pytest

from battery_dispatch.data import BatterySpec, RunConfig
from battery_dispatch.model import DELTA_T_HOURS, optimise_window
from battery_dispatch.simulation import EXECUTE_PERIODS, LOOKAHEAD_PERIODS, run_simulation

TOL = 1e-6


def _battery() -> BatterySpec:
    return BatterySpec(
        max_charge_mw=2.0,
        max_discharge_mw=2.0,
        max_storage_mwh=4.0,
        charging_loss_fraction=0.05,
        discharging_loss_fraction=0.05,
        lifetime_years=10.0,
        lifetime_cycles=5000.0,
        degradation_pct_per_cycle=0.001,
        capex_gbp=500000.0,
        fixed_opex_gbp_per_year=5000.0,
    )


def _config() -> RunConfig:
    return RunConfig(
        use_market_1_charging=True,
        use_market_2_charging=True,
        include_charging_loss=True,
        include_degradation=True,
        include_discharging_loss=True,
        use_market_1_discharging=True,
        use_market_2_discharging=True,
    )


def test_rolling_carry_forward_uses_executed_soc_not_lookahead_soc():
    battery = _battery()
    config = _config()
    eta_charge = 1 - battery.charging_loss_fraction
    eta_discharge = 1 - battery.discharging_loss_fraction

    # Two executed blocks (96 periods = 48 + 48). Price is flat everywhere
    # except: very cheap on the last period of day 1 (prompting a charge
    # right at the boundary) and very expensive on the first period of day
    # 2 (visible to day 1's look-ahead, but not executed until day 2).
    n = LOOKAHEAD_PERIODS
    m1_price = [50.0] * n
    m1_price[EXECUTE_PERIODS - 1] = -500.0
    m1_price[EXECUTE_PERIODS] = 500.0
    prices = pd.DataFrame({"market_1_price": m1_price, "market_2_price": [50.0] * n})

    result = run_simulation(prices, battery, config)
    dispatch = result.dispatch

    previous_executed_soc = dispatch["soc_mwh"].iloc[EXECUTE_PERIODS - 1]
    next_row = dispatch.iloc[EXECUTE_PERIODS]
    total_charge = next_row["charge_m1_mw"] + next_row["charge_m2_mw"]
    total_discharge = next_row["discharge_m1_mw"] + next_row["discharge_m2_mw"]

    expected_next_soc = (
        previous_executed_soc
        + eta_charge * total_charge * DELTA_T_HOURS
        - total_discharge * DELTA_T_HOURS / eta_discharge
    )

    # This is the actual carry-forward check: the first row of block 2 must
    # balance against the end of block 1's EXECUTED portion. If the wrong
    # state (e.g. the provisional 48h-look-ahead SoC) had been passed into
    # the second optimise_window() call, this balance would not hold.
    assert next_row["soc_mwh"] == pytest.approx(expected_next_soc, abs=TOL)

    # Confirm the test setup is actually meaningful: the executed-block
    # ending SoC should be clearly different from what the same first
    # window's own (unexecuted) full 48-hour look-ahead solution would
    # have ended with.
    standalone = optimise_window(
        prices.iloc[:LOOKAHEAD_PERIODS],
        initial_soc=0.0,
        current_usable_capacity=battery.max_storage_mwh,
        remaining_cycles=battery.lifetime_cycles,
        battery=battery,
        config=config,
        force_terminal_empty=False,
    )
    assert abs(previous_executed_soc - standalone.final_soc) > 0.3
