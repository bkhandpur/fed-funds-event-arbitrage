from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

FULL_MONTH_DV01_DOLLARS = 5_000_000 * (30 / 360) * 0.0001
PRICE_POINT_VALUE_DOLLARS = 4167.0
# FULL_MONTH_DV01 remains the continuous deposit approximation. Settlement uses 4167.


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
    no_change_effr_pct: float | None = None,
) -> float:
    if days_in_month != pre_days + post_days or days_in_month <= 0:
        raise ValueError("pre and post days must sum to the settlement month")
    pre = Decimal(str(pre_effr_pct))
    base = pre if no_change_effr_pct is None else Decimal(str(no_change_effr_pct))
    post = base + (Decimal(str(move_bp)) + Decimal(str(basis_change_bp))) / Decimal(100)
    monthly = (pre_days * pre + post_days * post) / days_in_month
    return exact_settlement_price(monthly)


def exact_settlement_price(monthly_effr_pct: Decimal | float) -> float:
    rate = Decimal(str(monthly_effr_pct))
    if not rate.is_finite():
        raise ValueError("monthly rate must be finite")
    return float(Decimal(100) - rate.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))


def published_calendar_average(
    rates: dict[date, tuple[Decimal, datetime] | None],
    first: date,
    last: date,
    available_at: datetime,
) -> Decimal:
    """Average calendar rates with publication-time filtering and prior carry.

    Mapping values are (rate Decimal, publication datetime). Weekends and
    holidays carry the last preceding published rate. A missing business-day
    observation is not silently replaced. The caller supplies the exact set of
    publication-free dates in the mapping as None, avoiding an invented history.
    """
    from datetime import timedelta

    values = []
    current = None
    for effective, observation in sorted(rates.items()):
        if effective < first and observation is not None and observation[1] <= available_at:
            current = observation[0]
    day = first
    while day <= last:
        if day not in rates:
            raise ValueError("missing daily EFFR observation or publication-free designation")
        observation = rates[day]
        if observation is not None:
            rate, published = observation
            if published > available_at:
                raise ValueError("daily EFFR has not yet been published")
            current = rate
        if current is None:
            raise ValueError("no preceding published EFFR available for carry")
        values.append(Decimal(str(current)))
        day += timedelta(days=1)
    return sum(values, Decimal(0)) / len(values)
