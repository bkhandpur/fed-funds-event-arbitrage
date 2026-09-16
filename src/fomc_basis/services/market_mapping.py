from __future__ import annotations

from ..enums import RiskFlag
from ..models import KalshiMarket


def map_outcome_markets(
    markets: list[KalshiMarket],
) -> tuple[dict[int, KalshiMarket], set[RiskFlag]]:
    mapped: dict[int, KalshiMarket] = {}
    flags: set[RiskFlag] = set()
    for market in markets:
        if market.outcome_move_bp is None:
            flags.add(RiskFlag.TAIL_RISK)
            continue
        if market.outcome_move_bp in mapped:
            flags.add(RiskFlag.SETTLEMENT_MISMATCH)
            continue
        mapped[market.outcome_move_bp] = market
        language = f"{market.rules} {market.settlement_description}".lower()
        if "target" in language and "effective federal funds" not in language:
            flags.add(RiskFlag.SETTLEMENT_MISMATCH)
    return mapped, flags
