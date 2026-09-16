from __future__ import annotations

from decimal import Decimal

from .models import KalshiMarket, OrderBookLevel

MONTH_CODES = {
    1: "F",
    2: "G",
    3: "H",
    4: "J",
    5: "K",
    6: "M",
    7: "N",
    8: "Q",
    9: "U",
    10: "V",
    11: "X",
    12: "Z",
}


def yahoo_zq_symbol(year: int, month: int) -> str:
    """Return Yahoo's CBOT symbol for a 30-Day Federal Funds futures month."""
    if month not in MONTH_CODES:
        raise ValueError("month must be in 1..12")
    if year < 2000 or year > 2099:
        raise ValueError("year must be in 2000..2099 for Yahoo's two-digit convention")
    return f"ZQ{MONTH_CODES[month]}{year % 100:02d}.CBT"


def _best(levels: list[OrderBookLevel]) -> OrderBookLevel | None:
    return max(levels, key=lambda level: level.price_dollars, default=None)


def kalshi_top_of_book(market: KalshiMarket) -> dict[str, Decimal | None]:
    """Derive asks from the opposite bid book, preserving absent-side semantics."""
    yes = _best(market.yes_bids)
    no = _best(market.no_bids)
    one = Decimal("1")
    return {
        "yes_bid": yes.price_dollars if yes else None,
        "yes_bid_quantity": yes.quantity if yes else None,
        "yes_ask": one - no.price_dollars if no else None,
        "yes_ask_quantity": no.quantity if no else None,
        "no_bid": no.price_dollars if no else None,
        "no_bid_quantity": no.quantity if no else None,
        "no_ask": one - yes.price_dollars if yes else None,
        "no_ask_quantity": yes.quantity if yes else None,
    }
