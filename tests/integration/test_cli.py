import json
from datetime import UTC, date, datetime
from decimal import Decimal

from typer.testing import CliRunner

import fomc_basis.cli as cli_module
from fomc_basis.cli import app
from fomc_basis.models import FOMCMeeting, KalshiMarket, OrderBookLevel, Quote
from fomc_basis.storage import Database

runner = CliRunner()


def test_json_analysis_command() -> None:
    result = runner.invoke(app, ["analyze", "--meeting", "2026-09-16", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["classification"]["label"] == "NO_TRADE"
    assert payload["conclusion"].endswith("not a proven true arbitrage.")


def test_backtest_is_honest_about_missing_history() -> None:
    result = runner.invoke(app, ["backtest", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.output)["status"] == "no_recorded_history"


def test_generic_manual_analysis_command() -> None:
    result = runner.invoke(
        app,
        [
            "analyze",
            "--meeting",
            "2027-01-27",
            "--futures-bid",
            "96.338",
            "--futures-ask",
            "96.340",
            "--kalshi-yes-bid",
            "0.87",
            "--kalshi-yes-ask",
            "0.88",
            "--effr-pct",
            "3.63",
            "--tail-50-probability",
            "0.02",
            "--yes-depth",
            "500",
            "--no-depth",
            "500",
            "--settlement-compatible",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["meeting"]["decision_date"] == "2027-01-27"
    assert payload["day_count"]["post_decision_days"] == 4
    assert payload["research_only"] is True


def test_manual_cli_accepts_generic_state_grid_bounds_and_prior() -> None:
    result = runner.invoke(
        app,
        [
            "analyze",
            "--meeting",
            "2027-01-27",
            "--mode",
            "tails",
            "--futures-last",
            "96.338",
            "--kalshi-yes-last",
            "0.10",
            "--effr-pct",
            "3.63",
            "--outcome-bp",
            "-75",
            "--state",
            "-75",
            "--state",
            "0",
            "--state",
            "25",
            "--state-bound",
            "25:0.80:1.00",
            "--prior",
            "0.05",
            "--prior",
            "0.10",
            "--prior",
            "0.85",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["inputs"]["state_grid_bp"] == [-75, 0, 25]
    assert payload["probability_model"]["mode"] == "tails"


def test_cli_backtest_replays_csv(tmp_path) -> None:
    path = tmp_path / "history.csv"
    path.write_text(
        "meeting_timestamp,observation_timestamp,probability,realized_move_bp,"
        "kalshi_price_dollars,gross_pnl_dollars,net_pnl_dollars,classification,"
        "observation_kind\n"
        "2026-09-16T18:00:00Z,2026-09-09T18:00:00Z,0.8,25,0.75,25,20,"
        "RELATIVE_VALUE_TRADE,RECORDED_HISTORICAL\n"
    )
    result = runner.invoke(app, ["backtest", "--input", str(path), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["metrics"]["observations"] == 1


def test_curve_cli_reports_multiple_meeting_exposure() -> None:
    result = runner.invoke(
        app,
        [
            "curve",
            "--contract",
            "2026-09:96.26",
            "--contract",
            "2026-10:96.10",
            "--meeting-effective",
            "2026-09-17",
            "--meeting-effective",
            "2026-10-29",
            "--starting-effr-pct",
            "3.63",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["fully_identified"] is True
    assert payload["contracts_with_multiple_meetings"] == ["2026-10"]


def test_manual_analysis_can_persist_normalized_outputs(tmp_path) -> None:
    database_path = tmp_path / "analysis.sqlite3"
    output_path = tmp_path / "analysis.json"
    result = runner.invoke(
        app,
        [
            "analyze",
            "--meeting",
            "2027-01-27",
            "--futures-last",
            "96.338",
            "--kalshi-yes-last",
            "0.10",
            "--effr-pct",
            "3.63",
            "--save-database",
            str(database_path),
            "--output-json",
            str(output_path),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(output_path.read_text())["meeting"]["decision_date"] == "2027-01-27"
    with Database(database_path) as database:
        assert len(database.rows("model_runs")) == 1
        assert len(database.rows("inferred_probability_distributions")) == 1
        assert len(database.rows("trade_candidates")) == 1
        assert len(database.rows("state_payoff_results")) == 1


def test_snapshot_command_orchestrates_and_persists_all_providers(tmp_path, monkeypatch) -> None:
    meeting = FOMCMeeting(
        start_date=date(2027, 1, 26),
        decision_date=date(2027, 1, 27),
        effective_date=date(2027, 1, 28),
    )

    class Calendar:
        def meetings(self, year: int) -> list[FOMCMeeting]:
            return [meeting] if year == 2027 else []

    class Futures:
        def quote(self, year: int, month: int) -> Quote:
            return Quote(
                instrument=f"ZQ-{year}-{month}",
                source="fixture",
                source_timestamp=datetime(2027, 1, 1, tzinfo=UTC),
                last=Decimal("96.25"),
            )

    class Kalshi:
        def discover(self, decision_date: date | None = None) -> list[KalshiMarket]:
            return [
                KalshiMarket(
                    ticker="KXFED-25",
                    title="Fed hikes 25 bp",
                    yes_bids=[OrderBookLevel(price_dollars=Decimal("0.8"), quantity=5)],
                    raw={"market": {"event_ticker": "KXFED"}, "orderbook": {}},
                )
            ]

    class Effr:
        def latest_effr_pct(self) -> tuple[date, float, dict]:
            return date(2027, 1, 25), 3.63, {"fixture": True}

    monkeypatch.setattr(cli_module, "FederalReserveCalendarProvider", Calendar)
    monkeypatch.setattr(cli_module, "YahooFinanceProvider", lambda *_: Futures())
    monkeypatch.setattr(cli_module, "KalshiPublicProvider", lambda *_: Kalshi())
    monkeypatch.setattr(cli_module, "NewYorkFedProvider", lambda *_: Effr())
    database_path = tmp_path / "snapshot.sqlite3"
    result = runner.invoke(
        app,
        [
            "snapshot",
            "--as-of",
            "2027-01-01",
            "--database",
            str(database_path),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["provider_status"] == {
        "calendar": "ok",
        "futures": "ok",
        "kalshi": "ok",
        "effr": "ok",
    }
    assert "raw" not in payload["futures_quotes"][0]
    with Database(database_path) as database:
        assert len(database.rows("futures_quotes")) == 2
        assert len(database.rows("kalshi_order_book_snapshots")) == 1


def test_record_commands_persist_realized_data(tmp_path) -> None:
    database_path = tmp_path / "realized.sqlite3"
    outcome = runner.invoke(
        app,
        [
            "record-outcome",
            "--meeting",
            "2027-01-27",
            "--realized-move-bp",
            "25",
            "--database",
            str(database_path),
            "--json",
        ],
    )
    settlement = runner.invoke(
        app,
        [
            "record-settlement",
            "--symbol",
            "ZQF27.CBT",
            "--settlement-date",
            "2027-01-31",
            "--settlement-price-points",
            "96.25",
            "--database",
            str(database_path),
            "--json",
        ],
    )
    assert outcome.exit_code == 0, outcome.output
    assert settlement.exit_code == 0, settlement.output
    with Database(database_path) as database:
        assert len(database.rows("realized_fomc_outcomes")) == 1
        assert len(database.rows("realized_futures_settlements")) == 1


def test_payoff_and_optimizer_accept_generic_analysis_json(tmp_path) -> None:
    path = tmp_path / "analysis.json"
    path.write_text(
        json.dumps(
            {
                "state_payoffs": [
                    {
                        "move_bp": 0,
                        "combined_net_pnl_dollars": 2,
                        "capital_required_dollars": 60,
                    },
                    {
                        "move_bp": 25,
                        "combined_net_pnl_dollars": 3,
                        "capital_required_dollars": 60,
                    },
                ],
                "classification": {"label": "RELATIVE_VALUE_TRADE"},
            }
        )
    )
    payoff = runner.invoke(app, ["payoff", "--analysis-json", str(path), "--json"])
    optimized = runner.invoke(
        app,
        ["optimize", "--analysis-json", str(path), "--capital", "100", "--json"],
    )
    assert payoff.exit_code == 0, payoff.output
    assert len(json.loads(payoff.output)) == 2
    assert optimized.exit_code == 0, optimized.output
    assert json.loads(optimized.output)["quantities"] == {"hedged_package": 1}


def test_optimizer_accepts_multiple_directional_instruments(tmp_path) -> None:
    path = tmp_path / "instruments.json"
    path.write_text(
        json.dumps(
            [
                {
                    "name": "kalshi_yes",
                    "entry_cost_dollars": 1,
                    "state_payoffs_dollars": [0, 3],
                    "max_quantity": 2,
                },
                {
                    "name": "zq_long",
                    "entry_cost_dollars": 1,
                    "state_payoffs_dollars": [3, 0],
                    "max_quantity": 2,
                },
            ]
        )
    )
    result = runner.invoke(
        app,
        ["optimize", "--instruments-json", str(path), "--capital", "4", "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["worst_case_dollars"] > 0
    assert set(payload["quantities"]) == {"kalshi_yes", "zq_long"}
