"""Focused tests for the single-window MILP in battery_dispatch.model."""

import pandas as pd
import pytest

from battery_dispatch.data import BatterySpec, RunConfig
from battery_dispatch.model import optimise_window

TOL = 1e-6


def _battery(**overrides) -> BatterySpec:
    defaults = dict(
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
    defaults.update(overrides)
    return BatterySpec(**defaults)


def _config(**overrides) -> RunConfig:
    defaults = dict(
        use_market_1_charging=True,
        use_market_2_charging=True,
        include_charging_loss=True,
        include_degradation=True,
        include_discharging_loss=True,
        use_market_1_discharging=True,
        use_market_2_discharging=True,
    )
    defaults.update(overrides)
    return RunConfig(**defaults)


def test_soc_loss_accounting_market_1_only():
    # Market 2 is disabled so the charge/discharge power is unambiguously
    # Market 1's, keeping the expected arithmetic simple to check by hand.
    battery = _battery()
    config = _config(use_market_2_charging=False, use_market_2_discharging=False)
    eta_charge = 1 - battery.charging_loss_fraction
    eta_discharge = 1 - battery.discharging_loss_fraction

    prices = pd.DataFrame(
        {
            "market_1_price": [-1000.0, 1000.0],
            "market_2_price": [0.0, 0.0],
        }
    )

    result = optimise_window(
        prices,
        initial_soc=0.0,
        current_usable_capacity=battery.max_storage_mwh,
        remaining_cycles=battery.lifetime_cycles,
        battery=battery,
        config=config,
        force_terminal_empty=True,
    )
    d = result.dispatch

    # An extremely negative price at period 0 makes full charging optimal.
    assert d["charge_m1_mw"].iloc[0] == pytest.approx(battery.max_charge_mw, abs=TOL)
    expected_soc_0 = eta_charge * battery.max_charge_mw * 0.5
    assert d["soc_mwh"].iloc[0] == pytest.approx(expected_soc_0, abs=TOL)

    # force_terminal_empty drains everything by period 1; check the
    # discharge power that implies against the 1/eta_discharge relation.
    assert d["soc_mwh"].iloc[1] == pytest.approx(0.0, abs=TOL)
    expected_discharge_power = expected_soc_0 * eta_discharge / 0.5
    assert d["discharge_m1_mw"].iloc[1] == pytest.approx(expected_discharge_power, abs=TOL)


def test_physical_power_and_mode_constraints():
    battery = _battery()
    config = _config()
    prices = pd.DataFrame(
        {
            "market_1_price": [10.0, 100.0, 10.0, 100.0],
            "market_2_price": [50.0, 50.0, 90.0, 90.0],
        }
    )
    result = optimise_window(
        prices,
        initial_soc=0.0,
        current_usable_capacity=battery.max_storage_mwh,
        remaining_cycles=battery.lifetime_cycles,
        battery=battery,
        config=config,
    )
    d = result.dispatch
    total_charge = d["charge_m1_mw"] + d["charge_m2_mw"]
    total_discharge = d["discharge_m1_mw"] + d["discharge_m2_mw"]

    assert (total_charge <= battery.max_charge_mw + TOL).all()
    assert (total_discharge <= battery.max_discharge_mw + TOL).all()
    assert ((total_charge < TOL) | (total_discharge < TOL)).all()


def test_market_2_hourly_commitment_when_used():
    battery = _battery()
    config = _config()
    # Market 1 is flat (no arbitrage value once losses are considered);
    # Market 2 has a clear cheap-hour/expensive-hour spread, so Market 2 is
    # the only market worth using and the equality check below is
    # meaningful rather than vacuously true on all-zero variables.
    prices = pd.DataFrame(
        {
            "market_1_price": [50.0, 50.0, 50.0, 50.0],
            "market_2_price": [10.0, 10.0, 90.0, 90.0],
        }
    )
    result = optimise_window(
        prices,
        initial_soc=0.0,
        current_usable_capacity=battery.max_storage_mwh,
        remaining_cycles=battery.lifetime_cycles,
        battery=battery,
        config=config,
    )
    d = result.dispatch

    assert d["charge_m2_mw"].iloc[0] == pytest.approx(d["charge_m2_mw"].iloc[1], abs=TOL)
    assert d["discharge_m2_mw"].iloc[2] == pytest.approx(d["discharge_m2_mw"].iloc[3], abs=TOL)
    assert d["charge_m2_mw"].iloc[0] > TOL, "Expected Market 2 charging in this test case."
    assert d["discharge_m2_mw"].iloc[2] > TOL, "Expected Market 2 discharging in this test case."


def test_terminal_empty_forces_zero_final_soc():
    battery = _battery()
    config = _config()
    prices = pd.DataFrame(
        {
            "market_1_price": [50.0, 50.0],
            "market_2_price": [50.0, 50.0],
        }
    )
    result = optimise_window(
        prices,
        initial_soc=2.0,
        current_usable_capacity=battery.max_storage_mwh,
        remaining_cycles=battery.lifetime_cycles,
        battery=battery,
        config=config,
        force_terminal_empty=True,
    )
    assert result.final_soc == pytest.approx(0.0, abs=TOL)


def test_efc_cap_is_respected():
    battery = _battery()
    config = _config()
    prices = pd.DataFrame(
        {
            "market_1_price": [10.0, 100.0, 10.0, 100.0],
            "market_2_price": [50.0, 50.0, 90.0, 90.0],
        }
    )
    result = optimise_window(
        prices,
        initial_soc=0.0,
        current_usable_capacity=battery.max_storage_mwh,
        remaining_cycles=0.1,
        battery=battery,
        config=config,
    )
    assert result.window_efc <= 0.1 + TOL
