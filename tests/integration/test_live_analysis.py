from datetime import UTC, date, datetime
from decimal import Decimal

from fomc_basis.config import load_config
from fomc_basis.models import FOMCMeeting, KalshiMarket, OrderBookLevel, Quote
from fomc_basis.services.live_analysis import analyze_live_meeting


class CalendarFixture:
    def meetings(self, year: int) -> list[FOMCMeeting]:
        return [
            FOMCMeeting(
                start_date=date(2027, 1, 26),
                decision_date=date(2027, 1, 27),
                effective_date=date(2027, 1, 28),
            )
        ]


class FuturesFixture:
    def quote(self, year: int, month: int) -> Quote:
        now = datetime.now(UTC)
        return Quote(
            instrument="ZQF27.CBT",
            source="fixture",
            source_timestamp=now,
            receipt_timestamp=now,
            bid=Decimal("96.338"),
            ask=Decimal("96.340"),
            price_precision=Decimal("0.0025"),
        )


class KalshiFixture:
    def discover(self, decision_date: date | None = None) -> list[KalshiMarket]:
        return [
            KalshiMarket(
                ticker="KXFED-25",
                title="Fed hikes exactly 25 bp",
                outcome_move_bp=25,
                yes_bids=[OrderBookLevel(price_dollars=Decimal("0.87"), quantity=1_000)],
                no_bids=[OrderBookLevel(price_dollars=Decimal("0.12"), quantity=1_000)],
                raw={"market": {"last_price_dollars": "0.875"}},
            )
        ]


class EffrFixture:
    def latest_effr_pct(self) -> tuple[date, float, dict]:
        return date(2027, 1, 25), 3.63, {"fixture": True}


def test_live_analysis_service_runs_all_provider_boundaries() -> None:
    result = analyze_live_meeting(
        date(2027, 1, 27),
        25,
        500,
        config=load_config(),
        calendar_provider=CalendarFixture(),
        futures_provider=FuturesFixture(),
        kalshi_provider=KalshiFixture(),
        effr_provider=EffrFixture(),
    )
    assert result["meeting"]["decision_date"] == "2027-01-27"
    assert result["live_context"]["market"]["ticker"] == "KXFED-25"
    assert result["live_context"]["top_of_book"]["yes_ask"] == Decimal("0.88")
    assert result["classification"]["label"] == "NO_TRADE"
