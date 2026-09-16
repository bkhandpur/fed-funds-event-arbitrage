from pathlib import Path

import pytest

from fomc_basis.config import load_config
from fomc_basis.services.backtest import backtest_metrics, maximum_drawdown, multiclass_brier
from fomc_basis.storage import Database, deterministic_id


def test_deterministic_id_ignores_dict_order() -> None:
    assert deterministic_id("x", {"a": 1, "b": 2}) == deterministic_id("x", {"b": 2, "a": 1})


def test_sqlite_round_trip_and_deduplication(tmp_path: Path) -> None:
    with Database(tmp_path / "test.sqlite3") as database:
        payload = {"price": 96.25}
        first = database.insert(
            "futures_quotes",
            payload,
            instrument="ZQU26",
            source_timestamp="a",
            receipt_timestamp="b",
        )
        second = database.insert(
            "futures_quotes",
            payload,
            instrument="ZQU26",
            source_timestamp="a",
            receipt_timestamp="b",
        )
        rows = database.rows("futures_quotes")
    assert first == second
    assert len(rows) == 1


def test_entity_rows_upsert_latest_payload_with_stable_id(tmp_path: Path) -> None:
    with Database(tmp_path / "entities.sqlite3") as database:
        first = database.insert("kalshi_markets", {"volume": 1}, ticker="KXFED")
        second = database.insert("kalshi_markets", {"volume": 2}, ticker="KXFED")
        rows = database.rows("kalshi_markets")
    assert first == second
    assert len(rows) == 1
    assert '"volume": 2' in rows[0]["payload_json"]


def test_csv_export(tmp_path: Path) -> None:
    with Database(tmp_path / "test.sqlite3") as database:
        database.insert("fomc_meetings", {"x": 1}, decision_date="2026-09-16")
        paths = database.export(tmp_path / "export", "csv")
    assert any(path.name == "fomc_meetings.csv" for path in paths)


def test_parquet_export(tmp_path: Path) -> None:
    with Database(tmp_path / "test.sqlite3") as database:
        database.insert("fomc_meetings", {"x": 1}, decision_date="2026-09-16")
        paths = database.export(tmp_path / "parquet", "parquet")
    meeting_path = next(path for path in paths if path.name == "fomc_meetings.parquet")
    assert meeting_path.exists()


def test_backtest_metrics() -> None:
    metrics = backtest_metrics([0.8, 0.2], [1, 0], [1, -0.5])
    assert metrics["brier_score"] == pytest.approx(0.04)
    assert metrics["net_pnl_dollars"] == pytest.approx(0.5)
    assert metrics["win_rate"] == pytest.approx(0.5)
    assert maximum_drawdown([1, -2, 1]) == pytest.approx(2)
    assert multiclass_brier([[0.8, 0.2], [0.1, 0.9]], [0, 1]) == pytest.approx(0.05)


def test_packaged_default_config_loads_outside_repository(tmp_path: Path, monkeypatch) -> None:
    repository_config = load_config()
    monkeypatch.chdir(tmp_path)
    config = load_config()
    assert config.providers["kalshi_base_url"].startswith("https://")
    assert config.model.tail_bounds[50] == (0.0, 0.02)
    assert config == repository_config
