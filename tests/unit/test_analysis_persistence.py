from datetime import UTC, datetime
from pathlib import Path

from fomc_basis.services.persistence import save_analysis_run
from fomc_basis.storage import Database


def test_analysis_run_persists_linked_outputs(tmp_path: Path) -> None:
    result = {
        "analysis_timestamp": datetime(2026, 9, 15, tzinfo=UTC).isoformat(),
        "probability_model": {"bounds": {"25": [0.8, 0.9]}},
        "classification": {"label": "NO_TRADE"},
        "expected_value": {"yes": -0.01},
        "chosen_side": "yes",
        "hedge": {"nearest_futures_contracts": 1},
        "state_payoffs": [{"move_bp": 25, "combined_net_pnl_dollars": -1}],
        "basis_stress": [],
    }
    with Database(tmp_path / "runs.sqlite3") as database:
        identifiers = save_analysis_run(database, result)
        assert len(database.rows("model_runs")) == 1
        assert len(database.rows("inferred_probability_distributions")) == 1
        assert len(database.rows("trade_candidates")) == 1
        assert len(database.rows("state_payoff_results")) == 1
    assert all(identifiers.values())
