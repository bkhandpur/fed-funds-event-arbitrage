from __future__ import annotations

FULL_MONTH_DV01_DOLLARS = 5_000_000 * (30 / 360) * 0.0001
PRICE_POINT_VALUE_DOLLARS = FULL_MONTH_DV01_DOLLARS * 100


def implied_monthly_effr_pct(futures_price_points: float) -> float:
    return 100.0 - futures_price_points


def expected_post_meeting_effr_pct(
    futures_price_points: float,
    days_in_month: int,
    pre_decision_days: int,
    post_decision_days: int,
    pre_effr_pct: float,
) -> float:
    if days_in_month != pre_decision_days + post_decision_days:
        raise ValueError("pre and post days must sum to calendar days in month")
    if post_decision_days <= 0:
        raise ValueError("post-decision rate is not identified when there are no post days")
    monthly = implied_monthly_effr_pct(futures_price_points)
    return (days_in_month * monthly - pre_decision_days * pre_effr_pct) / post_decision_days


def expected_move_bp(post_effr_pct: float, no_change_effr_pct: float) -> float:
    return (post_effr_pct - no_change_effr_pct) * 100.0


def equivalent_probability_25bp(expected_move_bp_value: float) -> float:
    """25_BP_EQUIVALENT_PROBABILITY; not necessarily P(exactly +25 bp)."""
    return expected_move_bp_value / 25.0


def quote_probability_sensitivity(days_in_month: int, post_decision_days: int) -> float:
    """dq/dF in probability units per futures price point."""
    if post_decision_days <= 0:
        raise ValueError("post_decision_days must be positive")
    return -days_in_month / (post_decision_days * 0.25)


def event_dv01_dollars(days_in_month: int, post_decision_days: int) -> float:
    return FULL_MONTH_DV01_DOLLARS * post_decision_days / days_in_month


def event_move_value_dollars(move_bp: float, days_in_month: int, post_decision_days: int) -> float:
    return event_dv01_dollars(days_in_month, post_decision_days) * move_bp


def futures_settlement_price(
    pre_effr_pct: float,
    move_bp: float,
    days_in_month: int,
    pre_days: int,
    post_days: int,
    basis_change_bp: float = 0.0,
) -> float:
    post = pre_effr_pct + (move_bp + basis_change_bp) / 100.0
    monthly = (pre_days * pre_effr_pct + post_days * post) / days_in_month
    return 100.0 - monthly
