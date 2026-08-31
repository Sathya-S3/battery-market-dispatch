# Model Notes

This document explains the modelling decisions behind the battery
dispatch model, in plain language rather than optimisation jargon.

## Battery/state conventions

- Power is always in MW, stored energy (state of charge, "SoC") always in
  MWh.
- Both markets are represented on one common half-hour simulation grid.
  Market 1 is naturally half-hourly; Market 2's hourly price is repeated
  across its two corresponding half-hour rows purely so both markets can
  sit in the same table.
- The charge/discharge decision variables are grid-side power: MW bought
  from or sold to the grid, not the (smaller) amount that actually
  reaches or leaves storage once losses are applied.
- Charging and discharging losses are both modelled as simple fixed
  fractions (the supplied workbook labels them "efficiency", but their
  own descriptions define them as loss fractions - e.g. 0.05 means a 5%
  loss, not a 5% efficiency).

## Two markets

- Market 1 decisions can change every 30 minutes.
- Market 2 decisions must stay constant for the full one-hour interval it
  is committed for - whatever amount is chosen, it cannot change between
  the two half-hours of that hour, even though it need not be the
  battery's full power.
- Repeating Market 2's price onto the half-hour grid is only a
  data/simulation convenience; it does not shorten the real one-hour
  commitment, which is enforced directly as a pair of equality
  constraints in the optimisation.
- Both markets draw on the same physical battery, so their combined
  charging power (and separately, combined discharging power) is capped
  at the battery's rated MW - there is no independent full-power limit
  per market.

## No simultaneous charge/discharge

A single binary "is charging" variable per half-hour period caps combined
charging power to the battery's rated limit when "on" and combined
discharging power to the rated limit when "off", which makes the two
modes mutually exclusive without needing a second binary. Idle operation
(neither charging nor discharging) is always allowed.

## Rolling look-ahead

The full ~3-year dataset is solved by repeatedly:

```text
optimise up to 48 hours ahead
execute only the first 24 hours
carry the executed state forward
move forward 24 hours, and re-optimise
```

The extra day of visibility exists so that decisions near the end of one
day are not made blind to prices just beyond midnight - a plain 24-hour-only
optimisation could, for example, discharge late in the day without
"seeing" a much more profitable opportunity a few hours later. 48 hours
is a reasonable, simple choice for this exercise, not a claim that it is
universally optimal; a natural follow-up would be to test sensitivity to
shorter or longer look-aheads (e.g. 30h/48h/72h).

## Terminal SoC

Ordinary rolling windows do not force the battery empty at the end of
their (provisional) look-ahead horizon - doing so would create an
artificial boundary effect, penalising the battery for holding energy
that a later window would have been happy to sell. The battery is only
forced to end at SoC = 0 at the very end of the complete supplied price
dataset, since energy left in the battery beyond the last available price
has no defined value in this model.

## Cycles

An equivalent full cycle (EFC) is measured here as the total storage-side discharged
energy (i.e. energy leaving the battery's stored volume, before
discharge losses) divided by the battery's original nominal storage
capacity. The supplied 5,000-cycle lifetime is enforced as a hard
constraint on cumulative EFC.

## Degradation

The supplied degradation rate (0.001% of storage capacity lost per
cycle) is used directly and linearly. For the scope of this exercise, the supplied linear degradation relationship is used directly rather than introducing a more detailed battery-ageing model that is not specified in the input data.
Capacity is updated once between each executed 24-hour block, directly
from cumulative executed EFC (not compounded block-by-block); only
executed operation ever changes the battery's permanent state, never the
discarded look-ahead portion. Because degradation is applied only
*between* blocks rather than continuously within the optimisation itself,
it can occasionally leave the battery holding slightly more stored energy
than its newly-reduced capacity allows; this small excess is treated as
lost along with the capacity reduction rather than modelled explicitly
within the MILP.

## Data timestamps

The supplied raw timestamps contain known irregularities around UK
daylight-saving transitions and are left untouched rather than repaired.
Rows are instead treated as an ordered sequence of market settlement
periods, and a new, perfectly regular half-hour "canonical" timestamp is
generated once during data preparation. The optimiser uses this canonical
timeline; the original Market 1 source timestamp is retained in the
processed data for traceability only, and never determines interval
duration or alignment.

## Economics

The dispatch objective is trading profit only - revenue from discharging
minus expenditure on charging, at the market price, summed over executed
periods. Negative prices are preserved throughout and handled naturally
by the objective (a negative price makes charging profitable and
discharging costly in that period).

CAPEX and fixed OPEX do not affect the optimal dispatch because they are
fixed with respect to individual charging and discharging decisions.
Fixed OPEX is nevertheless deducted afterwards (in the output workbook's
`Summary` sheet) to report operating profit over the simulated period.
CAPEX remains visible only in the `Inputs` sheet, since it is part of the
supplied battery specification. Investment payback is left as a potential
extension, because it would require assumptions about future market
prices and battery operation beyond the supplied dataset.

## Limitations / with more time

- Test sensitivity of results to the look-ahead duration (e.g.
  30h/48h/72h) rather than assuming 48 hours is the right choice.
- Model price uncertainty/forecasting rather than assuming perfect
  knowledge of future prices within each look-ahead window.
- A more detailed degradation model (e.g. depth-of-discharge or
  calendar-based effects) than the simple linear per-cycle rate used
  here.
- Investigate solver/runtime scaling for larger datasets or shorter
  rolling steps.
- Richer market rules (e.g. transaction costs, bid/offer spreads).
- An explicit economic value for stored energy left at the very end of
  the dataset, instead of the current forced-empty terminal condition.
- Broader automated test coverage beyond the current focused set.
