# Numerical conventions

Python is the financial source of truth. The version 2 API exposes typed leg ledgers, candidates, probability constraints and execution evidence. Generated examples in `lib` are produced by the same engine and checked against fresh calculations in CI. They render immediately without waiting for providers.

## Independent reference A

The month has 30 calendar days split 15 and 15. EFFR is 3.50 percent before effectiveness and under no change. Zero and +25 bp have equal masses. Continuous inversion gives 3.5625 percent monthly EFFR and 96.4375 midpoint futures points.

With 500 YES at 0.48, synthetic cent-rounded taker fee is 8.74 dollars. One long future enters at ask 96.440, settles at 96.500 or 96.375 and incurs 6.04 dollars round-trip cost. Binary model EV is 1.26 dollars. Combined state P&L is -4.76 and -25.635 dollars and combined model EV is -15.1975 dollars. Each 0.01 dollars per binary contract of entry slippage lowers both state payoffs by 5 dollars and raises acquisition cash by 5 dollars. Numerical acceptance uses 1e-8 dollars.

## Settlement

[CBOT Chapter 22](https://www.cmegroup.com/rulebook/CBOT/III/22.pdf) specifies 4167 dollars per index point and half-up final monthly-rate rounding to 0.001 percentage point. Continuous deposit DV01 and expectation inversion remain named analytical approximations. Contract identity is checked against the effective settlement month. First-day effectiveness uses the next month calendar, including leap February.

Pre-effective calendar-day average, post no-change EFFR and target basis are distinct inputs. Examples use labeled flat-rate assumptions. They do not reconstruct missing daily history. The calendar-average helper filters publication times and carries a preceding published rate only on explicitly designated nonpublication dates. Missing observations fail.

Tick indication follows the Chapter 22 weekday transition between 0.005 and 0.0025 points. A holiday trading-calendar certification is unavailable. This indication cannot establish executable quote validity. Multiple meetings block a single-meeting execution claim.

## Fees

The [official July 7 2026 Kalshi schedule](https://kalshi.com/docs/kalshi-fee-schedule.pdf), checked October 14 2026, specifies centicent rounding of aggregate fee plus position cost. KXFED and KXFEDDECISION have taker multiplier one and maker multiplier one. Their coefficients are 0.07 and 0.0175. Maker status, coefficient overrides, settlement fees and rounding are explicit request assumptions. Reference A and the historical example preserve named cent rounding. Change rounding to 0.0001 and the appropriate maker coefficient when modeling that checked schedule. Fee schedules can change and are not live execution certification.

Each break-even and hurdle evaluation reprices the aggregate rounded fee at its candidate price. Futures cost is per round trip per absolute contract. Binary slippage is entry-only acquisition cash. Margin is not deducted from P&L. Estimated cash plus assumed margin does not establish a broker capital requirement or sufficient variation-margin liquidity.

## Probabilities and positions

Joint LP constraints impose sum one, nonnegative state masses, a futures-implied expected move and declared state bounds. Two-state mode assigns zero mass outside its declared pair. Point masses minimize KL divergence to the displayed prior and remain prior conditioned. A user distribution can differ from the market moment and its residual and EV are separately labeled. Infeasible constraints fail rather than being normalized.

For zero, +25 and +50 at mean +25, p0 equals p50 and p25 equals one minus twice p50. Thus p25 spans zero to one without tails. Fixing p50 to 0.10 gives p25 of 0.80 and p0 of 0.10. Combined EV bounds apply one joint distribution to the complete ledger rather than summing marginal extrema.

Pair flattening divides the signed binary payout difference by the exact futures payoff difference with the opposite sign. A bucket has residual risk outside the chosen pair. Exact bounded enumeration considers every integer from negative to positive position limit plus no position, maximizing conservative combined EV within declared stress and capital limits. Ties are assessed by objective value.

## Exports and migration

Export JSON carries schema and model versions, the immutable analyzed request, selected candidate, units and provenance. CSV carries the numerical ledger. Run `fomc-basis rerun saved.json` from an installed wheel outside the checkout. The returned version 2 result reproduces cashflows to 1e-8 dollars. Research source times are retained, so rerunning a historical request does not make it fresh.

Historical flat input fields and legacy tail aliases have moved into the shared request and probability model. The old routes, package CLI and local Streamlit remain available. The generic standalone maximin optimizer is preserved and is not used by the website.
