import pytest

from fomc_basis.math.fed_funds import (
    FULL_MONTH_DV01_DOLLARS,
    equivalent_probability_25bp,
    event_dv01_dollars,
    event_move_value_dollars,
    expected_move_bp,
    expected_post_meeting_effr_pct,
    implied_monthly_effr_pct,
    quote_probability_sensitivity,
)


def test_price_to_monthly_rate() -> None:
    assert implied_monthly_effr_pct(96.26) == pytest.approx(3.74)


def test_september_post_meeting_decomposition() -> None:
    result = expected_post_meeting_effr_pct(96.26, 30, 16, 14, 3.63)
    assert result == pytest.approx(3.865714285714)
    assert expected_move_bp(result, 3.63) == pytest.approx(23.5714285714)
    assert equivalent_probability_25bp(expected_move_bp(result, 3.63)) == pytest.approx(
        0.942857142856
    )


def test_no_rounding_in_decomposition() -> None:
    bid = expected_post_meeting_effr_pct(96.2600, 30, 16, 14, 3.63)
    ask = expected_post_meeting_effr_pct(96.2625, 30, 16, 14, 3.63)
    assert ask < bid
    assert bid - ask == pytest.approx(0.005357142857)


def test_final_day_cannot_identify_post_rate() -> None:
    with pytest.raises(ValueError):
        expected_post_meeting_effr_pct(96, 30, 30, 0, 3.5)


def test_dv01_and_event_exposure() -> None:
    assert pytest.approx(41.6666666667) == FULL_MONTH_DV01_DOLLARS
    assert event_dv01_dollars(30, 14) == pytest.approx(19.4444444444)
    assert event_move_value_dollars(25, 30, 14) == pytest.approx(486.111111111)


def test_quote_sensitivity() -> None:
    assert quote_probability_sensitivity(30, 14) == pytest.approx(-30 / (14 * 0.25))
