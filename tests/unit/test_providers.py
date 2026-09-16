import sys
from datetime import UTC, date
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pandas as pd
import pytest

from fomc_basis.enums import ObservationKind
from fomc_basis.models import Quote
from fomc_basis.providers.csv_replay import CSVReplayProvider
from fomc_basis.providers.federal_reserve import FederalReserveCalendarProvider
from fomc_basis.providers.kalshi import KalshiPublicProvider
from fomc_basis.providers.manual import ManualQuoteProvider
from fomc_basis.providers.new_york_fed import NewYorkFedProvider
from fomc_basis.providers.yahoo import YahooFinanceProvider


def test_kalshi_provider_parses_cents_and_depth() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/markets"):
            return httpx.Response(
                200,
                json={
                    "markets": [
                        {
                            "ticker": "KXFED-25",
                            "event_ticker": "KXFED",
                            "title": "Fed raises rates exactly 25 bps",
                            "rules_primary": "Settles from target range",
                            "status": "open",
                            "strike_type": "greater",
                            "floor_strike": "3.7500",
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"orderbook": {"yes": [[87, 10]], "no": [[12, 7]]}})

    provider = KalshiPublicProvider("https://example.test")
    provider.client = httpx.Client(transport=httpx.MockTransport(handler))
    markets = provider.discover()
    assert markets[0].outcome_move_bp == 25
    assert float(markets[0].yes_bids[0].price_dollars) == pytest.approx(0.87)
    assert markets[0].no_bids[0].quantity == 7
    assert markets[0].strike_type == "greater"
    assert float(markets[0].threshold_pct) == pytest.approx(3.75)


def test_kalshi_provider_preserves_fractional_fixed_point_quantities() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/markets"):
            return httpx.Response(
                200,
                json={
                    "markets": [
                        {
                            "ticker": "KXFED-25",
                            "title": "Fed raises rates 25 bps",
                            "volume_fp": "10.75",
                            "open_interest_fp": "8.50",
                        }
                    ]
                },
            )
        return httpx.Response(
            200, json={"orderbook_fp": {"yes_dollars": [["0.87", "7.25"]], "no_dollars": []}}
        )

    provider = KalshiPublicProvider("https://example.test")
    provider.client = httpx.Client(transport=httpx.MockTransport(handler))
    market = provider.discover()[0]
    assert market.yes_bids[0].quantity == Decimal("7.25")
    assert market.volume == Decimal("10.75")
    assert market.open_interest == Decimal("8.50")


def test_kalshi_provider_follows_bounded_pagination() -> None:
    market = {
        "ticker": "KXFED-25",
        "event_ticker": "KXFED",
        "title": "Fed raises rates exactly 25 bps",
        "status": "open",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/orderbook"):
            return httpx.Response(200, json={"orderbook": {"yes": [], "no": []}})
        if request.url.params.get("cursor") == "next":
            return httpx.Response(200, json={"markets": [market], "cursor": ""})
        return httpx.Response(200, json={"markets": [], "cursor": "next"})

    provider = KalshiPublicProvider("https://example.test")
    provider.client = httpx.Client(transport=httpx.MockTransport(handler))
    assert provider.discover()[0].ticker == "KXFED-25"


def test_new_york_fed_provider_parses_effr() -> None:
    provider = NewYorkFedProvider()
    provider.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, json={"refRates": [{"effectiveDate": "2026-09-14", "percentRate": "3.63"}]}
            )
        )
    )
    effective, value, _ = provider.latest_effr_pct()
    assert effective == date(2026, 9, 14)
    assert value == pytest.approx(3.63)


def test_federal_reserve_cached_calendar(tmp_path) -> None:
    cache = tmp_path / "calendar.html"
    cache.write_text("<div>September 15-16, 2026</div><div>October 27–28, 2026</div>")
    meetings = FederalReserveCalendarProvider(cache).meetings(2026)
    assert [meeting.decision_date for meeting in meetings] == [
        date(2026, 9, 16),
        date(2026, 10, 28),
    ]
    assert meetings[0].effective_date == date(2026, 9, 17)


def test_federal_reserve_year_scope_and_cross_month(tmp_path) -> None:
    cache = tmp_path / "calendar.html"
    cache.write_text(
        "<h4>2026 FOMC Meetings</h4><div>Jan/Feb 31-1*</div>"
        "<h4>2025 FOMC Meetings</h4><div>March 18-19</div>"
    )
    meetings = FederalReserveCalendarProvider(cache).meetings(2026)
    assert len(meetings) == 1
    assert meetings[0].start_date == date(2026, 1, 31)
    assert meetings[0].decision_date == date(2026, 2, 1)


def test_yahoo_provider_preserves_real_bid_ask_and_contract_tick(monkeypatch) -> None:
    history = pd.DataFrame(
        {"Open": [96.10], "High": [96.20], "Low": [96.00], "Close": [96.15]},
        index=pd.DatetimeIndex(["2026-09-14T00:00:00Z"]),
    )

    class Ticker:
        fast_info = {
            "bid": 96.14,
            "ask": 96.16,
            "last_price": 96.15,
            "previous_close": 96.125,
        }

        def history(self, **_: object) -> pd.DataFrame:
            return history

    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(Ticker=lambda _: Ticker()))
    quote = YahooFinanceProvider(max_retries=1).quote(2026, 9)

    assert quote.instrument == "ZQU26.CBT"
    assert quote.source_timestamp.tzinfo is UTC
    assert quote.bid == Decimal("96.14")
    assert quote.ask == Decimal("96.16")
    assert quote.last == Decimal("96.15")
    assert quote.previous_close == Decimal("96.125")
    assert quote.price_precision == Decimal("0.0025")
    assert quote.delayed is True
    assert quote.observation_kind is ObservationKind.LIVE_INDICATIVE


def test_yahoo_provider_never_fabricates_book_sides(monkeypatch) -> None:
    history = pd.DataFrame({"Close": [96.15]}, index=pd.DatetimeIndex(["2026-09-14T00:00:00Z"]))

    class Ticker:
        fast_info = {"last_price": 96.15}

        def history(self, **_: object) -> pd.DataFrame:
            return history

    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(Ticker=lambda _: Ticker()))
    quote = YahooFinanceProvider(max_retries=1).quote(2026, 9)

    assert quote.bid is None
    assert quote.ask is None
    assert quote.last == Decimal("96.15")


def test_yahoo_provider_reports_bounded_failure_and_validates_settings(monkeypatch) -> None:
    class EmptyTicker:
        fast_info: dict[str, object] = {}

        def history(self, **_: object) -> pd.DataFrame:
            return pd.DataFrame()

    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(Ticker=lambda _: EmptyTicker()))
    with pytest.raises(RuntimeError, match="after 1 attempts"):
        YahooFinanceProvider(max_retries=1).quote(2026, 9)
    with pytest.raises(ValueError, match="at least one"):
        YahooFinanceProvider(max_retries=0)
    with pytest.raises(ValueError, match="positive"):
        YahooFinanceProvider(timeout_seconds=0)


def test_yahoo_stream_explicitly_marks_polling_fallback(monkeypatch) -> None:
    history = pd.DataFrame({"Close": [96.15]}, index=pd.DatetimeIndex(["2026-09-14T00:00:00Z"]))

    class Ticker:
        @property
        def fast_info(self) -> dict[str, object]:
            raise LookupError("not available")

        def history(self, **_: object) -> pd.DataFrame:
            return history

    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(Ticker=lambda _: Ticker()))
    quote = YahooFinanceProvider(max_retries=1).stream(2026, 9)

    assert quote.raw["degraded_mode"] == "history_polling_without_fast_info"
    assert quote.bid is None and quote.ask is None


def test_csv_replay_selects_latest_quote_at_or_before_cutoff(tmp_path) -> None:
    path = tmp_path / "quotes.csv"
    pd.DataFrame(
        [
            {
                "year": 2027,
                "month": 1,
                "receipt_timestamp": "2027-01-01T12:00:00Z",
                "source_timestamp": "2027-01-01T11:59:59Z",
                "source": "recorded",
                "bid": 96.1,
                "ask": 96.2,
            },
            {
                "year": 2027,
                "month": 1,
                "receipt_timestamp": "2027-01-02T12:00:00Z",
                "source": "recorded",
                "bid": 96.3,
                "ask": 96.4,
            },
        ]
    ).to_csv(path, index=False)
    provider = CSVReplayProvider(path, as_of=pd.Timestamp("2027-01-01T18:00:00Z"))
    quote = provider.quote(2027, 1)

    assert quote.instrument == "ZQF27.CBT"
    assert quote.bid == Decimal("96.1")
    assert quote.observation_kind is ObservationKind.RECORDED
    with pytest.raises(LookupError, match="no replay quote"):
        provider.quote(2027, 2)


def test_csv_replay_validates_required_columns(tmp_path) -> None:
    path = tmp_path / "invalid.csv"
    pd.DataFrame([{"year": 2027}]).to_csv(path, index=False)
    with pytest.raises(ValueError, match="CSV missing columns"):
        CSVReplayProvider(path)


def test_manual_provider_returns_defensive_copy_and_clear_missing_error() -> None:
    timestamp = pd.Timestamp("2027-01-01T12:00:00Z").to_pydatetime()
    original = Quote(
        instrument="ZQF27.CBT",
        source="manual",
        source_timestamp=timestamp,
        receipt_timestamp=timestamp,
        bid=Decimal("96.1"),
        ask=Decimal("96.2"),
        observation_kind=ObservationKind.MANUAL,
    )
    source = {(2027, 1): original}
    provider = ManualQuoteProvider(source)
    source.clear()

    assert provider.quote(2027, 1) == original
    with pytest.raises(LookupError, match="no manual quote"):
        provider.quote(2027, 2)
