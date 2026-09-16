from __future__ import annotations

import tomllib
from importlib import resources
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class QualityConfig(BaseModel):
    stale_warning_seconds: float = 60
    stale_hard_seconds: float = 300
    sync_hard_seconds: float = 60
    minimum_futures_precision: float = 0.0025


class ModelConfig(BaseModel):
    state_grid_bp: list[int] = Field(default_factory=lambda: [-50, -25, 0, 25, 50, 75])
    prior: list[float] = Field(default_factory=lambda: [0.01, 0.04, 0.45, 0.43, 0.05, 0.02])
    tail_bounds: dict[int, tuple[float, float]] = Field(default_factory=dict)
    basis_scenarios_bp: list[int] = Field(default_factory=lambda: [0, -1, 1, -2, 2])
    ev_hurdle_dollars: float = 0.01
    probability_tolerance: float = 1e-8
    arbitrage_tolerance_dollars: float = 1e-8


class AppConfig(BaseModel):
    providers: dict[str, Any] = Field(default_factory=dict)
    quality: QualityConfig = Field(default_factory=QualityConfig)
    model: ModelConfig = Field(default_factory=ModelConfig)
    costs: dict[str, Any] = Field(default_factory=dict)
    limits: dict[str, Any] = Field(default_factory=dict)
    storage: dict[str, Any] = Field(default_factory=dict)


def load_config(path: str | Path = "config/default.toml") -> AppConfig:
    requested = Path(path)
    if requested.exists():
        with requested.open("rb") as handle:
            return AppConfig.model_validate(tomllib.load(handle))
    if str(path) == "config/default.toml":
        with resources.files("fomc_basis").joinpath("default.toml").open("rb") as handle:
            return AppConfig.model_validate(tomllib.load(handle))
    raise FileNotFoundError(f"configuration file does not exist: {requested}")
