from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any

import typer

from .config import load_config
from .contracts import kalshi_top_of_book
from .enums import ObservationKind
from .math.arbitrage import Instrument, maximize_worst_case
from .models import AnalysisRequest, FOMCMeeting, Quote, SnapshotResult
from .providers.federal_reserve import FederalReserveCalendarProvider
from .providers.kalshi import KalshiPublicProvider
from .providers.new_york_fed import NewYorkFedProvider
from .providers.yahoo import YahooFinanceProvider
from .reporting.console import render_analysis
from .services.analysis import analyze_september_2026_fixture
from .services.backtest import replay_csv, replay_database
from .services.curve_analysis import analyze_curve
from .services.generic_analysis import analyze_request
from .services.persistence import (
    save_analysis_run,
    save_realized_outcome,
    save_realized_settlement,
)
from .services.snapshot import collect_snapshot
from .storage import Database

app = typer.Typer(help="Research-only FOMC/Kalshi/ZQ basis monitor. Never places trades.")


def _print(value: object, json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps(value, indent=2, default=str))
    else:
        typer.echo(value)


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter("meeting must be an ISO date such as 2026-09-16") from exc


def _timestamp(value: str | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise typer.BadParameter("quote timestamp must include a UTC offset")
    return parsed.astimezone(UTC)


def _decimal(value: float | None) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


def _state_bound(value: str) -> tuple[int, tuple[float, float]]:
    try:
        state_text, lower_text, upper_text = value.split(":")
        return int(state_text), (float(lower_text), float(upper_text))
    except ValueError as exc:
        raise typer.BadParameter("state bounds use MOVE_BP:LOWER:UPPER, such as 50:0:0.02") from exc


def _contract_value(value: str) -> tuple[tuple[int, int], float]:
    try:
        month_text, number_text = value.rsplit(":", 1)
        parsed = date.fromisoformat(f"{month_text}-01")
        return (parsed.year, parsed.month), float(number_text)
    except ValueError as exc:
        raise typer.BadParameter("contract values use YYYY-MM:VALUE") from exc


def _analysis_file(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise typer.BadParameter(f"could not read analysis JSON: {exc}") from exc
    if not isinstance(value, dict) or "state_payoffs" not in value:
        raise typer.BadParameter("analysis JSON must contain state_payoffs")
    return value


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, default=str) + "\n", encoding="utf-8")


def _snapshot_payload(result: SnapshotResult, include_raw: bool) -> dict[str, object]:
    if include_raw:
        return result.model_dump(mode="json")
    quotes = []
    for quote in result.futures_quotes:
        payload = quote.model_dump(mode="json", exclude={"raw"})
        quotes.append(payload)
    markets = []
    for market in result.kalshi_markets:
        book = kalshi_top_of_book(market)
        markets.append(
            {
                "ticker": market.ticker,
                "title": market.title,
                "status": market.status,
                "outcome_move_bp": market.outcome_move_bp,
                "outcome_bucket": market.outcome_bucket,
                "strike_type": market.strike_type,
                "threshold_pct": market.threshold_pct,
                "volume": market.volume,
                "open_interest": market.open_interest,
                "top_of_book": book,
                "yes_depth_levels": len(market.yes_bids),
                "no_depth_levels": len(market.no_bids),
            }
        )
    return {
        "analysis_timestamp": result.analysis_timestamp,
        "meeting": result.meeting.model_dump(mode="json") if result.meeting else None,
        "curve_meetings": [item.model_dump(mode="json") for item in result.curve_meetings],
        "futures_quotes": quotes,
        "kalshi_markets": markets,
        "effr_effective_date": result.effr_effective_date,
        "effr_pct": result.effr_pct,
        "target_lower_pct": result.target_lower_pct,
        "target_upper_pct": result.target_upper_pct,
        "target_midpoint_pct": result.target_midpoint_pct,
        "effr_target_basis_bp": result.effr_target_basis_bp,
        "provider_status": result.provider_status,
        "stored_ids": result.stored_ids,
    }


@app.command()
def meetings(
    year: int = typer.Option(date.today().year), json_output: bool = typer.Option(False, "--json")
) -> None:
    values = FederalReserveCalendarProvider(
        cache_path=Path("data") / f"fomc_calendar_{year}.html"
    ).meetings(year)
    payload = [value.model_dump(mode="json") for value in values]
    _print(payload, json_output)


@app.command("discover-kalshi")
def discover_kalshi(
    meeting: str | None = typer.Option(None, "--meeting"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    config = load_config()
    markets = KalshiPublicProvider(config.providers.get("kalshi_base_url", "")).discover(
        _date(meeting) if meeting else None
    )
    _print([market.model_dump(mode="json") for market in markets], json_output)


@app.command("quote-zq")
def quote_zq(
    year: int = typer.Option(..., "--year"),
    month: int = typer.Option(..., "--month"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    config = load_config()
    quote = YahooFinanceProvider(
        float(config.providers.get("http_timeout_seconds", 10)),
        int(config.providers.get("max_retries", 3)),
    ).quote(year, month)
    _print(quote.model_dump(mode="json"), json_output)


@app.command()
def snapshot(
    as_of: str | None = typer.Option(None, "--as-of"),
    database: str = "data/fomc_basis.sqlite3",
    include_raw: bool = typer.Option(False, "--include-raw"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    config = load_config()
    with Database(database) as db:
        result = collect_snapshot(
            db,
            FederalReserveCalendarProvider(),
            YahooFinanceProvider(
                float(config.providers.get("http_timeout_seconds", 10)),
                int(config.providers.get("max_retries", 3)),
            ),
            KalshiPublicProvider(str(config.providers.get("kalshi_base_url", ""))),
            NewYorkFedProvider(float(config.providers.get("http_timeout_seconds", 10))),
            as_of=_date(as_of) if as_of else None,
        )
    _print(_snapshot_payload(result, include_raw), json_output)


@app.command()
def analyze(
    meeting: str = typer.Option(..., "--meeting"),
    mode: str = "bounds",
    live: bool = typer.Option(False, "--live"),
    futures_bid: float | None = typer.Option(None, "--futures-bid"),
    futures_ask: float | None = typer.Option(None, "--futures-ask"),
    futures_last: float | None = typer.Option(None, "--futures-last"),
    kalshi_yes_bid: float | None = typer.Option(None, "--kalshi-yes-bid"),
    kalshi_yes_ask: float | None = typer.Option(None, "--kalshi-yes-ask"),
    kalshi_yes_last: float | None = typer.Option(None, "--kalshi-yes-last"),
    effr_pct: float | None = typer.Option(None, "--effr-pct"),
    no_change_effr_pct: float | None = typer.Option(None, "--no-change-effr-pct"),
    effective_date: str | None = typer.Option(None, "--effective-date"),
    outcome_bp: int = typer.Option(25, "--outcome-bp"),
    two_state_low_bp: int = typer.Option(0, "--two-state-low-bp"),
    kalshi_contracts: int = typer.Option(500, "--kalshi-contracts"),
    tail_50_probability: float | None = typer.Option(None, "--tail-50-probability"),
    state: Annotated[list[int] | None, typer.Option("--state")] = None,
    state_bound: Annotated[list[str] | None, typer.Option("--state-bound")] = None,
    prior: Annotated[list[float] | None, typer.Option("--prior")] = None,
    winning_state: Annotated[list[int] | None, typer.Option("--winning-state")] = None,
    yes_depth: float | None = typer.Option(None, "--yes-depth"),
    no_depth: float | None = typer.Option(None, "--no-depth"),
    settlement_compatible: bool = typer.Option(False, "--settlement-compatible"),
    all_outcomes_modeled: bool = typer.Option(True, "--all-outcomes-modeled/--unmodeled-outcomes"),
    multiple_meetings: bool = typer.Option(False, "--multiple-meetings"),
    capital_limit: float | None = typer.Option(None, "--capital-limit"),
    max_kalshi_contracts: int | None = typer.Option(None, "--max-kalshi-contracts"),
    max_futures_contracts: int | None = typer.Option(None, "--max-futures-contracts"),
    maker_order: bool = typer.Option(False, "--maker-order"),
    zero_fee: bool = typer.Option(False, "--zero-fee"),
    settlement_fee: float | None = typer.Option(None, "--settlement-fee"),
    current_basis_bp: float | None = typer.Option(None, "--current-basis-bp"),
    post_basis_bp: float | None = typer.Option(None, "--post-basis-bp"),
    historical_basis_bp: float | None = typer.Option(None, "--historical-basis-bp"),
    quote_timestamp: str | None = typer.Option(None, "--quote-timestamp"),
    save_database: str | None = typer.Option(None, "--save-database"),
    output_json: Annotated[Path | None, typer.Option("--output-json")] = None,
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    decision = _date(meeting)
    manual_supplied = any(
        value is not None
        for value in (
            futures_bid,
            futures_ask,
            futures_last,
            kalshi_yes_bid,
            kalshi_yes_ask,
            kalshi_yes_last,
            effr_pct,
        )
    )
    if decision == date(2026, 9, 16) and not live and not manual_supplied:
        result = analyze_september_2026_fixture(kalshi_contracts)
        result["requested_mode"] = mode
        if save_database:
            with Database(save_database) as database:
                result["stored_ids"] = save_analysis_run(database, result)
        if output_json:
            _write_json(output_json, result)
        render_analysis(result, json_output)
        return

    config = load_config()
    timestamp = _timestamp(quote_timestamp)
    yes_depth_value = _decimal(yes_depth)
    no_depth_value = _decimal(no_depth)
    winning_states_value = winning_state
    meeting_model = FOMCMeeting(
        start_date=decision - timedelta(days=1),
        decision_date=decision,
        effective_date=_date(effective_date) if effective_date else decision + timedelta(days=1),
        source="manual override",
    )
    if live:
        calendar_provider = FederalReserveCalendarProvider()
        scheduled = calendar_provider.meetings(decision.year)
        matching = [item for item in scheduled if item.decision_date == decision]
        if not matching:
            raise typer.BadParameter("meeting is not on the official scheduled calendar")
        meeting_model = matching[0]
        futures_quote = YahooFinanceProvider().quote(
            meeting_model.effective_date.year, meeting_model.effective_date.month
        )
        _, live_effr, _ = NewYorkFedProvider().latest_effr_pct()
        effr_pct = live_effr
        markets = KalshiPublicProvider(str(config.providers.get("kalshi_base_url", ""))).discover(
            decision
        )
        matching_markets = [item for item in markets if item.outcome_move_bp == outcome_bp]
        if not matching_markets:
            raise typer.BadParameter(f"no open Kalshi market mapped to {outcome_bp:+d} bp")
        market = matching_markets[0]
        if market.outcome_bucket and "MORE_THAN" in market.outcome_bucket:
            winning_states_value = [
                state
                for state in config.model.state_grid_bp
                if (state > 25 if outcome_bp > 0 else state < -25)
            ]
        book = kalshi_top_of_book(market)
        kalshi_quote = Quote(
            instrument=market.ticker,
            source="Kalshi public API",
            source_timestamp=None,
            receipt_timestamp=datetime.now(UTC),
            bid=book["yes_bid"],
            ask=book["yes_ask"],
            price_precision=Decimal("0.01"),
            raw=market.raw,
        )
        yes_depth_value = book["yes_ask_quantity"]
        no_depth_value = book["no_ask_quantity"]
    else:
        if effr_pct is None:
            raise typer.BadParameter("manual analysis requires --effr-pct")
        futures_quote = Quote(
            instrument=f"ZQ-{meeting_model.effective_date:%Y-%m}",
            source="manual input",
            source_timestamp=timestamp,
            receipt_timestamp=datetime.now(UTC),
            bid=_decimal(futures_bid),
            ask=_decimal(futures_ask),
            last=_decimal(futures_last),
            price_precision=Decimal("0.0025"),
            observation_kind=ObservationKind.MANUAL,
        )
        kalshi_quote = Quote(
            instrument=f"MANUAL-{outcome_bp:+d}BP",
            source="manual input",
            source_timestamp=timestamp,
            receipt_timestamp=datetime.now(UTC),
            bid=_decimal(kalshi_yes_bid),
            ask=_decimal(kalshi_yes_ask),
            last=_decimal(kalshi_yes_last),
            price_precision=Decimal("0.01"),
            observation_kind=ObservationKind.MANUAL,
        )
    if effr_pct is None:  # guarded by live/manual branches
        raise typer.BadParameter("EFFR is unavailable")
    state_grid = state or config.model.state_grid_bp
    selected_prior = prior if prior is not None else (config.model.prior if state is None else None)
    state_bounds = dict(config.model.tail_bounds) if state is None else {}
    state_bounds.update(_state_bound(value) for value in (state_bound or []))
    if tail_50_probability is not None:
        state_bounds[50] = (tail_50_probability, tail_50_probability)
    request = AnalysisRequest(
        meeting=meeting_model,
        futures_quote=futures_quote,
        kalshi_yes_quote=kalshi_quote,
        current_effr_pct=effr_pct,
        no_change_effr_pct=no_change_effr_pct,
        kalshi_outcome_bp=outcome_bp,
        kalshi_winning_states_bp=winning_states_value,
        two_state_low_bp=two_state_low_bp,
        kalshi_contracts=kalshi_contracts,
        state_grid_bp=state_grid,
        probability_mode=mode,
        prior=selected_prior,
        state_probability_bounds=state_bounds,
        basis_scenarios_bp=config.model.basis_scenarios_bp,
        kalshi_fee_coefficient=Decimal(str(config.costs.get("kalshi_taker_fee_coefficient", 0.07))),
        kalshi_maker_fee_coefficient=Decimal(
            str(config.costs.get("kalshi_maker_fee_coefficient", 0))
        ),
        kalshi_settlement_fee_per_contract_dollars=Decimal(
            str(
                settlement_fee
                if settlement_fee is not None
                else config.costs.get("kalshi_settlement_fee_per_contract_dollars", 0)
            )
        ),
        kalshi_fee_schedule_name=str(
            config.costs.get("kalshi_schedule_name", "configurable-standard")
        ),
        kalshi_fee_schedule_effective_date=date.fromisoformat(
            str(config.costs["kalshi_schedule_effective_date"])
        )
        if config.costs.get("kalshi_schedule_effective_date")
        else None,
        kalshi_order_is_maker=maker_order,
        kalshi_zero_fee=zero_fee,
        futures_round_trip_cost_per_contract_dollars=float(
            config.costs.get("futures_exit_cost_dollars", 3.02)
            + config.costs.get("futures_entry_commission_dollars", 1.5)
            + config.costs.get("futures_exchange_fee_dollars", 1.5)
            + config.costs.get("futures_nfa_fee_dollars", 0.02)
        ),
        futures_margin_per_contract_dollars=float(
            config.costs.get("futures_margin_per_contract_dollars", 2000)
        ),
        slippage_per_kalshi_contract_dollars=Decimal(
            str(config.costs.get("slippage_per_kalshi_contract_dollars", 0))
        ),
        ev_hurdle_dollars=config.model.ev_hurdle_dollars,
        probability_tolerance=config.model.probability_tolerance,
        arbitrage_tolerance_dollars=config.model.arbitrage_tolerance_dollars,
        capital_limit_dollars=float(
            capital_limit
            if capital_limit is not None
            else config.limits.get("capital_dollars", 10_000)
        ),
        max_kalshi_contracts=int(
            max_kalshi_contracts
            if max_kalshi_contracts is not None
            else config.limits.get("max_kalshi_contracts", 1_000)
        ),
        max_futures_contracts=int(
            max_futures_contracts
            if max_futures_contracts is not None
            else config.limits.get("max_futures_contracts", 10)
        ),
        settlement_compatible=settlement_compatible,
        all_outcomes_modeled=all_outcomes_modeled,
        multiple_meetings_in_contract=multiple_meetings,
        kalshi_yes_ask_depth=yes_depth_value,
        kalshi_no_ask_depth=no_depth_value,
        analysis_timestamp=datetime.now(UTC),
        stale_warning_seconds=config.quality.stale_warning_seconds,
        stale_hard_seconds=config.quality.stale_hard_seconds,
        sync_hard_seconds=config.quality.sync_hard_seconds,
        minimum_futures_precision=Decimal(str(config.quality.minimum_futures_precision)),
        current_effr_target_basis_bp=current_basis_bp,
        assumed_post_meeting_basis_bp=post_basis_bp,
        historical_average_basis_bp=historical_basis_bp,
    )
    result = analyze_request(request)
    if save_database:
        with Database(save_database) as database:
            result["stored_ids"] = save_analysis_run(database, result)
    if output_json:
        _write_json(output_json, result)
    render_analysis(result, json_output)


@app.command()
def curve(
    contract: Annotated[list[str], typer.Option("--contract", help="Repeat YYYY-MM:FUTURES_PRICE")],
    meeting_effective: Annotated[
        list[str], typer.Option("--meeting-effective", help="Repeat ISO effective dates")
    ],
    starting_effr_pct: float = typer.Option(..., "--starting-effr-pct"),
    monthly_basis: Annotated[
        list[str] | None, typer.Option("--monthly-basis", help="Repeat YYYY-MM:BASIS_BP")
    ] = None,
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    contracts = dict(_contract_value(value) for value in contract)
    if len(contracts) != len(contract):
        raise typer.BadParameter("each --contract month must be unique")
    meetings = [_date(value) for value in meeting_effective]
    basis = dict(_contract_value(value) for value in (monthly_basis or []))
    _print(analyze_curve(contracts, meetings, starting_effr_pct, basis), json_output)


@app.command()
def payoff(
    meeting: str | None = typer.Option(None, "--meeting"),
    analysis_json: Annotated[Path | None, typer.Option("--analysis-json")] = None,
    kalshi_contracts: int = 500,
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    if analysis_json:
        result = _analysis_file(analysis_json)
    elif meeting and _date(meeting) == date(2026, 9, 16):
        result = analyze_september_2026_fixture(kalshi_contracts)
    else:
        raise typer.BadParameter("provide --analysis-json or the bundled --meeting 2026-09-16")
    _print(result["state_payoffs"], json_output)


@app.command()
def optimize(
    meeting: str | None = typer.Option(None, "--meeting"),
    analysis_json: Annotated[Path | None, typer.Option("--analysis-json")] = None,
    instruments_json: Annotated[Path | None, typer.Option("--instruments-json")] = None,
    capital: float = 5000,
    max_packages: int = typer.Option(100, "--max-packages"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    if analysis_json and instruments_json:
        raise typer.BadParameter("use either --analysis-json or --instruments-json, not both")
    if instruments_json:
        try:
            raw_instruments = json.loads(instruments_json.read_text(encoding="utf-8"))
            instruments = [Instrument(**item) for item in raw_instruments]
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            raise typer.BadParameter(f"invalid instruments JSON: {exc}") from exc
        solution = maximize_worst_case(instruments, capital)
        _print(
            {
                **solution.__dict__,
                "research_only": True,
                "warning": "Each long/short direction must be a separate, correctly costed instrument.",
            },
            json_output,
        )
        return
    if analysis_json:
        result = _analysis_file(analysis_json)
    elif meeting and _date(meeting) == date(2026, 9, 16):
        result = analyze_september_2026_fixture(500)
    else:
        raise typer.BadParameter(
            "provide --analysis-json, --instruments-json, or the bundled --meeting 2026-09-16"
        )
    state_net = tuple(float(row["combined_net_pnl_dollars"]) for row in result["state_payoffs"])
    capital_required = max(
        float(row["capital_required_dollars"]) for row in result["state_payoffs"]
    )
    candidate = Instrument(
        "hedged_package",
        0,
        state_net,
        max_packages,
        True,
        bool(result.get("classification", {}).get("label") != "NO_TRADE"),
        capital_required,
    )
    solution = maximize_worst_case([candidate], capital)
    payload = {
        **solution.__dict__,
        "research_only": True,
        "source_classification": result.get("classification"),
        "warning": "Optimization cannot override quote-quality or settlement-risk gates.",
    }
    _print(payload, json_output)


@app.command()
def backtest(
    input_path: str | None = typer.Option(None, "--input"),
    database: str | None = typer.Option(None, "--database"),
    outcome_bp: int = typer.Option(25, "--outcome-bp"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    if input_path and database:
        raise typer.BadParameter("use either --input or --database, not both")
    if input_path:
        _print(replay_csv(input_path, outcome_bp), json_output)
        return
    if database:
        _print(replay_database(database, outcome_bp), json_output)
        return
    payload = {
        "status": "no_recorded_history",
        "message": "Import CSV snapshots or accumulate local observations before claiming historical results.",
    }
    _print(payload, json_output)


@app.command("record-outcome")
def record_outcome(
    meeting: str = typer.Option(..., "--meeting"),
    realized_move_bp: int = typer.Option(..., "--realized-move-bp"),
    database: str = "data/fomc_basis.sqlite3",
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    with Database(database) as db:
        identifier = save_realized_outcome(db, _date(meeting), realized_move_bp)
    _print({"realized_fomc_outcome_id": identifier}, json_output)


@app.command("record-settlement")
def record_settlement(
    symbol: str = typer.Option(..., "--symbol"),
    settlement_date: str = typer.Option(..., "--settlement-date"),
    settlement_price_points: float = typer.Option(..., "--settlement-price-points"),
    database: str = "data/fomc_basis.sqlite3",
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    with Database(database) as db:
        identifier = save_realized_settlement(
            db, symbol, _date(settlement_date), settlement_price_points
        )
    _print({"realized_futures_settlement_id": identifier}, json_output)


@app.command()
def export(
    format: str = typer.Option("csv", "--format"),
    database: str = "data/fomc_basis.sqlite3",
    output: str = "exports",
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    with Database(database) as db:
        paths = db.export(output, format)
    _print({"files": [str(path) for path in paths]}, json_output)


if __name__ == "__main__":
    app()
