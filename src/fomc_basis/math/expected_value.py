from __future__ import annotations

from decimal import Decimal


def binary_ev_per_contract(
    probability: float,
    price_dollars: Decimal,
    fees_dollars: Decimal = Decimal("0"),
    slippage_dollars: Decimal = Decimal("0"),
    *,
    side: str = "yes",
) -> Decimal:
    if not 0 <= probability <= 1:
        raise ValueError("probability must be in [0, 1]")
    if side not in {"yes", "no"}:
        raise ValueError("side must be 'yes' or 'no'")
    win_probability = probability if side == "yes" else 1.0 - probability
    return Decimal(str(win_probability)) - price_dollars - fees_dollars - slippage_dollars


def break_even_probability(
    price_dollars: Decimal,
    fees_dollars: Decimal = Decimal("0"),
    slippage_dollars: Decimal = Decimal("0"),
    *,
    side: str = "yes",
) -> float:
    cost = float(price_dollars + fees_dollars + slippage_dollars)
    return cost if side == "yes" else 1.0 - cost


def price_for_ev_hurdle(
    probability: float, hurdle_dollars: float, costs_dollars: float = 0.0, *, side: str = "yes"
) -> float:
    win_probability = probability if side == "yes" else 1.0 - probability
    return win_probability - hurdle_dollars - costs_dollars


def expected_return_on_cash(
    ev_dollars: float, price_dollars: float, fees_dollars: float = 0.0
) -> float:
    capital = price_dollars + fees_dollars
    return ev_dollars / capital if capital > 0 else float("inf")
