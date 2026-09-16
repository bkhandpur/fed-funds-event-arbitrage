from decimal import Decimal

import pytest

from fomc_basis.fees import KalshiFeeSchedule
from fomc_basis.math.expected_value import (
    binary_ev_per_contract,
    break_even_probability,
    price_for_ev_hurdle,
)
from fomc_basis.math.hedge import integer_hedges
from fomc_basis.math.payoff import state_payoff_table


def test_aggregate_fee_rounding() -> None:
    fee = KalshiFeeSchedule().order_fee(500, Decimal("0.88"))
    assert fee == Decimal("3.70")
    assert fee != KalshiFeeSchedule().order_fee(1, Decimal("0.88")) * 500


def test_fee_zero_override_and_validation() -> None:
    schedule = KalshiFeeSchedule()
    assert schedule.order_fee(10, Decimal("0.5"), zero_fee=True) == 0
    with pytest.raises(ValueError):
        schedule.order_fee(1, Decimal("1.1"))


def test_yes_and_no_expected_value() -> None:
    assert binary_ev_per_contract(0.9, Decimal("0.88"), side="yes") == Decimal("0.02")
    assert binary_ev_per_contract(0.1, Decimal("0.88"), side="no") == Decimal("0.02")
    assert break_even_probability(Decimal("0.88"), Decimal("0.01")) == pytest.approx(0.89)
    assert price_for_ev_hurdle(0.94, 0.02, 0.01) == pytest.approx(0.91)


def test_hedge_sign_and_integer_residuals() -> None:
    choices = integer_hedges(500, 30, 14)
    assert choices["continuous"] > 0
    assert choices["nearest"].futures_contracts == 1
    assert choices["floor"].futures_contracts == 1
    assert choices["ceiling"].futures_contracts == 2


def test_long_futures_enters_at_ask_and_loses_on_hike() -> None:
    rows = state_payoff_table(
        [0, 25],
        kalshi_outcome_bp=25,
        kalshi_side="yes",
        kalshi_contracts=0,
        kalshi_entry_price_dollars=0,
        kalshi_total_fees_dollars=0,
        futures_contracts=1,
        futures_entry_price_points=96.2625,
        futures_round_trip_cost_per_contract_dollars=0,
        pre_effr_pct=3.63,
        days_in_month=30,
        pre_days=16,
        post_days=14,
        futures_margin_per_contract_dollars=2000,
    )
    assert rows[1].futures_gross_pnl_dollars < rows[0].futures_gross_pnl_dollars


def test_short_futures_sign() -> None:
    rows = state_payoff_table(
        [0, 25],
        kalshi_outcome_bp=25,
        kalshi_side="yes",
        kalshi_contracts=0,
        kalshi_entry_price_dollars=0,
        kalshi_total_fees_dollars=0,
        futures_contracts=-1,
        futures_entry_price_points=96.26,
        futures_round_trip_cost_per_contract_dollars=0,
        pre_effr_pct=3.63,
        days_in_month=30,
        pre_days=16,
        post_days=14,
        futures_margin_per_contract_dollars=2000,
    )
    assert rows[1].futures_gross_pnl_dollars > rows[0].futures_gross_pnl_dollars


def test_multi_state_kalshi_bucket_settles_in_every_winning_state() -> None:
    rows = state_payoff_table(
        [25, 50, 75],
        kalshi_outcome_bp=50,
        kalshi_winning_states_bp=[50, 75],
        kalshi_side="yes",
        kalshi_contracts=1,
        kalshi_entry_price_dollars=0.2,
        kalshi_total_fees_dollars=0,
        futures_contracts=0,
        futures_entry_price_points=96,
        futures_round_trip_cost_per_contract_dollars=0,
        pre_effr_pct=3.5,
        days_in_month=30,
        pre_days=15,
        post_days=15,
        futures_margin_per_contract_dollars=0,
    )
    assert [row.kalshi_settlement_dollars for row in rows] == [0, 1, 1]
