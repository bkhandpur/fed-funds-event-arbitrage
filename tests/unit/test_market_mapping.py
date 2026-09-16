from fomc_basis.enums import RiskFlag
from fomc_basis.models import KalshiMarket
from fomc_basis.providers.kalshi import semantic_move_bp, semantic_outcome
from fomc_basis.services.market_mapping import map_outcome_markets


def test_semantic_outcomes() -> None:
    assert semantic_move_bp("Fed raises rates exactly 25 bps") == 25
    assert semantic_move_bp("cut more than 50 basis points") == -75
    assert semantic_move_bp("No change in target range") == 0
    assert semantic_outcome("Fed hikes more than 25 bp") == (50, "HIKE_MORE_THAN_25")
    assert semantic_outcome("Fed cuts greater than 25 bp") == (-50, "CUT_MORE_THAN_25")
    assert semantic_outcome("Fed Cut rates by >25bps") == (-50, "CUT_MORE_THAN_25")
    assert semantic_outcome("Fed Hike rates by 0bps") == (0, "NO_CHANGE")


def test_mapping_flags_target_effr_settlement_mismatch() -> None:
    market = KalshiMarket(
        ticker="X",
        title="hike exactly 25 bp",
        rules="Settles from announced target range",
        outcome_move_bp=25,
    )
    mapped, flags = map_outcome_markets([market])
    assert mapped[25] == market
    assert RiskFlag.SETTLEMENT_MISMATCH in flags
