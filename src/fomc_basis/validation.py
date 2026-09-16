from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from .enums import RiskFlag
from .models import Quote


def quote_risk_flags(
    quotes: list[Quote],
    analysis_timestamp: datetime,
    *,
    stale_warning_seconds: float = 60,
    stale_hard_seconds: float = 300,
    sync_hard_seconds: float = 60,
    minimum_futures_precision: Decimal = Decimal("0.0025"),
) -> set[RiskFlag]:
    flags: set[RiskFlag] = {RiskFlag.ASYNC_EXECUTION}
    timestamps = []
    for quote in quotes:
        if quote.mode.value == "INDICATIVE_ONLY":
            flags.add(RiskFlag.INDICATIVE_ONLY)
        age = quote.age_seconds(analysis_timestamp)
        if age is None or age > stale_warning_seconds:
            flags.add(RiskFlag.STALE_QUOTE)
        if quote.bid is not None and quote.ask is not None:
            if quote.bid > quote.ask:
                flags.add(RiskFlag.CROSSED_MARKET)
            if quote.bid != quote.ask:
                flags.add(
                    RiskFlag.FUTURES_SPREAD
                    if quote.instrument.startswith("ZQ")
                    else RiskFlag.KALSHI_SPREAD
                )
        if (
            quote.price_precision is not None
            and quote.instrument.startswith("ZQ")
            and quote.price_precision > minimum_futures_precision
        ):
            flags.add(RiskFlag.COARSE_PRECISION)
        if quote.market_open is False:
            flags.add(RiskFlag.MARKET_CLOSED)
        if quote.source_timestamp:
            timestamps.append(quote.source_timestamp)
    if timestamps and (max(timestamps) - min(timestamps)).total_seconds() > sync_hard_seconds:
        flags.add(RiskFlag.UNSYNCHRONIZED_QUOTES)
    return flags


def arbitrage_quote_gate(
    quotes: list[Quote],
    analysis_timestamp: datetime,
    stale_hard_seconds: float = 300,
    sync_hard_seconds: float = 60,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if any(quote.mode.value != "EXECUTABLE" for quote in quotes):
        reasons.append("NON_EXECUTABLE_QUOTES")
    ages = [quote.age_seconds(analysis_timestamp) for quote in quotes]
    if any(age is None or age > stale_hard_seconds for age in ages):
        reasons.append("STALE_QUOTES")
    timestamps = [quote.source_timestamp for quote in quotes if quote.source_timestamp is not None]
    if len(timestamps) != len(quotes) or (
        timestamps and (max(timestamps) - min(timestamps)).total_seconds() > sync_hard_seconds
    ):
        reasons.append("UNSYNCHRONIZED_QUOTES")
    if any(
        quote.bid is not None and quote.ask is not None and quote.bid > quote.ask
        for quote in quotes
    ):
        reasons.append("CROSSED_MARKET")
    return not reasons, reasons
