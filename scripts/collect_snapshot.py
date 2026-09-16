from __future__ import annotations

import argparse
import json
from datetime import date

from fomc_basis.config import load_config
from fomc_basis.providers.federal_reserve import FederalReserveCalendarProvider
from fomc_basis.providers.kalshi import KalshiPublicProvider
from fomc_basis.providers.new_york_fed import NewYorkFedProvider
from fomc_basis.providers.yahoo import YahooFinanceProvider
from fomc_basis.services.snapshot import collect_snapshot
from fomc_basis.storage import Database


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", type=date.fromisoformat)
    parser.add_argument("--database", default="data/fomc_basis.sqlite3")
    parser.add_argument("--config", default="config/default.toml")
    args = parser.parse_args()
    config = load_config(args.config)
    timeout = float(config.providers.get("http_timeout_seconds", 10))
    with Database(args.database) as database:
        result = collect_snapshot(
            database,
            FederalReserveCalendarProvider(timeout_seconds=timeout),
            YahooFinanceProvider(
                timeout,
                int(config.providers.get("max_retries", 3)),
            ),
            KalshiPublicProvider(str(config.providers.get("kalshi_base_url", "")), timeout),
            NewYorkFedProvider(timeout),
            as_of=args.as_of,
        )
    print(
        json.dumps(
            {
                "analysis_timestamp": result.analysis_timestamp.isoformat(),
                "meeting": result.meeting.model_dump(mode="json") if result.meeting else None,
                "curve_meetings": [item.model_dump(mode="json") for item in result.curve_meetings],
                "futures_quote_count": len(result.futures_quotes),
                "kalshi_market_count": len(result.kalshi_markets),
                "effr_effective_date": result.effr_effective_date,
                "effr_pct": result.effr_pct,
                "target_lower_pct": result.target_lower_pct,
                "target_upper_pct": result.target_upper_pct,
                "target_midpoint_pct": result.target_midpoint_pct,
                "effr_target_basis_bp": result.effr_target_basis_bp,
                "provider_status": result.provider_status,
                "stored_ids": result.stored_ids,
            },
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
