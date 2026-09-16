# FOMC Basis Monitor

A research tool for comparing Kalshi FOMC event contracts with 30-Day Federal
Funds futures. The central question is whether an apparent probability gap survives
contract normalization, executable prices, fees and settlement-basis risk.

The monitor never places orders. It classifies a setup as `TRUE_ARBITRAGE`,
`NEAR_ARBITRAGE_WITH_SMALL_BASIS_RISK`, `RELATIVE_VALUE_TRADE` or `NO_TRADE`, and
reports the failed conditions behind that label.

## Why the comparison is difficult

A Kalshi contract pays on a specific target-rate outcome. A ZQ futures contract settles
to the arithmetic average of daily effective federal funds rates across a calendar
month. Their displayed percentages are therefore not directly comparable.

The model first removes the known pre-meeting portion of the monthly average:

\[
100-F=\frac{d_{pre}r_{pre}+d_{post}r_{post}}{D}, \qquad
r_{post}=\frac{D(100-F)-d_{pre}r_{pre}}{d_{post}}
\]

Under a two-state 0/+25 bp assumption, the expected move can be expressed as a +25 bp
equivalent probability. Outside that assumption, futures identify an expected value,
not a unique outcome distribution. The package therefore supports probability bounds
and a regularized distribution rather than silently treating one point estimate as an
exact-outcome probability.

## What the project covers

- Official FOMC meeting calendars and calendar-day weighting
- Kalshi market discovery, settlement mapping and order-book normalization
- ZQ quote handling with explicit indicative/executable provenance
- Probability bounds under configurable state and tail constraints
- Kalshi fees, futures costs, depth, capital limits and integer hedge sizing
- EFFR-versus-target basis scenarios and state-by-state payoff tables
- SQLite snapshots, realized outcomes, replay and calibration metrics
- CLI, JSON output and a Streamlit dashboard

## Install

Python 3.11 or newer is required.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,parquet]"
```

## Quick start

The repository includes a fixed September 16, 2026 case study. The observations are
user-supplied and are not presented as synchronized or executable quotes.

```bash
fomc-basis analyze --meeting 2026-09-16
```

For a manual meeting analysis:

```bash
fomc-basis analyze \
  --meeting 2027-01-27 \
  --futures-bid 96.338 \
  --futures-ask 96.340 \
  --kalshi-yes-bid 0.87 \
  --kalshi-yes-ask 0.88 \
  --effr-pct 3.63 \
  --output-json reports/analysis.json
```

Useful commands:

```bash
fomc-basis meetings --year 2027
fomc-basis discover-kalshi --meeting 2027-01-27
fomc-basis snapshot --as-of 2027-01-27
fomc-basis curve --contract 2027-01:96.34 --contract 2027-02:96.20 \
  --meeting-effective 2027-01-28 --starting-effr-pct 3.63
fomc-basis payoff --analysis-json reports/analysis.json --json
fomc-basis optimize --analysis-json reports/analysis.json --capital 5000 --json
fomc-basis backtest --database data/fomc_basis.sqlite3 --json
```

Run the dashboard with:

```bash
streamlit run dashboard/app.py
```

## Analysis flow

```text
providers -> validated domain models -> probability and payoff math
          -> classification -> SQLite -> CLI / dashboard
```

The provider layer is separate from the math layer. Quotes retain source and receipt
timestamps, observation kind and raw payloads. Analysis runs use deterministic IDs so
the same observation can be replayed without creating duplicate records.

The arbitrage test is stricter than positive expected value. Net payoff must be
nonnegative in every modeled state, positive in at least one state, and based on fresh,
compatible and sufficiently deep quotes after costs. If tails, basis risk, stale data or
integer sizing break that condition, the output says so.

## Data sources

- Kalshi public API for market definitions and order-book depth
- Federal Reserve for scheduled meeting dates
- Federal Reserve Bank of New York for EFFR observations
- Yahoo Finance as a free indicative ZQ source
- Manual and CSV providers for controlled analysis and replay

Yahoo data is not exchange-direct and may be delayed, rounded or incomplete. A last
trade is never promoted to an executable bid or ask. Historical calibration is only
meaningful after enough genuinely recorded snapshots and realized outcomes have been
collected; reconstructed and synthetic observations remain labeled as such.

## Tests

```bash
pytest
ruff check .
mypy src
```

Unit tests cover day-count decomposition, contract mapping, probability constraints,
fees, hedge direction, state payoffs, quote-quality gates and false-arbitrage cases.
Integration tests cover snapshots, persistence, replay, CLI JSON and the dashboard.
Network tests are marked `live` and are excluded from the default suite.

## Repository layout

```text
src/fomc_basis/providers/   Market and reference-data adapters
src/fomc_basis/math/        Day-count, probability, fee, hedge and payoff logic
src/fomc_basis/services/    Analysis, snapshots, persistence and replay
src/fomc_basis/reporting/   Console tables and charts
dashboard/                  Streamlit interface
tests/                      Unit and integration tests
examples/                   Manual and replay fixtures
```

## Limitations

- ZQ settles to average EFFR; Kalshi contracts settle to target-rate outcomes.
- More than one meeting in a contract month can underidentify a naive single-meeting
  estimate.
- Tail outcomes change exact-outcome probabilities even when the expected move is fixed.
- Public quotes do not establish joint executability, latency or available size.
- Broker margin, venue-specific futures fees and settlement interpretation require
  independent verification before any trading decision.

## License

MIT. See [LICENSE](LICENSE).
