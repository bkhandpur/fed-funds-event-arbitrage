from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp

from ..enums import RiskFlag, TradeClassification
from ..models import ClassificationResult


@dataclass(frozen=True)
class Instrument:
    name: str
    entry_cost_dollars: float
    state_payoffs_dollars: tuple[float, ...]
    max_quantity: int
    whole_contracts: bool = True
    executable: bool = True
    capital_required_dollars: float | None = None


@dataclass(frozen=True)
class ArbitrageSolution:
    quantities: dict[str, int | float]
    state_net_payoffs_dollars: tuple[float, ...]
    worst_case_dollars: float
    status: str


def maximize_worst_case(instruments: list[Instrument], capital_dollars: float) -> ArbitrageSolution:
    """Maximize the minimum net payoff, including the zero-position alternative.

    Entry cost is the signed cash debit (negative for a short credit), inclusive
    of entry fees. State payoffs include closing cashflows and remaining costs.
    Short directions require explicit nonnegative collateral, separate from cash
    credit. Quantities are nonnegative; supply each direction separately.
    """
    if not np.isfinite(capital_dollars) or capital_dollars < 0:
        raise ValueError("capital must be nonnegative")
    if not instruments:
        return ArbitrageSolution({}, (), 0.0, "no_instruments")
    if any(item.max_quantity < 0 for item in instruments):
        raise ValueError("maximum quantities must be nonnegative")
    if len({item.name for item in instruments}) != len(instruments):
        raise ValueError("instrument names must be unique")
    if any(
        not np.isfinite(item.entry_cost_dollars)
        or not np.isfinite(item.max_quantity)
        or not all(np.isfinite(v) for v in item.state_payoffs_dollars)
        or (
            item.capital_required_dollars is not None
            and not np.isfinite(item.capital_required_dollars)
        )
        for item in instruments
    ):
        raise ValueError("instrument inputs must be finite")
    if any(
        item.whole_contracts and int(item.max_quantity) != item.max_quantity for item in instruments
    ):
        raise ValueError("whole-contract quantity limits must be integers")
    if any(
        item.entry_cost_dollars < 0 and item.capital_required_dollars is None
        for item in instruments
    ):
        raise ValueError("short credits require an explicit capital requirement")
    if any(
        item.capital_required_dollars is not None and item.capital_required_dollars < 0
        for item in instruments
    ):
        raise ValueError("capital requirements must be nonnegative")
    states = len(instruments[0].state_payoffs_dollars)
    if states == 0:
        raise ValueError("at least one exhaustive payoff state is required")
    if any(len(item.state_payoffs_dollars) != states for item in instruments):
        raise ValueError("all instruments must use the same exhaustive state grid")
    count = len(instruments)
    objective = np.zeros(count + 1)
    objective[-1] = -1.0
    # payoff @ quantity - z >= 0 -> -payoff @ quantity + z <= 0
    payoff = np.array([item.state_payoffs_dollars for item in instruments], dtype=float).T
    costs = np.array([item.entry_cost_dollars for item in instruments])
    net_payoff = payoff - costs[None, :]
    state_matrix = np.column_stack([-net_payoff, np.ones(states)])
    capital_row = np.r_[
        np.array(
            [
                max(
                    item.capital_required_dollars
                    if item.capital_required_dollars is not None
                    else item.entry_cost_dollars,
                    0,
                )
                for item in instruments
            ]
        ),
        0.0,
    ]
    constraints = [
        LinearConstraint(state_matrix, -np.inf, np.zeros(states)),
        LinearConstraint(capital_row, -np.inf, capital_dollars),
    ]
    upper = np.r_[[item.max_quantity if item.executable else 0 for item in instruments], np.inf]
    integrality = np.r_[[1 if item.whole_contracts else 0 for item in instruments], 0]
    result = milp(
        objective,
        integrality=integrality,
        bounds=Bounds(np.r_[np.zeros(count), -np.inf], upper),
        constraints=constraints,
    )
    if not result.success or result.x is None:
        return ArbitrageSolution({}, (), float("-inf"), f"failed: {result.message}")
    quantities = result.x[:count]
    net = payoff @ quantities - sum(
        item.entry_cost_dollars * quantity
        for item, quantity in zip(instruments, quantities, strict=True)
    )
    rendered = {
        item.name: int(round(quantity)) if item.whole_contracts else float(quantity)
        for item, quantity in zip(instruments, quantities, strict=True)
        if quantity > 1e-8
    }
    return ArbitrageSolution(
        rendered, tuple(float(value) for value in net), float(net.min()), "optimal"
    )


def classify(
    state_pnl_dollars: list[float],
    expected_value_dollars: float,
    risk_flags: set[RiskFlag],
    *,
    executable: bool,
    all_outcomes_modeled: bool,
    settlement_compatible: bool,
    depth_sufficient: bool,
    integer_sizing: bool,
    basis_stress_pnl_dollars: list[float],
    position_limits_satisfied: bool = True,
    capital_sufficient: bool = True,
    ev_hurdle_dollars: float = 0.01,
    tolerance: float = 1e-8,
) -> ClassificationResult:
    if not state_pnl_dollars or not basis_stress_pnl_dollars:
        raise ValueError("modeled and basis-stress states must be nonempty")
    if not all(
        np.isfinite(v)
        for v in [*state_pnl_dollars, *basis_stress_pnl_dollars, expected_value_dollars]
    ):
        raise ValueError("classification inputs must be finite")
    reasons: list[str] = []
    worst = min(state_pnl_dollars + basis_stress_pnl_dollars)
    gates = {
        "NON_EXECUTABLE_QUOTES": executable,
        "UNMODELED_OUTCOMES": all_outcomes_modeled,
        "SETTLEMENT_MISMATCH": settlement_compatible,
        "INSUFFICIENT_DEPTH": depth_sufficient,
        "NON_INTEGER_SIZING": integer_sizing,
        "POSITION_LIMIT_EXCEEDED": position_limits_satisfied,
        "CAPITAL_LIMIT_EXCEEDED": capital_sufficient,
        "LOSING_MODELED_STATE": min(state_pnl_dollars) >= -tolerance,
        "LOSING_BASIS_STRESS": min(basis_stress_pnl_dollars) >= -tolerance,
        "NO_STRICTLY_POSITIVE_STATE": max(state_pnl_dollars) > tolerance,
    }
    reasons.extend(code for code, passed in gates.items() if not passed)
    hard_no_trade_flags = {
        RiskFlag.UNSYNCHRONIZED_QUOTES,
        RiskFlag.INDICATIVE_ONLY,
        RiskFlag.COARSE_PRECISION,
        RiskFlag.CROSSED_MARKET,
        RiskFlag.MARKET_CLOSED,
        RiskFlag.INSUFFICIENT_DEPTH,
        RiskFlag.SETTLEMENT_MISMATCH,
        RiskFlag.POSITION_LIMIT,
        RiskFlag.CAPITAL_LIMIT,
    }
    hard_gate_failed = not (
        executable
        and all_outcomes_modeled
        and settlement_compatible
        and depth_sufficient
        and integer_sizing
        and position_limits_satisfied
        and capital_sufficient
    ) or bool(risk_flags & hard_no_trade_flags)
    if all(gates.values()) and not hard_gate_failed:
        label = TradeClassification.TRUE_ARBITRAGE
    elif hard_gate_failed:
        label = TradeClassification.NO_TRADE
    elif min(state_pnl_dollars) >= -tolerance and expected_value_dollars > ev_hurdle_dollars:
        label = TradeClassification.NEAR_ARBITRAGE
    elif expected_value_dollars > ev_hurdle_dollars and min(state_pnl_dollars) < -tolerance:
        label = TradeClassification.RELATIVE_VALUE
    else:
        label = TradeClassification.NO_TRADE
        if expected_value_dollars <= ev_hurdle_dollars:
            reasons.append("EV_BELOW_HURDLE")
    return ClassificationResult(
        label=label,
        reason_codes=sorted(set(reasons)),
        risk_flags=risk_flags,
        worst_case_pnl_dollars=worst,
        expected_value_dollars=expected_value_dollars,
    )
