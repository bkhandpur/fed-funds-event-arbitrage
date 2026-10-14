from __future__ import annotations

from typing import Any

from ..models import AnalysisRequest
from .generic_analysis import analyze_request


def example_request(
    reference: bool = False, contracts: int = 500, tail: float = 0.02
) -> AnalysisRequest:
    day = "2026-09-15" if reference else "2026-09-16"
    effective = "2026-09-16" if reference else "2026-09-17"
    stamp = "2026-09-14T16:00:00+00:00"
    quote = {
        "source": "Synthetic reference A" if reference else "Historical user-supplied observation",
        "receipt_timestamp": stamp,
        "observation_kind": "HISTORICAL_USER_SUPPLIED_FIXTURE",
    }
    return AnalysisRequest.model_validate(
        {
            "meeting": {"start_date": day, "decision_date": day, "effective_date": effective},
            "analysis_timestamp": stamp,
            "futures_quote": {
                **quote,
                "instrument": "ZQU26.CBT",
                "bid": "96.435" if reference else "96.2600",
                "ask": "96.440" if reference else "96.2625",
            },
            "kalshi_yes_quote": {
                **quote,
                "instrument": "SYNTHETIC-PLUS25" if reference else "HISTORICAL-PLUS25",
                "bid": "0.47" if reference else "0.87",
                "ask": "0.48" if reference else "0.88",
            },
            "current_effr_pct": 3.5 if reference else 3.63,
            "pre_effective_effr_average_pct": 3.5 if reference else 3.63,
            "pre_effective_rate_assumption": "Synthetic flat rate"
            if reference
            else "Historical flat-rate assumption",
            "reference_rate_date": "2026-09-14",
            "kalshi_contracts": contracts,
            "selected_binary_side": "yes",
            "selected_futures_contracts": 1,
            "state_grid_bp": [0, 25] if reference else [-50, -25, 0, 25, 50, 75],
            "probability_mode": "two_state" if reference else "tails",
            "state_probability_bounds": {}
            if reference
            else {-50: [0, 0], -25: [0, 0], 50: [tail, tail], 75: [0, 0]},
            "kalshi_fee_schedule_name": "Synthetic cent-rounded reference"
            if reference
            else "Historical cent-rounded assumption",
            "kalshi_fee_rounding_dollars": "0.01",
            "all_outcomes_modeled": False,
        }
    )


def analyze_september_2026_fixture(
    kalshi_contracts: int = 500, tail_50_probability: float = 0.02
) -> dict[str, Any]:
    result = analyze_request(example_request(False, kalshi_contracts, tail_50_probability))
    result["fixture"] = "september_2026"
    result["observation_kind"] = "HISTORICAL_USER_SUPPLIED_FIXTURE"
    result["warning"] = "Historical flat-rate assumption with unavailable execution evidence"
    result["conclusion"] = (
        "Research scenario with execution unverified and not a proven true arbitrage."
    )
    return result
