from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date

import numpy as np


@dataclass(frozen=True)
class CurveSolution:
    expected_moves_bp: np.ndarray
    rank: int
    condition_number: float
    residuals_bp: np.ndarray
    method: str
    warnings: tuple[str, ...]


def meeting_weight(contract_year: int, contract_month: int, effective_date: date) -> float:
    """Calendar-month fraction affected by a meeting, including its effective day."""
    month_start = date(contract_year, contract_month, 1)
    days = calendar.monthrange(contract_year, contract_month)[1]
    if effective_date <= month_start:
        return 1.0
    if effective_date.year != contract_year or effective_date.month != contract_month:
        return 0.0
    return (days - effective_date.day + 1) / days


def design_matrix(
    contract_months: list[tuple[int, int]], meeting_effective_dates: list[date]
) -> np.ndarray:
    return np.asarray(
        [
            [meeting_weight(year, month, meeting) for meeting in meeting_effective_dates]
            for year, month in contract_months
        ],
        dtype=float,
    )


def solve_curve(
    weights: np.ndarray, monthly_move_bp: np.ndarray, ridge_alpha: float = 1e-8
) -> CurveSolution:
    weights = np.asarray(weights, dtype=float)
    values = np.asarray(monthly_move_bp, dtype=float)
    if weights.shape[0] != len(values):
        raise ValueError("one monthly value is required per contract row")
    rank = int(np.linalg.matrix_rank(weights))
    condition = float(np.linalg.cond(weights))
    warnings: list[str] = []
    columns = weights.shape[1]
    if rank < columns:
        warnings.append("curve is underdetermined; some meeting moves cannot be separated")
        method = "ridge"
        estimate = np.linalg.solve(
            weights.T @ weights + ridge_alpha * np.eye(columns), weights.T @ values
        )
    elif weights.shape[0] == columns and condition < 1e8:
        method = "exact"
        estimate = np.linalg.solve(weights, values)
    elif condition >= 1e8:
        warnings.append("design matrix is ill-conditioned; ridge regularization applied")
        method = "ridge"
        estimate = np.linalg.solve(
            weights.T @ weights + ridge_alpha * np.eye(columns), weights.T @ values
        )
    else:
        method = "least_squares"
        estimate = np.linalg.lstsq(weights, values, rcond=None)[0]
    return CurveSolution(
        estimate, rank, condition, values - weights @ estimate, method, tuple(warnings)
    )
