from datetime import date, datetime
from decimal import Decimal

import pytest

from fomc_basis.contracts import kalshi_top_of_book, yahoo_zq_symbol
from fomc_basis.day_count import meeting_day_count
from fomc_basis.models import KalshiMarket, OrderBookLevel, Quote


@pytest.mark.parametrize(
    ("month", "code"),
    [
        (1, "F"),
        (2, "G"),
        (3, "H"),
        (4, "J"),
        (5, "K"),
        (6, "M"),
        (7, "N"),
        (8, "Q"),
        (9, "U"),
        (10, "V"),
        (11, "X"),
        (12, "Z"),
    ],
)
def test_yahoo_symbols_all_months(month: int, code: str) -> None:
    assert yahoo_zq_symbol(2026, month) == f"ZQ{code}26.CBT"


@pytest.mark.parametrize(("year", "suffix"), [(2001, "01"), (2026, "26"), (2099, "99")])
def test_yahoo_symbol_years(year: int, suffix: str) -> None:
    assert yahoo_zq_symbol(year, 9) == f"ZQU{suffix}.CBT"


def test_invalid_symbol_inputs() -> None:
    with pytest.raises(ValueError):
        yahoo_zq_symbol(2026, 13)
    with pytest.raises(ValueError):
        yahoo_zq_symbol(1999, 1)


@pytest.mark.parametrize(
    ("decision", "expected"),
    [
        (date(2026, 1, 2), (2, 29)),
        (date(2026, 9, 16), (16, 14)),
        (date(2026, 4, 29), (29, 1)),
        (date(2026, 4, 30), (30, 0)),
        (date(2028, 2, 14), (14, 15)),
    ],
)
def test_calendar_day_counts(decision: date, expected: tuple[int, int]) -> None:
    result = meeting_day_count(decision)
    assert (result.pre_decision_days, result.post_decision_days) == expected


def test_kalshi_asks_are_derived_from_opposite_bids() -> None:
    market = KalshiMarket(
        ticker="TEST",
        title="test",
        yes_bids=[OrderBookLevel(price_dollars=Decimal("0.87"), quantity=10)],
        no_bids=[OrderBookLevel(price_dollars=Decimal("0.12"), quantity=7)],
    )
    book = kalshi_top_of_book(market)
    assert book["yes_ask"] == Decimal("0.88")
    assert book["yes_ask_quantity"] == 7
    assert book["no_ask"] == Decimal("0.13")


def test_no_opposite_bid_means_no_derived_ask() -> None:
    market = KalshiMarket(ticker="TEST", title="test")
    assert kalshi_top_of_book(market)["yes_ask"] is None


def test_quote_requires_timezone_aware_timestamps() -> None:
    with pytest.raises(ValueError, match="UTC offset"):
        Quote(
            instrument="ZQ",
            source="test",
            receipt_timestamp=datetime(2027, 1, 1),
            last=Decimal("96"),
        )
