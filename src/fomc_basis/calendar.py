from __future__ import annotations

from datetime import date, timedelta

from .models import FOMCMeeting


def next_meeting(meetings: list[FOMCMeeting], as_of: date | None = None) -> FOMCMeeting:
    as_of = as_of or date.today()
    future = [meeting for meeting in meetings if meeting.decision_date >= as_of]
    if not future:
        raise LookupError("no scheduled FOMC meeting on or after the requested date")
    return min(future, key=lambda meeting: meeting.decision_date)


def manual_meeting(start_date: date, decision_date: date) -> FOMCMeeting:
    return FOMCMeeting(
        start_date=start_date,
        decision_date=decision_date,
        effective_date=decision_date + timedelta(days=1),
        source="manual override",
    )
