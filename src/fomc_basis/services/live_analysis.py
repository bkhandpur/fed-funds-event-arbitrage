from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from ..config import AppConfig, load_config
from ..contracts import kalshi_top_of_book
from ..models import AnalysisRequest, Quote
from ..providers.base import (
    CalendarProvider,
    EFFRProvider,
    EventMarketProvider,
    FuturesQuoteProvider,
)
from ..providers.federal_reserve import FederalReserveCalendarProvider
from ..providers.kalshi import KalshiPublicProvider
from ..providers.new_york_fed import NewYorkFedProvider
from ..providers.yahoo import YahooFinanceProvider
from .generic_analysis import analyze_request


def analyze_live_meeting(
    decision_date: date,
    outcome_bp: int = 25,
    kalshi_contracts: int = 500,
    *,
    config: AppConfig | None = None,
    calendar_provider: CalendarProvider | None = None,
    futures_provider: FuturesQuoteProvider | None = None,
    kalshi_provider: EventMarketProvider | None = None,
    effr_provider: EFFRProvider | None = None,
) -> dict:
    """Run the public-data workflow for one official FOMC meeting."""
    config = config or load_config()
    timeout = float(config.providers.get("http_timeout_seconds", 10))
    calendar_provider = calendar_provider or FederalReserveCalendarProvider(timeout_seconds=timeout)
    futures_provider = futures_provider or YahooFinanceProvider(
        timeout, int(config.providers.get("max_retries", 3))
    )
    kalshi_provider = kalshi_provider or KalshiPublicProvider(
        str(config.providers.get("kalshi_base_url", "")), timeout
    )
    effr_provider = effr_provider or NewYorkFedProvider(timeout)
    meetings = calendar_provider.meetings(decision_date.year)
    matching_meetings = [item for item in meetings if item.decision_date == decision_date]
    if not matching_meetings:
        raise ValueError("meeting is not on the official scheduled calendar")
    meeting = matching_meetings[0]
    multiple_meetings = (
        sum(
            item.effective_date.year == meeting.effective_date.year
            and item.effective_date.month == meeting.effective_date.month
            for item in meetings
        )
        > 1
    )
    futures_quote = futures_provider.quote(
        meeting.effective_date.year, meeting.effective_date.month
    )
    _, effr_pct, effr_raw = effr_provider.latest_effr_pct()
    effr_rows = effr_raw.get("refRates", effr_raw.get("rates", []))
    target_lower_pct = effr_rows[0].get("targetRateFrom") if effr_rows else None
    target_upper_pct = effr_rows[0].get("targetRateTo") if effr_rows else None
    target_midpoint_pct = (
        (float(target_lower_pct) + float(target_upper_pct)) / 2
        if target_lower_pct is not None and target_upper_pct is not None
        else None
    )
    markets = kalshi_provider.discover(decision_date)
    matching_markets = [item for item in markets if item.outcome_move_bp == outcome_bp]
    if not matching_markets:
        raise ValueError(f"no open Kalshi market mapped to {outcome_bp:+d} bp")
    market = matching_markets[0]
    winning_states = None
    if market.outcome_bucket and "MORE_THAN" in market.outcome_bucket:
        winning_states = [
            state
            for state in config.model.state_grid_bp
            if (state > 25 if outcome_bp > 0 else state < -25)
        ]
    book = kalshi_top_of_book(market)
    receipt = datetime.now(UTC)
    kalshi_quote = Quote(
        instrument=market.ticker,
        source="Kalshi public API",
        source_timestamp=None,
        receipt_timestamp=receipt,
        bid=book["yes_bid"],
        ask=book["yes_ask"],
        last=Decimal(str(market.raw.get("market", {}).get("last_price_dollars")))
        if market.raw.get("market", {}).get("last_price_dollars") is not None
        else None,
        price_precision=Decimal("0.01"),
        raw=market.raw,
    )
    request = AnalysisRequest(
        meeting=meeting,
        futures_quote=futures_quote,
        kalshi_yes_quote=kalshi_quote,
        current_effr_pct=effr_pct,
        kalshi_outcome_bp=outcome_bp,
        kalshi_winning_states_bp=winning_states,
        kalshi_contracts=kalshi_contracts,
        state_grid_bp=config.model.state_grid_bp,
        probability_mode="bounds",
        prior=config.model.prior,
        state_probability_bounds=config.model.tail_bounds,
        basis_scenarios_bp=config.model.basis_scenarios_bp,
        kalshi_fee_coefficient=Decimal(str(config.costs.get("kalshi_taker_fee_coefficient", 0.07))),
        kalshi_maker_fee_coefficient=Decimal(
            str(config.costs.get("kalshi_maker_fee_coefficient", 0))
        ),
        kalshi_settlement_fee_per_contract_dollars=Decimal(
            str(config.costs.get("kalshi_settlement_fee_per_contract_dollars", 0))
        ),
        futures_round_trip_cost_per_contract_dollars=float(
            config.costs.get("futures_exit_cost_dollars", 3.02)
            + config.costs.get("futures_entry_commission_dollars", 1.5)
            + config.costs.get("futures_exchange_fee_dollars", 1.5)
            + config.costs.get("futures_nfa_fee_dollars", 0.02)
        ),
        futures_margin_per_contract_dollars=float(
            config.costs.get("futures_margin_per_contract_dollars", 2_000)
        ),
        slippage_per_kalshi_contract_dollars=Decimal(
            str(config.costs.get("slippage_per_kalshi_contract_dollars", 0))
        ),
        ev_hurdle_dollars=config.model.ev_hurdle_dollars,
        probability_tolerance=config.model.probability_tolerance,
        arbitrage_tolerance_dollars=config.model.arbitrage_tolerance_dollars,
        capital_limit_dollars=float(config.limits.get("capital_dollars", 10_000)),
        max_kalshi_contracts=int(config.limits.get("max_kalshi_contracts", 1_000)),
        max_futures_contracts=int(config.limits.get("max_futures_contracts", 10)),
        settlement_compatible=False,
        multiple_meetings_in_contract=multiple_meetings,
        kalshi_yes_ask_depth=book["yes_ask_quantity"],
        kalshi_no_ask_depth=book["no_ask_quantity"],
        analysis_timestamp=receipt,
        stale_warning_seconds=config.quality.stale_warning_seconds,
        stale_hard_seconds=config.quality.stale_hard_seconds,
        sync_hard_seconds=config.quality.sync_hard_seconds,
        minimum_futures_precision=Decimal(str(config.quality.minimum_futures_precision)),
        current_effr_target_basis_bp=(
            (effr_pct - target_midpoint_pct) * 100 if target_midpoint_pct is not None else None
        ),
    )
    result = analyze_request(request)
    result["live_context"] = {
        "market": market.model_dump(mode="json", exclude={"raw", "yes_bids", "no_bids"}),
        "top_of_book": book,
        "effr_raw": effr_raw,
        "target_lower_pct": target_lower_pct,
        "target_upper_pct": target_upper_pct,
        "target_midpoint_pct": target_midpoint_pct,
    }
    return result
