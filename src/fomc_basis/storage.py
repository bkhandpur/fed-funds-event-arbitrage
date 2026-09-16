from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS fomc_meetings (id TEXT PRIMARY KEY, decision_date TEXT UNIQUE NOT NULL, payload_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS futures_contracts (id TEXT PRIMARY KEY, symbol TEXT UNIQUE NOT NULL, payload_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS futures_quotes (id TEXT PRIMARY KEY, instrument TEXT NOT NULL, source_timestamp TEXT, receipt_timestamp TEXT NOT NULL, payload_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS kalshi_events (id TEXT PRIMARY KEY, event_ticker TEXT UNIQUE NOT NULL, payload_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS kalshi_markets (id TEXT PRIMARY KEY, ticker TEXT UNIQUE NOT NULL, payload_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS kalshi_order_book_snapshots (id TEXT PRIMARY KEY, ticker TEXT NOT NULL, receipt_timestamp TEXT NOT NULL, payload_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS effr_observations (id TEXT PRIMARY KEY, effective_date TEXT NOT NULL, payload_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS model_runs (id TEXT PRIMARY KEY, analysis_timestamp TEXT NOT NULL, payload_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS inferred_probability_distributions (id TEXT PRIMARY KEY, model_run_id TEXT NOT NULL, payload_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS trade_candidates (id TEXT PRIMARY KEY, model_run_id TEXT NOT NULL, payload_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS state_payoff_results (id TEXT PRIMARY KEY, model_run_id TEXT NOT NULL, payload_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS realized_fomc_outcomes (id TEXT PRIMARY KEY, decision_date TEXT UNIQUE NOT NULL, payload_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS realized_futures_settlements (id TEXT PRIMARY KEY, symbol TEXT NOT NULL, settlement_date TEXT NOT NULL, payload_json TEXT NOT NULL, UNIQUE(symbol, settlement_date));
"""

ENTITY_UNIQUE_KEYS = {
    "fomc_meetings": ("decision_date",),
    "futures_contracts": ("symbol",),
    "kalshi_events": ("event_ticker",),
    "kalshi_markets": ("ticker",),
    "realized_fomc_outcomes": ("decision_date",),
    "realized_futures_settlements": ("symbol", "settlement_date"),
}


def deterministic_id(namespace: str, payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(f"{namespace}:{canonical}".encode()).hexdigest()


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row

    def initialize(self) -> None:
        self.connection.executescript(SCHEMA)
        self.connection.commit()

    def insert(self, table: str, payload: dict[str, Any], **indexed: Any) -> str:
        allowed = {
            line.split()[5] for line in SCHEMA.splitlines() if line.startswith("CREATE TABLE")
        }
        if table not in allowed:
            raise ValueError(f"unknown table: {table}")
        unique_keys = ENTITY_UNIQUE_KEYS.get(table)
        identity_payload = (
            {key: indexed[key] for key in unique_keys}
            if unique_keys and all(key in indexed for key in unique_keys)
            else {**indexed, "payload": payload}
        )
        identifier = deterministic_id(table, identity_payload)
        values = {
            "id": identifier,
            **indexed,
            "payload_json": json.dumps(payload, sort_keys=True, default=str),
        }
        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        if unique_keys and all(key in indexed for key in unique_keys):
            conflict = ", ".join(unique_keys)
            updates = ", ".join(
                f"{column}=excluded.{column}"
                for column in values
                if column not in {"id", *unique_keys}
            )
            statement = (
                f"INSERT INTO {table} ({columns}) VALUES ({placeholders}) "
                f"ON CONFLICT ({conflict}) DO UPDATE SET {updates}"
            )
        else:
            statement = f"INSERT OR IGNORE INTO {table} ({columns}) VALUES ({placeholders})"
        self.connection.execute(statement, tuple(values.values()))
        self.connection.commit()
        if unique_keys and all(key in indexed for key in unique_keys):
            predicate = " AND ".join(f"{key} = ?" for key in unique_keys)
            row = self.connection.execute(
                f"SELECT id FROM {table} WHERE {predicate}",
                tuple(indexed[key] for key in unique_keys),
            ).fetchone()
            if row is not None:
                return str(row["id"])
        return identifier

    def rows(self, table: str) -> list[dict[str, Any]]:
        cursor = self.connection.execute(f"SELECT * FROM {table}")
        return [dict(row) for row in cursor.fetchall()]

    def export(self, output_dir: str | Path, format: str = "csv") -> list[Path]:
        if format not in {"csv", "parquet"}:
            raise ValueError("format must be csv or parquet")
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        tables = [
            row[0]
            for row in self.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        ]
        paths = []
        for table in tables:
            frame = pd.read_sql_query(f"SELECT * FROM {table}", self.connection)
            path = output / f"{table}.{format}"
            frame.to_csv(path, index=False) if format == "csv" else frame.to_parquet(
                path, index=False
            )
            paths.append(path)
        return paths

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> Database:
        self.initialize()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
