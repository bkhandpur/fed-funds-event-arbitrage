from __future__ import annotations

import re
from calendar import month_name
from datetime import date, timedelta
from html import unescape
from pathlib import Path

import httpx

from ..models import FOMCMeeting
from .base import CalendarProvider


class FederalReserveCalendarProvider(CalendarProvider):
    URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"

    def __init__(self, cache_path: str | Path | None = None, timeout_seconds: float = 10):
        self.cache_path = Path(cache_path) if cache_path else None
        self.timeout_seconds = timeout_seconds

    def meetings(self, year: int) -> list[FOMCMeeting]:
        html = self._load_html()
        text = " ".join(unescape(re.sub(r"<[^>]+>", " ", html)).split())
        year_heading = re.search(rf"{year}\s+FOMC\s+Meetings", text, re.I)
        if year_heading:
            following = text[year_heading.end() :]
            next_heading = re.search(r"\d{4}\s+FOMC\s+Meetings", following, re.I)
            text = following[: next_heading.start()] if next_heading else following
        names = "|".join(month_name[1:])
        aliases = {name.lower(): index for index, name in enumerate(month_name) if name}
        aliases.update({name[:3].lower(): index for index, name in enumerate(month_name) if name})
        pattern = re.compile(
            rf"({names}|[A-Z][a-z]{{2}}/[A-Z][a-z]{{2}})\s+"
            r"(\d{1,2})\s*[-–]\s*(\d{1,2})\*?",
            re.I,
        )
        results = []
        for month_label, start_day, decision_day in pattern.findall(text):
            labels = month_label.split("/")
            start_month = aliases[labels[0].lower()]
            decision_month = aliases[labels[-1].lower()]
            decision_year = year + int(decision_month < start_month)
            start = date(year, start_month, int(start_day))
            decision = date(decision_year, decision_month, int(decision_day))
            results.append(
                FOMCMeeting(
                    start_date=start,
                    decision_date=decision,
                    effective_date=decision + timedelta(days=1),
                )
            )
        unique = {meeting.decision_date: meeting for meeting in results}
        if not unique:
            raise RuntimeError(
                "Federal Reserve calendar markup was not recognized; use cached calendar or manual override"
            )
        return sorted(unique.values(), key=lambda item: item.decision_date)

    def _load_html(self) -> str:
        if self.cache_path and self.cache_path.exists():
            return self.cache_path.read_text(encoding="utf-8")
        response = httpx.get(self.URL, timeout=self.timeout_seconds, follow_redirects=True)
        response.raise_for_status()
        return response.text
