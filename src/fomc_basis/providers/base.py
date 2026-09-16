from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from ..models import FOMCMeeting, KalshiMarket, Quote


class FuturesQuoteProvider(ABC):
    @abstractmethod
    def quote(self, year: int, month: int) -> Quote: ...


class EventMarketProvider(ABC):
    @abstractmethod
    def discover(self, decision_date: date | None = None) -> list[KalshiMarket]: ...


class EFFRProvider(ABC):
    @abstractmethod
    def latest_effr_pct(self) -> tuple[date, float, dict]: ...


class CalendarProvider(ABC):
    @abstractmethod
    def meetings(self, year: int) -> list[FOMCMeeting]: ...
