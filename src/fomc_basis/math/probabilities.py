from __future__ import annotations

import numpy as np
from scipy.optimize import minimize
from scipy.special import xlogy

from ..models import ProbabilityResult


def two_state_distribution(
    expected_move_bp: float, low_state_bp: int = 0, high_state_bp: int = 25
) -> ProbabilityResult:
    if low_state_bp == high_state_bp:
        raise ValueError("two states must differ")
    high_probability = (expected_move_bp - low_state_bp) / (high_state_bp - low_state_bp)
    warnings: list[str] = []
    status = "ok"
    if not 0 <= high_probability <= 1:
        status = "infeasible"
        warnings.append("two-state probability lies outside [0, 1]")
    return ProbabilityResult(
        states_bp=[low_state_bp, high_state_bp],
        probabilities=[1 - high_probability, high_probability],
        status=status,
        residuals={"expected_move_bp": 0.0},
        warnings=warnings,
    )


def three_state_with_tail(
    equivalent_probability_25bp: float, tail_50_probability: float
) -> ProbabilityResult:
    p25 = equivalent_probability_25bp - 2 * tail_50_probability
    p0 = 1 - p25 - tail_50_probability
    warnings = []
    if min(p0, p25, tail_50_probability) < 0:
        warnings.append("specified +50 bp tail is inconsistent with a nonnegative distribution")
    return ProbabilityResult(
        states_bp=[0, 25, 50],
        probabilities=[p0, p25, tail_50_probability],
        status="ok" if not warnings else "infeasible",
        warnings=warnings,
    )


def _constraints(states: np.ndarray, expected_move_bp: float) -> tuple[np.ndarray, np.ndarray]:
    return np.vstack([np.ones(len(states)), states]), np.array([1.0, expected_move_bp])


def probability_bounds(
    states_bp: list[int],
    expected_move_bp: float,
    state_bounds: dict[int, tuple[float, float]] | None = None,
) -> dict[int, tuple[float, float]]:
    """LP bounds given normalization, nonnegativity, expectation, and optional tails."""
    from scipy.optimize import linprog

    states = np.asarray(states_bp, dtype=float)
    a_eq, b_eq = _constraints(states, expected_move_bp)
    supplied = state_bounds or {}
    bounds = [supplied.get(int(state), (0.0, 1.0)) for state in states]
    result: dict[int, tuple[float, float]] = {}
    for index, state in enumerate(states):
        objective = np.zeros(len(states))
        objective[index] = 1.0
        minimum = linprog(objective, A_eq=a_eq, b_eq=b_eq, bounds=bounds, method="highs")
        maximum = linprog(-objective, A_eq=a_eq, b_eq=b_eq, bounds=bounds, method="highs")
        if not minimum.success or not maximum.success:
            raise ValueError("probability constraints are infeasible")
        result[int(state)] = (float(minimum.fun), float(-maximum.fun))
    return result


def subset_probability_bounds(
    states_bp: list[int],
    expected_move_bp: float,
    subset_states_bp: list[int],
    state_bounds: dict[int, tuple[float, float]] | None = None,
) -> tuple[float, float]:
    """LP bounds for the joint probability of a set of mutually exclusive states."""
    from scipy.optimize import linprog

    if not subset_states_bp or any(state not in states_bp for state in subset_states_bp):
        raise ValueError("subset states must be a non-empty subset of the state grid")
    states = np.asarray(states_bp, dtype=float)
    a_eq, b_eq = _constraints(states, expected_move_bp)
    supplied = state_bounds or {}
    variable_bounds = [supplied.get(int(state), (0.0, 1.0)) for state in states]
    objective = np.asarray([1.0 if int(state) in subset_states_bp else 0.0 for state in states])
    minimum = linprog(objective, A_eq=a_eq, b_eq=b_eq, bounds=variable_bounds, method="highs")
    maximum = linprog(-objective, A_eq=a_eq, b_eq=b_eq, bounds=variable_bounds, method="highs")
    if not minimum.success or not maximum.success:
        raise ValueError("probability constraints are infeasible")
    return float(minimum.fun), float(-maximum.fun)


def regularized_distribution(
    states_bp: list[int],
    expected_move_bp: float,
    prior: list[float],
    state_bounds: dict[int, tuple[float, float]] | None = None,
) -> ProbabilityResult:
    """Select a model-dependent distribution by minimum KL divergence to a prior."""
    states = np.asarray(states_bp, dtype=float)
    prior_array = np.asarray(prior, dtype=float)
    if len(states) != len(prior_array) or np.any(prior_array <= 0):
        raise ValueError("prior must be positive and match the state grid")
    prior_array /= prior_array.sum()
    bounds_map = state_bounds or {}
    bounds = [bounds_map.get(int(state), (1e-12, 1.0)) for state in states]
    constraints = [
        {"type": "eq", "fun": lambda p: np.sum(p) - 1.0},
        {"type": "eq", "fun": lambda p: float(p @ states) - expected_move_bp},
    ]

    def kl(probabilities: np.ndarray) -> float:
        return float(np.sum(xlogy(probabilities, probabilities / prior_array)))

    optimized = minimize(
        kl,
        prior_array,
        method="SLSQP",
        bounds=bounds,
        constraints=constraints,
        options={"ftol": 1e-12, "maxiter": 2000},
    )
    if not optimized.success:
        raise ValueError(f"regularized reconciliation failed: {optimized.message}")
    probabilities = np.asarray(optimized.x)
    return ProbabilityResult(
        states_bp=states_bp,
        probabilities=probabilities.tolist(),
        bounds=probability_bounds(states_bp, expected_move_bp, state_bounds),
        prior=prior_array.tolist(),
        status="optimal",
        residuals={
            "sum": float(probabilities.sum() - 1),
            "expected_move_bp": float(probabilities @ states - expected_move_bp),
        },
        warnings=["Selected distribution is model-dependent; futures alone do not identify it."],
    )
