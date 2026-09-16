import os
from datetime import date

import pytest

from fomc_basis.config import load_config
from fomc_basis.providers.federal_reserve import FederalReserveCalendarProvider
from fomc_basis.providers.kalshi import KalshiPublicProvider
from fomc_basis.providers.new_york_fed import NewYorkFedProvider
from fomc_basis.providers.yahoo import YahooFinanceProvider
from fomc_basis.services.snapshot import collect_snapshot
from fomc_basis.storage import Database

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.getenv("RUN_LIVE_TESTS") != "1",
        reason="set RUN_LIVE_TESTS=1 to contact public market-data endpoints",
    ),
]


def test_public_snapshot_provider_smoke(tmp_path) -> None:
    config = load_config()
    timeout = float(config.providers.get("http_timeout_seconds", 10))
    with Database(tmp_path / "live.sqlite3") as database:
        result = collect_snapshot(
            database,
            FederalReserveCalendarProvider(timeout_seconds=timeout),
            YahooFinanceProvider(timeout, 1),
            KalshiPublicProvider(str(config.providers["kalshi_base_url"]), timeout),
            NewYorkFedProvider(timeout),
            as_of=date.today(),
        )
    assert result.provider_status["calendar"] == "ok"
    assert result.provider_status["effr"] == "ok"
    assert result.meeting is not None
    assert result.effr_pct is not None
