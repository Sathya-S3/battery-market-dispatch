"""Run the full battery dispatch simulation and write the results workbook.

    python -m battery_dispatch.run

Uses the supplied battery specification, the current data/run_config.xlsx
scenario switches, and the prepared market-price data. Solving the full
~3-year dataset takes several minutes (roughly 6-7 on a typical laptop).
"""

import time

from battery_dispatch.data import load_battery_spec, load_market_prices, load_run_config
from battery_dispatch.reporting import write_report
from battery_dispatch.simulation import run_simulation


def main() -> None:
    battery = load_battery_spec()
    config = load_run_config()
    prices = load_market_prices()

    print(f"Running rolling simulation over {len(prices)} half-hour periods...")
    start = time.time()
    result = run_simulation(prices, battery, config)
    elapsed = time.time() - start

    output_path = write_report(result, battery, config)

    print(f"Solved in {elapsed / 60:.1f} minutes")
    print(f"Executed periods: {len(result.dispatch)}")
    print(f"Total trading profit: GBP {result.total_profit:,.2f}")
    print(f"Cumulative EFC: {result.cumulative_efc:.1f} (lifetime {battery.lifetime_cycles:.0f})")
    print(f"Final SoC: {result.final_soc:.3f} MWh")
    print(f"Final usable capacity: {result.final_usable_capacity:.4f} MWh (nominal {battery.max_storage_mwh} MWh)")
    print(f"Results written to {output_path}")


if __name__ == "__main__":
    main()
