from datetime import date

import pytest

from fomc_basis.services.curve_analysis import analyze_curve


def test_curve_analysis_reports_identification_and_contamination() -> None:
    result = analyze_curve(
        {(2026, 9): 96.26, (2026, 10): 96.10},
        [date(2026, 9, 17), date(2026, 10, 29)],
        3.63,
    )
    assert result["fully_identified"] is True
    assert result["method"] == "exact"
    assert result["contracts_with_multiple_meetings"] == ["2026-10"]
    assert len(result["expected_meeting_moves_bp"]) == 2
    assert result["residuals_bp"] == pytest.approx([0, 0])


def test_curve_analysis_reports_underdetermined_meetings() -> None:
    result = analyze_curve(
        {(2026, 10): 96.10},
        [date(2026, 9, 17), date(2026, 10, 29)],
        3.63,
    )
    assert result["fully_identified"] is False
    assert len(result["unidentified_meetings"]) == 2
