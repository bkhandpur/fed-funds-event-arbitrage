from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd

from ..contracts import yahoo_zq_symbol
from ..enums import ObservationKind
from ..models import Quote
from .base import FuturesQuoteProvider


class CSVReplayProvider(FuturesQuoteProvider):
    REQUIRED = {"year", "month", "receipt_timestamp", "source"}

    def __init__(self, path: str | Path, as_of: datetime | None = None):
        self.frame = pd.read_csv(path)
        missing = self.REQUIRED - set(self.frame.columns)
        if missing:
            raise ValueError(f"CSV missing columns: {sorted(missing)}")
        self.as_of = as_of

    def quote(self, year: int, month: int) -> Quote:
        rows = self.frame[(self.frame.year == year) & (self.frame.month == month)].copy()
        rows["receipt_timestamp"] = pd.to_datetime(rows.receipt_timestamp, utc=True)
        if self.as_of is not None:
            rows = rows[rows.receipt_timestamp <= self.as_of]
        if rows.empty:
            raise LookupError(f"no replay quote for {year}-{month:02d}")
        row = rows.sort_values("receipt_timestamp").iloc[-1]

        def value(name: str) -> Decimal | None:
            return None if name not in row or pd.isna(row[name]) else Decimal(str(row[name]))

        return Quote(
            instrument=str(row.get("instrument", yahoo_zq_symbol(year, month))),
            source=str(row.source),
            source_timestamp=pd.to_datetime(
                row.get("source_timestamp", row.receipt_timestamp), utc=True
            ).to_pydatetime(),
            receipt_timestamp=row.receipt_timestamp.to_pydatetime(),
            bid=value("bid"),
            ask=value("ask"),
            last=value("last"),
            previous_close=value("previous_close"),
            price_precision=value("price_precision"),
            delayed=bool(row.get("delayed", True)),
            observation_kind=ObservationKind.RECORDED,
            raw=row.to_dict(),
        )
