from datetime import date

import numpy as np
import pytest

from fomc_basis.math.curve import design_matrix, meeting_weight, solve_curve
from fomc_basis.math.probabilities import (
    probability_bounds,
    regularized_distribution,
    subset_probability_bounds,
    three_state_with_tail,
    two_state_distribution,
)


def test_two_state_probability() -> None:
    result = two_state_distribution(20, 0, 25)
    assert result.probabilities == pytest.approx([0.2, 0.8])


def test_two_state_out_of_range_warning() -> None:
    result = two_state_distribution(30, 0, 25)
    assert result.status == "infeasible"
    assert result.warnings


def test_50bp_tail_adjustment() -> None:
    result = three_state_with_tail(0.94, 0.02)
    assert result.probabilities == pytest.approx([0.08, 0.90, 0.02])


def test_probability_bounds() -> None:
    bounds = probability_bounds([0, 25, 50], 23.5, {50: (0, 0.02)})
    assert bounds[25][0] == pytest.approx(0.90)
    assert bounds[25][1] == pytest.approx(0.94)


def test_probability_bounds_for_multi_state_event_are_jointly_optimized() -> None:
    lower, upper = subset_probability_bounds([0, 25, 50, 75], 30, [50, 75])
    assert 0 <= lower <= upper <= 1
    assert upper == pytest.approx(0.6)


def test_regularized_distribution_is_feasible_and_labeled() -> None:
    result = regularized_distribution([0, 25, 50], 20, [0.3, 0.6, 0.1])
    assert sum(result.probabilities) == pytest.approx(1)
    assert np.dot(result.states_bp, result.probabilities) == pytest.approx(20)
    assert "model-dependent" in result.warnings[0]


def test_meeting_weights_include_multiple_meetings() -> None:
    contracts = [(2026, 9), (2026, 10)]
    meetings = [date(2026, 9, 17), date(2026, 10, 29)]
    matrix = design_matrix(contracts, meetings)
    assert matrix[0, 0] == pytest.approx(14 / 30)
    assert matrix[0, 1] == 0
    assert matrix[1, 1] == pytest.approx(3 / 31)


def test_curve_exact_and_underdetermined_diagnostics() -> None:
    exact = solve_curve(np.eye(2), np.array([10.0, 20.0]))
    assert exact.method == "exact"
    assert exact.expected_moves_bp == pytest.approx([10, 20])
    under = solve_curve(np.array([[0.5, 0.2]]), np.array([10.0]))
    assert under.rank == 1
    assert under.warnings


def test_prior_meeting_fully_affects_later_month() -> None:
    assert meeting_weight(2026, 10, date(2026, 9, 17)) == 1.0
