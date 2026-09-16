from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np

from ..math.curve import design_matrix, solve_curve


def analyze_curve(
    contract_prices: dict[tuple[int, int], float],
    meeting_effective_dates: list[date],
    starting_effr_pct: float,
    monthly_basis_bp: dict[tuple[int, int], float] | None = None,
) -> dict[str, Any]:
    """Estimate sequential expected meeting moves from a ZQ futures curve."""
    if not contract_prices:
        raise ValueError("at least one futures contract is required")
    if not meeting_effective_dates:
        raise ValueError("at least one meeting effective date is required")
    contracts = sorted(contract_prices)
    meetings = sorted(meeting_effective_dates)
    weights = design_matrix(contracts, meetings)
    basis = monthly_basis_bp or {}
    monthly_moves = np.asarray(
        [
            (100.0 - contract_prices[contract] - starting_effr_pct) * 100.0
            - basis.get(contract, 0.0)
            for contract in contracts
        ]
    )
    solution = solve_curve(weights, monthly_moves)
    contaminated = [
        f"{year:04d}-{month:02d}"
        for (year, month), row in zip(contracts, weights, strict=True)
        if np.count_nonzero(np.abs(row) > 1e-12) > 1
    ]
    fully_identified = solution.rank == len(meetings)
    warnings = list(solution.warnings)
    if contaminated:
        warnings.append("some contracts contain exposure to more than one FOMC meeting")
    return {
        "contracts": [f"{year:04d}-{month:02d}" for year, month in contracts],
        "meeting_effective_dates": [item.isoformat() for item in meetings],
        "weight_matrix": weights.tolist(),
        "monthly_expected_move_bp": monthly_moves.tolist(),
        "expected_meeting_moves_bp": solution.expected_moves_bp.tolist(),
        "matrix_rank": solution.rank,
        "condition_number": solution.condition_number,
        "residuals_bp": solution.residuals_bp.tolist(),
        "method": solution.method,
        "fully_identified": fully_identified,
        "unidentified_meetings": []
        if fully_identified
        else [item.isoformat() for item in meetings],
        "contracts_with_multiple_meetings": contaminated,
        "warnings": warnings,
    }
