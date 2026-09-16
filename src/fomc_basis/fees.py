from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_CEILING, Decimal

CENT = Decimal("0.01")


@dataclass(frozen=True)
class KalshiFeeSchedule:
    name: str = "configurable-standard"
    coefficient: Decimal = Decimal("0.07")
    maker_coefficient: Decimal = Decimal("0")
    settlement_fee_per_contract: Decimal = Decimal("0")
    effective_date: date | None = None

    def order_fee(
        self, contracts: int, price_dollars: Decimal, *, maker: bool = False, zero_fee: bool = False
    ) -> Decimal:
        """Fee is rounded once for the aggregate order, never per contract."""
        if contracts < 0:
            raise ValueError("contracts must be nonnegative")
        if not Decimal("0") <= price_dollars <= Decimal("1"):
            raise ValueError("binary price must be between zero and one dollar")
        if zero_fee or contracts == 0:
            return Decimal("0")
        coefficient = self.maker_coefficient if maker else self.coefficient
        raw = coefficient * Decimal(contracts) * price_dollars * (Decimal("1") - price_dollars)
        return raw.quantize(CENT, rounding=ROUND_CEILING)

    def total_fee(self, contracts: int, price_dollars: Decimal, **kwargs: bool) -> Decimal:
        return (
            self.order_fee(contracts, price_dollars, **kwargs)
            + self.settlement_fee_per_contract * contracts
        )
