from __future__ import annotations

from datetime import date

import httpx

from .base import EFFRProvider


class NewYorkFedProvider(EFFRProvider):
    ENDPOINT = "https://markets.newyorkfed.org/api/rates/secured/sofr/search.json"
    EFFR_ENDPOINT = "https://markets.newyorkfed.org/api/rates/unsecured/effr/last/1.json"

    def __init__(self, timeout_seconds: float = 10):
        self.client = httpx.Client(
            timeout=timeout_seconds, headers={"User-Agent": "fomc-basis-monitor/0.1"}
        )

    def latest_effr_pct(self) -> tuple[date, float, dict]:
        response = self.client.get(self.EFFR_ENDPOINT)
        response.raise_for_status()
        raw = response.json()
        rows = raw.get("refRates", raw.get("rates", []))
        if not rows:
            raise LookupError("New York Fed returned no EFFR observations")
        row = rows[0]
        return date.fromisoformat(row["effectiveDate"]), float(row["percentRate"]), raw
