from __future__ import annotations

from datetime import date
from typing import Any

from ..storage import Database


def save_analysis_run(database: Database, result: dict[str, Any]) -> dict[str, str]:
    """Persist one auditable analysis and its normalized research outputs."""
    timestamp = str(result["analysis_timestamp"])
    run_id = database.insert("model_runs", result, analysis_timestamp=timestamp)
    probability_payload = result.get(
        "probability_model",
        {
            "bounds": result.get("probability_bounds", {}),
            "tail_adjustment": result.get("tail_adjustment", {}),
        },
    )
    probability_id = database.insert(
        "inferred_probability_distributions",
        probability_payload,
        model_run_id=run_id,
    )
    candidate_payload = {
        "classification": result.get("classification", {}),
        "expected_value": result.get("expected_value", {}),
        "chosen_side": result.get("chosen_side"),
        "hedge": result.get("hedge", {}),
    }
    candidate_id = database.insert("trade_candidates", candidate_payload, model_run_id=run_id)
    payoff_id = database.insert(
        "state_payoff_results",
        {
            "state_payoffs": result.get("state_payoffs", []),
            "basis_stress": result.get("basis_stress", []),
        },
        model_run_id=run_id,
    )
    return {
        "model_run_id": run_id,
        "probability_distribution_id": probability_id,
        "trade_candidate_id": candidate_id,
        "state_payoff_result_id": payoff_id,
    }


def save_realized_outcome(database: Database, decision_date: date, realized_move_bp: int) -> str:
    payload = {
        "decision_date": decision_date.isoformat(),
        "realized_move_bp": realized_move_bp,
    }
    return database.insert(
        "realized_fomc_outcomes", payload, decision_date=decision_date.isoformat()
    )


def save_realized_settlement(
    database: Database, symbol: str, settlement_date: date, settlement_price_points: float
) -> str:
    payload = {
        "symbol": symbol,
        "settlement_date": settlement_date.isoformat(),
        "settlement_price_points": settlement_price_points,
    }
    return database.insert(
        "realized_futures_settlements",
        payload,
        symbol=symbol,
        settlement_date=settlement_date.isoformat(),
    )
