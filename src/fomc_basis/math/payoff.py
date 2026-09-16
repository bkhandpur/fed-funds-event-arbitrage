from __future__ import annotations

from dataclasses import asdict, dataclass

from .fed_funds import PRICE_POINT_VALUE_DOLLARS, futures_settlement_price


@dataclass(frozen=True)
class StatePayoff:
    move_bp: int
    basis_change_bp: float
    kalshi_settlement_dollars: float
    kalshi_acquisition_cost_dollars: float
    kalshi_fees_dollars: float
    futures_settlement_price_points: float
    futures_gross_pnl_dollars: float
    futures_costs_dollars: float
    combined_net_pnl_dollars: float
    capital_required_dollars: float
    return_on_capital: float

    def as_dict(self) -> dict[str, float | int]:
        return asdict(self)


def state_payoff_table(
    states_bp: list[int],
    *,
    kalshi_outcome_bp: int,
    kalshi_winning_states_bp: list[int] | None = None,
    kalshi_side: str,
    kalshi_contracts: int,
    kalshi_entry_price_dollars: float,
    kalshi_total_fees_dollars: float,
    futures_contracts: int,
    futures_entry_price_points: float,
    futures_round_trip_cost_per_contract_dollars: float,
    pre_effr_pct: float,
    days_in_month: int,
    pre_days: int,
    post_days: int,
    futures_margin_per_contract_dollars: float,
    basis_change_bp: float = 0.0,
) -> list[StatePayoff]:
    """Positive futures count is long and must have entered at the executable ask."""
    if kalshi_side not in {"yes", "no"}:
        raise ValueError("kalshi_side must be yes or no")
    acquisition = kalshi_contracts * kalshi_entry_price_dollars
    futures_costs = abs(futures_contracts) * futures_round_trip_cost_per_contract_dollars
    capital = (
        acquisition
        + kalshi_total_fees_dollars
        + abs(futures_contracts) * futures_margin_per_contract_dollars
    )
    rows = []
    winning_states = set(kalshi_winning_states_bp or [kalshi_outcome_bp])
    for move in states_bp:
        yes_settles = move in winning_states
        wins = yes_settles if kalshi_side == "yes" else not yes_settles
        settlement = float(kalshi_contracts if wins else 0)
        futures_settle = futures_settlement_price(
            pre_effr_pct, move, days_in_month, pre_days, post_days, basis_change_bp
        )
        futures_pnl = (
            (futures_settle - futures_entry_price_points)
            * PRICE_POINT_VALUE_DOLLARS
            * futures_contracts
        )
        net = settlement - acquisition - kalshi_total_fees_dollars + futures_pnl - futures_costs
        rows.append(
            StatePayoff(
                move,
                basis_change_bp,
                settlement,
                acquisition,
                kalshi_total_fees_dollars,
                futures_settle,
                futures_pnl,
                futures_costs,
                net,
                capital,
                net / capital if capital else 0.0,
            )
        )
    return rows
