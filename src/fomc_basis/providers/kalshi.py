from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Any

import httpx

from ..models import KalshiMarket, OrderBookLevel
from .base import EventMarketProvider


def semantic_outcome(text: str) -> tuple[int | None, str | None]:
    """Map outcome prose to a representative state and explicit semantic bucket."""
    value = text.lower().replace("basis points", "bp").replace("bps", "bp")
    number_match = re.search(r"(\d+)\s*bp", value)
    number = int(number_match.group(1)) if number_match else None
    if "no change" in value or "unchanged" in value or "maintain" in value:
        return 0, "NO_CHANGE"
    cut = any(word in value for word in ("cut", "decrease", "lower"))
    hike = any(word in value for word in ("hike", "increase", "raise"))
    if number == 0 and (cut or hike):
        return 0, "NO_CHANGE"
    if number is None or not (cut or hike):
        return None, None
    sign = -1 if cut else 1
    direction = "CUT" if cut else "HIKE"
    more_than = ">" in value or any(
        phrase in value for phrase in ("more than", "greater than", "over", "above")
    )
    if more_than:
        representative = number + 25
        return sign * representative, f"{direction}_MORE_THAN_{number}"
    return sign * number, f"{direction}_EXACTLY_{number}"


def semantic_move_bp(text: str) -> int | None:
    return semantic_outcome(text)[0]


class KalshiPublicProvider(EventMarketProvider):
    def __init__(
        self,
        base_url: str = "https://api.elections.kalshi.com/trade-api/v2",
        timeout_seconds: float = 10,
        max_pages: int = 20,
    ):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(
            timeout=timeout_seconds, headers={"User-Agent": "fomc-basis-monitor/0.1"}
        )
        self.max_pages = max_pages

    def discover(self, decision_date: date | None = None) -> list[KalshiMarket]:
        try:
            series_tickers = self._relevant_series_tickers()
        except (httpx.HTTPError, KeyError, ValueError):
            series_tickers = []
        if series_tickers:
            raw_markets = []
            for series_ticker in series_tickers:
                raw_markets.extend(self._market_pages({"series_ticker": series_ticker}))
        else:
            raw_markets = self._market_pages({"status": "open"})
        candidates = []
        event_cache: dict[str, dict[str, Any]] = {}
        for raw in raw_markets:
            searchable = " ".join(
                str(raw.get(field, ""))
                for field in ("ticker", "event_ticker", "title", "subtitle", "rules_primary")
            )
            if not re.search(
                r"FOMC|Fed(?:eral)?.{0,30}(?:funds|rates?)|interest rates?", searchable, re.I
            ):
                continue
            close_time = raw.get("close_time")
            title = str(raw.get("title", ""))
            date_matches = not decision_date or (
                close_time and str(close_time).startswith(decision_date.isoformat())
            )
            if decision_date and not date_matches:
                label = f"{decision_date.strftime('%b')} {decision_date.day}, {decision_date.year}"
                date_matches = label.lower() in title.lower()
            if not date_matches:
                continue
            event_raw: dict[str, Any] = {}
            event_ticker = raw.get("event_ticker")
            if event_ticker:
                event_key = str(event_ticker)
                if event_key not in event_cache:
                    try:
                        event_response = self.client.get(f"{self.base_url}/events/{event_key}")
                        event_response.raise_for_status()
                        event_cache[event_key] = event_response.json().get("event", {})
                    except (httpx.HTTPError, ValueError):
                        event_cache[event_key] = {}
                event_raw = event_cache[event_key]
            candidates.append(self._market(raw, event_raw))
        return candidates

    def _relevant_series_tickers(self) -> list[str]:
        response = self.client.get(f"{self.base_url}/series", params={"category": "Economics"})
        response.raise_for_status()
        payload = response.json()
        tickers = []
        for raw in payload.get("series", []):
            title = str(raw.get("title", ""))
            if re.search(r"\bfed(?:eral)? funds rate\b|\bfed meeting\b", title, re.I):
                tickers.append(str(raw["ticker"]))
        return tickers

    def _market_pages(self, filters: dict[str, Any]) -> list[dict[str, Any]]:
        raw_markets: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(self.max_pages):
            params: dict[str, Any] = {"limit": 1000, **filters}
            if cursor:
                params["cursor"] = cursor
            response = self.client.get(f"{self.base_url}/markets", params=params)
            response.raise_for_status()
            payload = response.json()
            raw_markets.extend(payload.get("markets", []))
            cursor = payload.get("cursor")
            if not cursor:
                break
        else:
            raise RuntimeError("Kalshi market pagination exceeded configured max_pages")
        return raw_markets

    def _market(self, raw: dict[str, Any], event_raw: dict[str, Any] | None = None) -> KalshiMarket:
        ticker = raw["ticker"]
        order_response = self.client.get(
            f"{self.base_url}/markets/{ticker}/orderbook", params={"depth": 0}
        )
        order_response.raise_for_status()
        order_raw = order_response.json()
        book = order_raw.get("orderbook_fp", order_raw.get("orderbook", order_raw))
        yes = self._levels(book.get("yes_dollars", book.get("yes", [])))
        no = self._levels(book.get("no_dollars", book.get("no", [])))
        title = str(raw.get("title", ""))
        subtitle = str(raw.get("subtitle", ""))
        rules = str(raw.get("rules_primary", raw.get("rules", "")))
        outcome_move_bp, outcome_bucket = semantic_outcome(" ".join((title, subtitle, rules)))
        return KalshiMarket(
            ticker=ticker,
            title=title,
            subtitle=subtitle,
            rules=rules,
            settlement_description=str(
                raw.get("settlement_value") or raw.get("rules_secondary", "")
            ),
            close_time=raw.get("close_time"),
            status=str(raw.get("status", "unknown")),
            volume=self._decimal(raw.get("volume_fp", raw.get("volume"))),
            open_interest=self._decimal(raw.get("open_interest_fp", raw.get("open_interest"))),
            yes_bids=yes,
            no_bids=no,
            outcome_move_bp=outcome_move_bp,
            outcome_bucket=outcome_bucket,
            strike_type=raw.get("strike_type"),
            threshold_pct=self._decimal(raw.get("floor_strike", raw.get("cap_strike"))),
            raw={"event": event_raw or {}, "market": raw, "orderbook": order_raw},
        )

    @staticmethod
    def _levels(raw_levels: list[list[int | float]]) -> list[OrderBookLevel]:
        levels = []
        for price, quantity in raw_levels:
            price_decimal = Decimal(str(price))
            if price_decimal > 1:
                price_decimal /= Decimal("100")
            levels.append(
                OrderBookLevel(
                    price_dollars=price_decimal,
                    quantity=Decimal(str(quantity)),
                )
            )
        return levels

    @staticmethod
    def _decimal(value: Any) -> Decimal | None:
        return None if value is None else Decimal(str(value))
