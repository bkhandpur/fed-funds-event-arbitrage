from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from fomc_basis.enums import ObservationKind
from fomc_basis.models import AnalysisRequest, FOMCMeeting, Quote
from fomc_basis.services.backtest import replay_csv, replay_database, replay_frame
from fomc_basis.services.generic_analysis import analyze_request
from fomc_basis.services.persistence import save_analysis_run, save_realized_outcome
from fomc_basis.storage import Database


def analysis_request() -> AnalysisRequest:
    timestamp = datetime(2027, 1, 26, 17, tzinfo=UTC)
    return AnalysisRequest(
        meeting=FOMCMeeting(
            start_date=date(2027, 1, 26),
            decision_date=date(2027, 1, 27),
            effective_date=date(2027, 1, 28),
        ),
        futures_quote=Quote(
            instrument="ZQG27.CBT",
            source="recorded",
            source_timestamp=timestamp,
            receipt_timestamp=timestamp,
            bid=Decimal("96.338"),
            ask=Decimal("96.340"),
            price_precision=Decimal("0.0025"),
            observation_kind=ObservationKind.RECORDED,
        ),
        kalshi_yes_quote=Quote(
            instrument="RECORDED-25",
            source="recorded",
            source_timestamp=timestamp,
            receipt_timestamp=timestamp,
            bid=Decimal("0.87"),
            ask=Decimal("0.88"),
            price_precision=Decimal("0.01"),
            observation_kind=ObservationKind.RECORDED,
        ),
        current_effr_pct=3.63,
        probability_mode="bounds",
        state_probability_bounds={
            -50: (0, 0.01),
            -25: (0, 0.02),
            50: (0, 0.02),
            75: (0, 0.01),
        },
        kalshi_yes_ask_depth=500,
        kalshi_no_ask_depth=500,
        settlement_compatible=True,
        analysis_timestamp=timestamp + timedelta(seconds=1),
    )


def replay_data() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "meeting_timestamp": ["2026-09-16T18:00:00Z", "2026-09-16T18:00:00Z"],
            "observation_timestamp": ["2026-09-09T18:00:00Z", "2026-09-16T17:00:00Z"],
            "probability": [0.8, 0.9],
            "realized_move_bp": [25, 25],
            "kalshi_price_dollars": [0.75, 0.88],
            "gross_pnl_dollars": [25.0, 12.0],
            "net_pnl_dollars": [20.0, 5.0],
            "classification": ["RELATIVE_VALUE_TRADE", "NO_TRADE"],
            "observation_kind": ["RECORDED_HISTORICAL", "SYNTHETIC_EXAMPLE"],
        }
    )


def test_replay_reports_scorekeeping_groups_and_provenance() -> None:
    result = replay_frame(replay_data())
    assert result["metrics"]["observations"] == 2
    assert result["metrics"]["gross_pnl_dollars"] == pytest.approx(37)
    assert set(result["performance_by_horizon"]) == {"7d", "1h"}
    assert result["provenance_counts"]["RECORDED_HISTORICAL"] == 1
    assert "not a pure historical backtest" in result["warnings"][0]


def test_replay_csv_and_probability_validation(tmp_path: Path) -> None:
    path = tmp_path / "replay.csv"
    replay_data().to_csv(path, index=False)
    assert replay_csv(path)["metrics"]["net_pnl_dollars"] == pytest.approx(25)
    invalid = replay_data()
    invalid.loc[0, "probability"] = 1.1
    with pytest.raises(ValueError, match="probabilities"):
        replay_frame(invalid)


def test_replay_rejects_unknown_provenance() -> None:
    invalid = replay_data()
    invalid.loc[0, "observation_kind"] = "MAGIC_HISTORY"
    with pytest.raises(ValueError, match="observation_kind"):
        replay_frame(invalid)


def test_replay_multiclass_scorekeeping() -> None:
    frame = replay_data()
    frame["probability_state_0"] = [0.2, 0.1]
    frame["probability_state_25"] = [0.8, 0.9]
    result = replay_frame(frame)
    assert result["metrics"]["multiclass_brier_score"] == pytest.approx(0.05)
    assert result["metrics"]["multiclass_log_loss"] > 0


def test_replay_groups_optional_tail_and_basis_sensitivity() -> None:
    frame = replay_data()
    frame["tail_assumption"] = [0.01, 0.02]
    frame["basis_assumption_bp"] = [-1, 1]
    sensitivity = replay_frame(frame)["sensitivity"]
    assert set(sensitivity) == {"tail_assumption", "basis_assumption_bp"}
    assert sensitivity["tail_assumption"]["0.01"]["observations"] == 1


def test_database_replay_joins_model_runs_to_realized_outcomes(tmp_path: Path) -> None:
    path = tmp_path / "history.sqlite3"
    with Database(path) as database:
        save_analysis_run(database, analyze_request(analysis_request()))
        save_realized_outcome(database, date(2027, 1, 27), 25)
    result = replay_database(path)
    assert result["metrics"]["observations"] == 1
    assert result["database_replay"]["matched_runs"] == 1
