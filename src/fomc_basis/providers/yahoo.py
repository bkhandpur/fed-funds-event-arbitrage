from __future__ import annotations

import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from ..contracts import yahoo_zq_symbol
from ..enums import ObservationKind
from ..models import Quote
from .base import FuturesQuoteProvider


class YahooFinanceProvider(FuturesQuoteProvider):
    """Free, indicative Yahoo adapter. It never invents bid/ask from OHLC or last."""

    def __init__(self, timeout_seconds: float = 10, max_retries: int = 3):
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_retries < 1:
            raise ValueError("max_retries must be at least one")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.degraded_mode: str | None = None

    def quote(self, year: int, month: int) -> Quote:
        try:
            import yfinance as yf
        except ImportError as exc:  # pragma: no cover - installation issue
            raise RuntimeError("Install yfinance to use YahooFinanceProvider") from exc
        symbol = yahoo_zq_symbol(year, month)
        error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                ticker = yf.Ticker(symbol)
                history = ticker.history(period="5d", interval="1d", timeout=self.timeout_seconds)
                if history.empty or "Close" not in history or history["Close"].dropna().empty:
                    raise LookupError(
                        f"Yahoo did not recognize {symbol} or returned no valid history"
                    )
                last_row = history.dropna(subset=["Close"]).iloc[-1]
                timestamp = history.dropna(subset=["Close"]).index[-1].to_pydatetime()
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=UTC)
                raw_info: dict[str, Any] = {}
                try:
                    raw_info = ticker.fast_info
                except Exception:
                    self.degraded_mode = "history_polling_without_fast_info"
                bid = self._decimal(raw_info.get("bid"))
                ask = self._decimal(raw_info.get("ask"))
                last = self._decimal(raw_info.get("last_price")) or self._decimal(last_row["Close"])
                previous = self._decimal(
                    raw_info.get("previous_close", raw_info.get("previousClose"))
                )
                return Quote(
                    instrument=symbol,
                    source="Yahoo Finance / yfinance",
                    source_timestamp=timestamp.astimezone(UTC),
                    receipt_timestamp=datetime.now(UTC),
                    bid=bid,
                    ask=ask,
                    last=last,
                    previous_close=previous,
                    price_precision=Decimal("0.0025"),
                    delayed=True,
                    market_open=None,
                    observation_kind=ObservationKind.LIVE_INDICATIVE,
                    raw={
                        "history": {
                            key: self._json_value(value) for key, value in last_row.items()
                        },
                        "fast_info": dict(raw_info),
                        "degraded_mode": self.degraded_mode,
                    },
                )
            except Exception as exc:  # bounded exponential backoff
                error = exc
                if attempt + 1 < self.max_retries:
                    time.sleep(min(2**attempt, 4))
        raise RuntimeError(
            f"Yahoo quote failed for {symbol} after {self.max_retries} attempts: {error}"
        ) from error

    def stream(self, year: int, month: int) -> Quote:
        """Conservative streaming hook: visibly degrades to bounded polling."""
        self.degraded_mode = "websocket_unavailable_fell_back_to_polling"
        return self.quote(year, month)

    @staticmethod
    def _decimal(value: Any) -> Decimal | None:
        try:
            return None if value is None else Decimal(str(value))
        except Exception:
            return None

    @staticmethod
    def _json_value(value: Any) -> Any:
        return value.item() if hasattr(value, "item") else str(value)
