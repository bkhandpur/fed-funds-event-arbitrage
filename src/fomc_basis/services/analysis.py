from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from ..enums import RiskFlag
from ..fees import KalshiFeeSchedule
from ..math.arbitrage import classify
from ..math.expected_value import binary_ev_per_contract, break_even_probability
from ..math.fed_funds import (
    equivalent_probability_25bp,
    event_move_value_dollars,
    expected_move_bp,
    expected_post_meeting_effr_pct,
    quote_probability_sensitivity,
)
from ..math.hedge import integer_hedges
from ..math.payoff import state_payoff_table
from ..math.probabilities import probability_bounds, three_state_with_tail


def analyze_september_2026_fixture(
    kalshi_contracts: int = 500, tail_50_probability: float = 0.02
) -> dict[str, Any]:
    """Reproducible, historical/user-supplied, non-synchronized case study."""
    days, pre, post = 30, 16, 14
    effr = 3.63
    futures_bid, futures_ask = 96.2600, 96.2625
    futures_mid = (futures_bid + futures_ask) / 2

    def implied(price: float) -> dict[str, float]:
        post_effr = expected_post_meeting_effr_pct(price, days, pre, post, effr)
        move = expected_move_bp(post_effr, effr)
        return {
            "futures_price_points": price,
            "post_meeting_effr_pct": post_effr,
            "expected_move_bp": move,
            "25_BP_EQUIVALENT_PROBABILITY": equivalent_probability_25bp(move),
        }

    scenarios = {
        "bid": implied(futures_bid),
        "ask": implied(futures_ask),
        "midpoint": implied(futures_mid),
    }
    q_low = scenarios["ask"]["25_BP_EQUIVALENT_PROBABILITY"]
    q_high = scenarios["bid"]["25_BP_EQUIVALENT_PROBABILITY"]
    q_mid = scenarios["midpoint"]["25_BP_EQUIVALENT_PROBABILITY"]
    adjusted = three_state_with_tail(q_mid, tail_50_probability)
    bounds = probability_bounds(
        [-50, -25, 0, 25, 50, 75],
        scenarios["midpoint"]["expected_move_bp"],
        {50: (0, 0.02), 75: (0, 0.01), -50: (0, 0.01), -25: (0, 0.02)},
    )
    schedule = KalshiFeeSchedule()
    fee_total = schedule.order_fee(kalshi_contracts, Decimal("0.88"))
    p25 = adjusted.probabilities[1]
    ev_per = binary_ev_per_contract(p25, Decimal("0.88"), fee_total / kalshi_contracts)
    hedges = integer_hedges(kalshi_contracts, days, post)
    nearest = hedges["nearest"]
    payoff = state_payoff_table(
        [-50, -25, 0, 25, 50, 75],
        kalshi_outcome_bp=25,
        kalshi_side="yes",
        kalshi_contracts=kalshi_contracts,
        kalshi_entry_price_dollars=0.88,
        kalshi_total_fees_dollars=float(fee_total),
        futures_contracts=nearest.futures_contracts,
        futures_entry_price_points=futures_ask,
        futures_round_trip_cost_per_contract_dollars=6.04,
        pre_effr_pct=effr,
        days_in_month=days,
        pre_days=pre,
        post_days=post,
        futures_margin_per_contract_dollars=2000,
    )
    state_pnl = [row.combined_net_pnl_dollars for row in payoff]
    basis_stress: list[float] = []
    for basis_bp in (-2, -1, 0, 1, 2):
        rows = state_payoff_table(
            [-50, -25, 0, 25, 50, 75],
            kalshi_outcome_bp=25,
            kalshi_side="yes",
            kalshi_contracts=kalshi_contracts,
            kalshi_entry_price_dollars=0.88,
            kalshi_total_fees_dollars=float(fee_total),
            futures_contracts=nearest.futures_contracts,
            futures_entry_price_points=futures_ask,
            futures_round_trip_cost_per_contract_dollars=6.04,
            pre_effr_pct=effr,
            days_in_month=days,
            pre_days=pre,
            post_days=post,
            futures_margin_per_contract_dollars=2000,
            basis_change_bp=basis_bp,
        )
        basis_stress.extend(row.combined_net_pnl_dollars for row in rows)
    risk_flags = {
        RiskFlag.INDICATIVE_ONLY,
        RiskFlag.STALE_QUOTE,
        RiskFlag.UNSYNCHRONIZED_QUOTES,
        RiskFlag.SETTLEMENT_MISMATCH,
        RiskFlag.TAIL_RISK,
        RiskFlag.BASIS_RISK,
        RiskFlag.TRANSACTION_COSTS,
        RiskFlag.INTEGER_HEDGE,
        RiskFlag.ASYNC_EXECUTION,
    }
    classification = classify(
        state_pnl,
        float(ev_per) * kalshi_contracts,
        risk_flags,
        executable=False,
        all_outcomes_modeled=True,
        settlement_compatible=False,
        depth_sufficient=False,
        integer_sizing=True,
        basis_stress_pnl_dollars=basis_stress,
    )
    hedge_payload = {
        "continuous": hedges["continuous"],
        "floor": {
            "futures_contracts": hedges["floor"].futures_contracts,
            "residual_dollars": hedges["floor"].residual_dollars,
        },
        "nearest": {
            "futures_contracts": hedges["nearest"].futures_contracts,
            "residual_dollars": hedges["nearest"].residual_dollars,
        },
        "ceiling": {
            "futures_contracts": hedges["ceiling"].futures_contracts,
            "residual_dollars": hedges["ceiling"].residual_dollars,
        },
    }
    return {
        "fixture": "september_2026",
        "observation_kind": "HISTORICAL_USER_SUPPLIED_FIXTURE",
        "analysis_timestamp": datetime.now(UTC).isoformat(),
        "warning": "Not necessarily synchronized or executable; displayed >25 probability is not used as a quote.",
        "day_count": {"days_in_month": days, "pre_decision_days": pre, "post_decision_days": post},
        "inputs": {
            "futures_symbol": "ZQU26.CBT",
            "futures_bid": futures_bid,
            "futures_ask": futures_ask,
            "kalshi_yes_bid": 0.87,
            "kalshi_yes_ask": 0.88,
            "effr_pct": effr,
        },
        "futures_implied": scenarios,
        "25_BP_EQUIVALENT_PROBABILITY_INTERVAL": [q_low, q_high],
        "quote_sensitivity": {
            "dq_d_futures_price_point": quote_probability_sensitivity(days, post),
            "one_minimum_tick_probability_change": abs(
                quote_probability_sensitivity(days, post) * 0.0025
            ),
            "current_spread_probability_change": abs(
                quote_probability_sensitivity(days, post) * (futures_ask - futures_bid)
            ),
            "one_bp_no_change_effr_probability_change": 0.01 / 0.25,
        },
        "tail_adjustment": {
            "p50": tail_50_probability,
            "p25": p25,
            "identity": "q25 = p25 + 2*p50",
        },
        "probability_bounds": {str(state): interval for state, interval in bounds.items()},
        "fees": {
            "total_dollars": float(fee_total),
            "per_contract_dollars": float(fee_total / kalshi_contracts),
        },
        "expected_value": {
            "yes_per_contract_dollars": float(ev_per),
            "yes_order_dollars": float(ev_per) * kalshi_contracts,
            "break_even_probability": break_even_probability(
                Decimal("0.88"), fee_total / kalshi_contracts
            ),
        },
        "event_25bp_value_per_future_dollars": event_move_value_dollars(25, days, post),
        "hedge": hedge_payload,
        "state_payoffs": [row.as_dict() for row in payoff],
        "classification": classification.model_dump(mode="json"),
        "conclusion": "The supplied observation is not a proven true arbitrage.",
    }
