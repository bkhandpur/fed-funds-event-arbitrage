from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from fomc_basis.models import FOMCMeeting, KalshiMarket, OrderBookLevel, Quote
from fomc_basis.services.snapshot import collect_snapshot
from fomc_basis.storage import Database


class CalendarFixture:
    def meetings(self, year: int) -> list[FOMCMeeting]:
        if year != 2027:
            return []
        return [
            FOMCMeeting(
                start_date=date(2027, 1, 26),
                decision_date=date(2027, 1, 27),
                effective_date=date(2027, 1, 28),
            )
        ]


class FuturesFixture:
    def quote(self, year: int, month: int) -> Quote:
        return Quote(
            instrument=f"ZQ-{year}-{month}",
            source="fixture",
            source_timestamp=datetime(2027, 1, 1, tzinfo=UTC),
            bid=Decimal("96.2"),
            ask=Decimal("96.21"),
        )


class KalshiFixture:
    def discover(self, decision_date: date | None = None) -> list[KalshiMarket]:
        return [
            KalshiMarket(
                ticker="KXFED-25",
                title="Fed raises rates exactly 25 bp",
                outcome_move_bp=25,
                yes_bids=[OrderBookLevel(price_dollars=Decimal("0.8"), quantity=5)],
                raw={"market": {"event_ticker": "KXFED"}, "orderbook": {"yes": [[80, 5]]}},
            )
        ]


class EffrFixture:
    def latest_effr_pct(self) -> tuple[date, float, dict]:
        return (
            date(2027, 1, 25),
            3.63,
            {
                "fixture": True,
                "refRates": [{"targetRateFrom": 3.5, "targetRateTo": 3.75}],
            },
        )


def test_complete_snapshot_persists_every_provider_domain(tmp_path: Path) -> None:
    with Database(tmp_path / "snapshot.sqlite3") as database:
        result = collect_snapshot(
            database,
            CalendarFixture(),
            FuturesFixture(),
            KalshiFixture(),
            EffrFixture(),
            as_of=date(2027, 1, 1),
        )
        assert result.meeting and result.meeting.decision_date == date(2027, 1, 27)
        assert len(result.futures_quotes) == 2
        assert result.provider_status == {
            "calendar": "ok",
            "futures": "ok",
            "kalshi": "ok",
            "effr": "ok",
        }
        assert result.target_midpoint_pct == 3.625
        assert result.effr_target_basis_bp == pytest.approx(0.5)
        assert len(database.rows("fomc_meetings")) == 1
        assert len(database.rows("futures_contracts")) == 2
        assert len(database.rows("futures_quotes")) == 2
        assert len(database.rows("kalshi_events")) == 1
        assert len(database.rows("kalshi_markets")) == 1
        assert len(database.rows("kalshi_order_book_snapshots")) == 1
        assert len(database.rows("effr_observations")) == 1


class FailedFutures:
    def quote(self, year: int, month: int) -> Quote:
        raise RuntimeError("feed unavailable")


def test_snapshot_retains_other_sources_when_one_provider_fails(tmp_path: Path) -> None:
    with Database(tmp_path / "partial.sqlite3") as database:
        result = collect_snapshot(
            database,
            CalendarFixture(),
            FailedFutures(),
            KalshiFixture(),
            EffrFixture(),
            as_of=date(2027, 1, 1),
        )
    assert result.provider_status["futures"].startswith("partial:")
    assert result.effr_pct == 3.63
    assert result.kalshi_markets


class MultipleMeetingCalendar:
    def meetings(self, year: int) -> list[FOMCMeeting]:
        if year != 2027:
            return []
        return [
            FOMCMeeting(
                start_date=date(2027, 1, 26),
                decision_date=date(2027, 1, 27),
                effective_date=date(2027, 1, 28),
            ),
            FOMCMeeting(
                start_date=date(2027, 2, 23),
                decision_date=date(2027, 2, 24),
                effective_date=date(2027, 2, 25),
            ),
        ]


def test_snapshot_includes_adjacent_meetings_that_contaminate_curve(tmp_path: Path) -> None:
    with Database(tmp_path / "curve.sqlite3") as database:
        result = collect_snapshot(
            database,
            MultipleMeetingCalendar(),
            FuturesFixture(),
            KalshiFixture(),
            EffrFixture(),
            as_of=date(2027, 1, 1),
        )
        assert [item.decision_date for item in result.curve_meetings] == [
            date(2027, 1, 27),
            date(2027, 2, 24),
        ]
        assert len(database.rows("fomc_meetings")) == 2
