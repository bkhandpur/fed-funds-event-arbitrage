from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TypedDict

from .fed_funds import event_move_value_dollars


@dataclass(frozen=True)
class HedgeChoice:
    futures_contracts: int
    residual_dollars: float


class IntegerHedgeSet(TypedDict):
    continuous: float
    floor: HedgeChoice
    nearest: HedgeChoice
    ceiling: HedgeChoice


def continuous_futures_hedge(kalshi_contracts: int, event_move_value: float) -> float:
    """Long exact-hike YES is hedged by long ZQ under the 0/+25 comparison."""
    if event_move_value <= 0:
        raise ValueError("event move value must be positive")
    return kalshi_contracts / event_move_value


def integer_hedges(
    kalshi_contracts: int, days_in_month: int, post_days: int, move_bp: float = 25
) -> IntegerHedgeSet:
    exposure = event_move_value_dollars(move_bp, days_in_month, post_days)
    continuous = continuous_futures_hedge(kalshi_contracts, exposure)
    candidates = {
        "floor": math.floor(continuous),
        "nearest": round(continuous),
        "ceiling": math.ceil(continuous),
    }
    return IntegerHedgeSet(
        continuous=continuous,
        floor=HedgeChoice(candidates["floor"], kalshi_contracts - candidates["floor"] * exposure),
        nearest=HedgeChoice(
            candidates["nearest"], kalshi_contracts - candidates["nearest"] * exposure
        ),
        ceiling=HedgeChoice(
            candidates["ceiling"], kalshi_contracts - candidates["ceiling"] * exposure
        ),
    )
