from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from ..calendar import next_meeting
from ..contracts import yahoo_zq_symbol
from ..models import FOMCMeeting, KalshiMarket, Quote, SnapshotResult
from ..providers.base import (
    CalendarProvider,
    EFFRProvider,
    EventMarketProvider,
    FuturesQuoteProvider,
)
from ..storage import Database


def save_snapshot(
    database: Database,
    futures_quotes: list[Quote],
    markets: list[KalshiMarket],
    meeting: FOMCMeeting | None = None,
    effr: tuple[date, float, dict] | None = None,
    additional_meetings: list[FOMCMeeting] | None = None,
) -> dict[str, list[str]]:
    ids: dict[str, list[str]] = {
        "fomc_meetings": [],
        "futures_contracts": [],
        "futures_quotes": [],
        "kalshi_events": [],
        "kalshi_markets": [],
        "order_books": [],
        "effr_observations": [],
    }
    meetings = ([meeting] if meeting is not None else []) + (additional_meetings or [])
    for scheduled_meeting in {item.decision_date: item for item in meetings}.values():
        payload = scheduled_meeting.model_dump(mode="json")
        ids["fomc_meetings"].append(
            database.insert(
                "fomc_meetings",
                payload,
                decision_date=scheduled_meeting.decision_date.isoformat(),
            )
        )
    for quote in futures_quotes:
        payload = quote.model_dump(mode="json")
        ids["futures_contracts"].append(
            database.insert(
                "futures_contracts",
                {"symbol": quote.instrument, "source": quote.source},
                symbol=quote.instrument,
            )
        )
        ids["futures_quotes"].append(
            database.insert(
                "futures_quotes",
                payload,
                instrument=quote.instrument,
                source_timestamp=str(quote.source_timestamp),
                receipt_timestamp=str(quote.receipt_timestamp),
            )
        )
    receipt = datetime.now(UTC).isoformat()
    for market in markets:
        payload = market.model_dump(mode="json")
        market_raw = payload.get("raw", {}).get("market", {})
        event_ticker = str(market_raw.get("event_ticker", market.ticker.split("-")[0]))
        event_payload = payload.get("raw", {}).get("event") or {"event_ticker": event_ticker}
        event_id = database.insert(
            "kalshi_events",
            event_payload,
            event_ticker=event_ticker,
        )
        if event_id not in ids["kalshi_events"]:
            ids["kalshi_events"].append(event_id)
        ids["kalshi_markets"].append(
            database.insert("kalshi_markets", payload, ticker=market.ticker)
        )
        ids["order_books"].append(
            database.insert(
                "kalshi_order_book_snapshots",
                payload["raw"].get("orderbook", {}),
                ticker=market.ticker,
                receipt_timestamp=receipt,
            )
        )
    if effr is not None:
        effective_date, effr_pct, raw = effr
        payload = {
            "effective_date": effective_date.isoformat(),
            "effr_pct": effr_pct,
            "raw": raw,
        }
        ids["effr_observations"].append(
            database.insert("effr_observations", payload, effective_date=effective_date.isoformat())
        )
    return ids


def _next_month(value: date) -> tuple[int, int]:
    next_value = (value.replace(day=28) + timedelta(days=4)).replace(day=1)
    return next_value.year, next_value.month


def collect_snapshot(
    database: Database,
    calendar_provider: CalendarProvider,
    futures_provider: FuturesQuoteProvider,
    kalshi_provider: EventMarketProvider,
    effr_provider: EFFRProvider,
    *,
    as_of: date | None = None,
) -> SnapshotResult:
    """Collect and persist a complete research snapshot without all-or-nothing failure."""
    as_of = as_of or date.today()
    status: dict[str, str] = {}
    meeting: FOMCMeeting | None = None
    futures_quotes: list[Quote] = []
    markets: list[KalshiMarket] = []
    effr: tuple[date, float, dict] | None = None
    scheduled: list[FOMCMeeting] = []
    curve_meetings: list[FOMCMeeting] = []
    try:
        scheduled.extend(calendar_provider.meetings(as_of.year))
        scheduled.extend(calendar_provider.meetings(as_of.year + 1))
        meeting = next_meeting(scheduled, as_of)
        status["calendar"] = "ok"
    except Exception as exc:
        status["calendar"] = f"error: {exc}"

    if meeting is not None:
        contract_months = {
            (meeting.decision_date.year, meeting.decision_date.month),
            (meeting.effective_date.year, meeting.effective_date.month),
            _next_month(meeting.decision_date),
        }
        final_year, final_month = max(contract_months)
        next_after_final = _next_month(date(final_year, final_month, 1))
        curve_cutoff = date(*next_after_final, 1)
        curve_meetings = [
            item
            for item in scheduled
            if item.decision_date >= as_of and item.effective_date < curve_cutoff
        ]
        errors = []
        for year, month in sorted(contract_months):
            try:
                futures_quotes.append(futures_provider.quote(year, month))
            except Exception as exc:
                errors.append(f"{yahoo_zq_symbol(year, month)}: {exc}")
        status["futures"] = "ok" if not errors else "partial: " + "; ".join(errors)
        try:
            markets = kalshi_provider.discover(meeting.decision_date)
            status["kalshi"] = "ok" if markets else "ok: no matching open markets"
        except Exception as exc:
            status["kalshi"] = f"error: {exc}"
    else:
        status["futures"] = "skipped: no meeting"
        status["kalshi"] = "skipped: no meeting"
    try:
        effr = effr_provider.latest_effr_pct()
        status["effr"] = "ok"
    except Exception as exc:
        status["effr"] = f"error: {exc}"

    stored = save_snapshot(
        database,
        futures_quotes,
        markets,
        meeting,
        effr,
        [item for item in curve_meetings if meeting is None or item != meeting],
    )
    target_lower_pct = None
    target_upper_pct = None
    if effr:
        rows = effr[2].get("refRates", effr[2].get("rates", []))
        if rows:
            target_lower_pct = rows[0].get("targetRateFrom")
            target_upper_pct = rows[0].get("targetRateTo")
    target_midpoint_pct = (
        (float(target_lower_pct) + float(target_upper_pct)) / 2
        if target_lower_pct is not None and target_upper_pct is not None
        else None
    )
    return SnapshotResult(
        meeting=meeting,
        curve_meetings=curve_meetings,
        futures_quotes=futures_quotes,
        kalshi_markets=markets,
        effr_effective_date=effr[0] if effr else None,
        effr_pct=effr[1] if effr else None,
        target_lower_pct=target_lower_pct,
        target_upper_pct=target_upper_pct,
        target_midpoint_pct=target_midpoint_pct,
        effr_target_basis_bp=(
            (effr[1] - target_midpoint_pct) * 100
            if effr is not None and target_midpoint_pct is not None
            else None
        ),
        provider_status=status,
        stored_ids=stored,
    )
