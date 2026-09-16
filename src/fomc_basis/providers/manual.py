from __future__ import annotations

from copy import deepcopy

from ..models import Quote
from .base import FuturesQuoteProvider


class ManualQuoteProvider(FuturesQuoteProvider):
    def __init__(self, quotes: dict[tuple[int, int], Quote]):
        self._quotes = deepcopy(quotes)

    def quote(self, year: int, month: int) -> Quote:
        try:
            return self._quotes[(year, month)]
        except KeyError as exc:
            raise LookupError(f"no manual quote for {year}-{month:02d}") from exc
