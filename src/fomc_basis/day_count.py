from __future__ import annotations

import calendar as calendar_module
from datetime import date, timedelta

from .models import DayCount, FOMCMeeting


def meeting_day_count(decision_date: date, effective_date: date | None = None) -> DayCount:
    """Count calendar days at the pre/post policy regime in the decision month."""
    effective_date = effective_date or decision_date + timedelta(days=1)
    if effective_date < decision_date:
        raise ValueError("effective date cannot precede decision date")
    days = calendar_module.monthrange(decision_date.year, decision_date.month)[1]
    first = decision_date.replace(day=1)
    next_month = date(
        decision_date.year + (decision_date.month == 12), decision_date.month % 12 + 1, 1
    )
    effective_in_month = min(max(effective_date, first), next_month)
    pre = (effective_in_month - first).days
    return DayCount(
        decision_date=decision_date,
        effective_date=effective_date,
        days_in_month=days,
        pre_decision_days=pre,
        post_decision_days=days - pre,
    )


def meeting_affects_month(meeting: FOMCMeeting, year: int, month: int) -> bool:
    return meeting.effective_date.year == year and meeting.effective_date.month == month
