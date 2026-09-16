from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..enums import ObservationKind
from ..storage import Database

REQUIRED_REPLAY_COLUMNS = {
    "meeting_timestamp",
    "observation_timestamp",
    "probability",
    "realized_move_bp",
    "kalshi_price_dollars",
    "gross_pnl_dollars",
    "net_pnl_dollars",
    "classification",
    "observation_kind",
}
HORIZONS_HOURS = {
    "30d": 30 * 24,
    "14d": 14 * 24,
    "7d": 7 * 24,
    "3d": 3 * 24,
    "1d": 24,
    "1h": 1,
}


def brier_score(probabilities: list[float], outcomes: list[int]) -> float:
    if len(probabilities) != len(outcomes) or not probabilities:
        raise ValueError("equal non-empty probability and outcome arrays required")
    return float(
        np.mean(
            [
                (probability - outcome) ** 2
                for probability, outcome in zip(probabilities, outcomes, strict=True)
            ]
        )
    )


def multiclass_brier(probabilities: list[list[float]], outcome_indices: list[int]) -> float:
    matrix = np.asarray(probabilities, dtype=float)
    targets = np.zeros_like(matrix)
    targets[np.arange(len(outcome_indices)), outcome_indices] = 1
    return float(np.mean(np.sum((matrix - targets) ** 2, axis=1)))


def multiclass_log_loss(
    probabilities: list[list[float]], outcome_indices: list[int], clip: float = 1e-12
) -> float:
    matrix = np.asarray(probabilities, dtype=float)
    if matrix.ndim != 2 or len(matrix) != len(outcome_indices):
        raise ValueError("one probability vector is required per outcome")
    clipped = np.clip(matrix, clip, 1.0)
    row_sums = clipped.sum(axis=1)
    if np.any(row_sums <= 0):
        raise ValueError("multiclass probabilities must have positive row sums")
    normalized = clipped / row_sums[:, None]
    return float(-np.mean(np.log(normalized[np.arange(len(outcome_indices)), outcome_indices])))


def log_loss(probabilities: list[float], outcomes: list[int], clip: float = 1e-12) -> float:
    values = [min(max(probability, clip), 1 - clip) for probability in probabilities]
    return -sum(
        outcome * math.log(probability) + (1 - outcome) * math.log(1 - probability)
        for probability, outcome in zip(values, outcomes, strict=True)
    ) / len(values)


def maximum_drawdown(pnl: list[float]) -> float:
    equity = np.cumsum(np.asarray(pnl, dtype=float))
    if equity.size == 0:
        return 0.0
    running_max = np.maximum.accumulate(np.r_[0.0, equity])
    drawdowns = running_max[1:] - equity
    return float(drawdowns.max(initial=0.0))


def backtest_metrics(
    probabilities: list[float], outcomes: list[int], net_pnl: list[float]
) -> dict[str, float]:
    return {
        "brier_score": brier_score(probabilities, outcomes),
        "log_loss": log_loss(probabilities, outcomes),
        "net_pnl_dollars": sum(net_pnl),
        "win_rate": sum(value > 0 for value in net_pnl) / len(net_pnl) if net_pnl else 0.0,
        "maximum_drawdown_dollars": maximum_drawdown(net_pnl),
    }


def calibration_table(
    probabilities: list[float], outcomes: list[int], bins: int = 10
) -> list[dict[str, float | int]]:
    if bins <= 0:
        raise ValueError("bins must be positive")
    frame = pd.DataFrame({"probability": probabilities, "outcome": outcomes})
    frame["bucket"] = pd.cut(frame["probability"], np.linspace(0, 1, bins + 1), include_lowest=True)
    rows = []
    for bucket, group in frame.groupby("bucket", observed=True):
        rows.append(
            {
                "lower": float(bucket.left),
                "upper": float(bucket.right),
                "count": int(len(group)),
                "mean_probability": float(group["probability"].mean()),
                "realized_frequency": float(group["outcome"].mean()),
            }
        )
    return rows


def _horizon_label(hours: float) -> str:
    candidates = sorted(HORIZONS_HOURS.items(), key=lambda item: abs(hours - item[1]))
    return candidates[0][0]


def _group_performance(frame: pd.DataFrame, column: str) -> dict[str, dict[str, float | int]]:
    output: dict[str, dict[str, float | int]] = {}
    for value, group in frame.groupby(column, dropna=False):
        output[str(value)] = {
            "observations": int(len(group)),
            "gross_pnl_dollars": float(group["gross_pnl_dollars"].sum()),
            "net_pnl_dollars": float(group["net_pnl_dollars"].sum()),
            "win_rate": float((group["net_pnl_dollars"] > 0).mean()),
            "average_edge_at_entry": float(group["edge_at_entry"].mean()),
        }
    return output


def replay_frame(frame: pd.DataFrame, outcome_bp: int = 25) -> dict[str, Any]:
    """Score recorded/reconstructed/synthetic observations without overstating provenance."""
    missing = REQUIRED_REPLAY_COLUMNS - set(frame.columns)
    if missing:
        raise ValueError(f"replay data missing columns: {sorted(missing)}")
    replay = frame.copy()
    replay["meeting_timestamp"] = pd.to_datetime(replay["meeting_timestamp"], utc=True)
    replay["observation_timestamp"] = pd.to_datetime(replay["observation_timestamp"], utc=True)
    replay["probability"] = pd.to_numeric(replay["probability"], errors="raise")
    if ((replay["probability"] < 0) | (replay["probability"] > 1)).any():
        raise ValueError("probabilities must be in [0, 1]")
    replay["outcome"] = (replay["realized_move_bp"] == outcome_bp).astype(int)
    replay["time_to_meeting_hours"] = (
        replay["meeting_timestamp"] - replay["observation_timestamp"]
    ).dt.total_seconds() / 3600
    if (replay["time_to_meeting_hours"] < 0).any():
        raise ValueError("replay observations must not occur after the meeting")
    replay["horizon"] = replay["time_to_meeting_hours"].map(_horizon_label)
    if "edge_at_entry" not in replay:
        replay["edge_at_entry"] = replay["probability"] - replay["kalshi_price_dollars"]
    probabilities = replay["probability"].astype(float).tolist()
    outcomes = replay["outcome"].astype(int).tolist()
    net_pnl = replay["net_pnl_dollars"].astype(float).tolist()
    metrics = backtest_metrics(probabilities, outcomes, net_pnl)
    metrics.update(
        {
            "gross_pnl_dollars": float(replay["gross_pnl_dollars"].sum()),
            "average_edge_at_entry": float(replay["edge_at_entry"].mean()),
            "observations": int(len(replay)),
        }
    )
    state_columns = {
        int(column.removeprefix("probability_state_")): column
        for column in replay.columns
        if column.startswith("probability_state_")
    }
    if state_columns:
        ordered_states = sorted(state_columns)
        if not set(replay["realized_move_bp"].astype(int)).issubset(ordered_states):
            raise ValueError("realized outcome is absent from multiclass probability columns")
        probability_matrix = replay[[state_columns[state] for state in ordered_states]].astype(
            float
        )
        if not np.allclose(probability_matrix.sum(axis=1), 1.0, atol=1e-6):
            raise ValueError("multiclass probability rows must sum to one")
        outcome_indices = [ordered_states.index(int(value)) for value in replay["realized_move_bp"]]
        metrics["multiclass_brier_score"] = multiclass_brier(
            probability_matrix.values.tolist(), outcome_indices
        )
        metrics["multiclass_log_loss"] = multiclass_log_loss(
            probability_matrix.values.tolist(), outcome_indices
        )
    kinds = {item.value for item in ObservationKind}
    unknown = set(replay["observation_kind"].astype(str)) - kinds
    if unknown:
        raise ValueError(f"unknown observation_kind values: {sorted(unknown)}")
    provenance = replay["observation_kind"].value_counts().to_dict()
    recorded_count = int(provenance.get(ObservationKind.RECORDED.value, 0))
    warnings = []
    if recorded_count != len(replay):
        warnings.append(
            "Results include non-recorded observations and are not a pure historical backtest."
        )
    if recorded_count < 30:
        warnings.append("Recorded sample is too small for strong calibration claims.")
    edge_decay = replay.groupby("horizon", observed=True)["edge_at_entry"].mean().to_dict()
    result = {
        "metrics": metrics,
        "calibration": calibration_table(probabilities, outcomes),
        "performance_by_horizon": _group_performance(replay, "horizon"),
        "performance_by_classification": _group_performance(replay, "classification"),
        "edge_decay_by_horizon": {str(key): float(value) for key, value in edge_decay.items()},
        "provenance_counts": {str(key): int(value) for key, value in provenance.items()},
        "warnings": warnings,
    }
    sensitivity_columns = [
        column for column in ("tail_assumption", "basis_assumption_bp") if column in replay
    ]
    result["sensitivity"] = {
        column: _group_performance(replay, column) for column in sensitivity_columns
    }
    return result


def replay_csv(path: str | Path, outcome_bp: int = 25) -> dict[str, Any]:
    return replay_frame(pd.read_csv(path), outcome_bp)


def replay_database(path: str | Path, outcome_bp: int = 25) -> dict[str, Any]:
    """Replay persisted model runs whose meetings have recorded realized outcomes."""
    with Database(path) as database:
        runs = database.rows("model_runs")
        outcomes = database.rows("realized_fomc_outcomes")
    realized = {
        row["decision_date"]: json.loads(row["payload_json"])["realized_move_bp"]
        for row in outcomes
    }
    records: list[dict[str, Any]] = []
    for row in runs:
        payload = json.loads(row["payload_json"])
        meeting = payload.get("meeting", {})
        decision_date = meeting.get("decision_date")
        if decision_date not in realized:
            continue
        realized_move = int(realized[decision_date])
        distribution = payload.get("probability_model", {}).get("selected_distribution", {})
        probability = distribution.get(str(outcome_bp), distribution.get(outcome_bp))
        chosen_side = payload.get("chosen_side")
        side_ev = payload.get("expected_value", {}).get(chosen_side, {})
        payoff = next(
            (
                item
                for item in payload.get("state_payoffs", [])
                if int(item["move_bp"]) == realized_move
            ),
            None,
        )
        if probability is None or payoff is None or not side_ev.get("available"):
            continue
        gross = (
            float(payoff["kalshi_settlement_dollars"])
            - float(payoff["kalshi_acquisition_cost_dollars"])
            + float(payoff["futures_gross_pnl_dollars"])
        )
        observation_kind = (
            payload.get("inputs", {})
            .get("futures_quote", {})
            .get("observation_kind", ObservationKind.RECONSTRUCTED.value)
        )
        records.append(
            {
                "meeting_timestamp": f"{decision_date}T18:00:00Z",
                "observation_timestamp": payload["analysis_timestamp"],
                "probability": probability,
                "realized_move_bp": realized_move,
                "kalshi_price_dollars": side_ev["price_dollars"],
                "gross_pnl_dollars": gross,
                "net_pnl_dollars": payoff["combined_net_pnl_dollars"],
                "classification": payload["classification"]["label"],
                "observation_kind": observation_kind,
            }
        )
    if not records:
        raise ValueError("database has no model runs matched to recorded realized outcomes")
    result = replay_frame(pd.DataFrame(records), outcome_bp)
    unmatched = len(runs) - len(records)
    result["database_replay"] = {
        "model_runs": len(runs),
        "matched_runs": len(records),
        "unmatched_or_incomplete_runs": unmatched,
    }
    if unmatched:
        result["warnings"].append(
            f"{unmatched} stored model run(s) lacked a matching complete realized outcome."
        )
    return result
