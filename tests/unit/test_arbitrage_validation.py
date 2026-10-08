from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from fomc_basis.enums import RiskFlag, TradeClassification
from fomc_basis.math.arbitrage import Instrument, classify, maximize_worst_case
from fomc_basis.models import Quote
from fomc_basis.validation import arbitrage_quote_gate, quote_risk_flags

NOW = datetime(2026, 9, 15, 16, tzinfo=UTC)


def quote(name: str, seconds_old: int = 0, bid: str | None = "1", ask: str | None = "2") -> Quote:
    return Quote(
        instrument=name,
        source="test",
        source_timestamp=NOW - timedelta(seconds=seconds_old),
        receipt_timestamp=NOW,
        bid=Decimal(bid) if bid else None,
        ask=Decimal(ask) if ask else None,
    )


def test_stale_quote_rejected() -> None:
    passed, reasons = arbitrage_quote_gate([quote("ZQU26", 301)], NOW)
    assert not passed
    assert "STALE_QUOTES" in reasons


def test_unsynchronized_quotes_rejected() -> None:
    passed, reasons = arbitrage_quote_gate([quote("ZQU26"), quote("KALSHI", 61)], NOW)
    assert not passed
    assert "UNSYNCHRONIZED_QUOTES" in reasons


def test_indicative_quote_rejected() -> None:
    passed, reasons = arbitrage_quote_gate([quote("ZQU26", bid=None, ask=None)], NOW)
    assert not passed
    assert "NON_EXECUTABLE_QUOTES" in reasons


def test_coarse_and_crossed_quote_flags() -> None:
    crossed = Quote(
        instrument="ZQU26",
        source="test",
        source_timestamp=NOW,
        receipt_timestamp=NOW,
        bid=Decimal("96.27"),
        ask=Decimal("96.26"),
        price_precision=Decimal("0.01"),
    )
    flags = quote_risk_flags([crossed], NOW)
    assert RiskFlag.CROSSED_MARKET in flags
    assert RiskFlag.COARSE_PRECISION in flags


def test_true_arbitrage_classifier_requires_every_gate() -> None:
    result = classify(
        [0, 1],
        0.5,
        set(),
        executable=True,
        all_outcomes_modeled=True,
        settlement_compatible=True,
        depth_sufficient=True,
        integer_sizing=True,
        basis_stress_pnl_dollars=[0, 0.5],
    )
    assert result.label == TradeClassification.TRUE_ARBITRAGE


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"executable": False}, "NON_EXECUTABLE_QUOTES"),
        ({"all_outcomes_modeled": False}, "UNMODELED_OUTCOMES"),
        ({"settlement_compatible": False}, "SETTLEMENT_MISMATCH"),
        ({"depth_sufficient": False}, "INSUFFICIENT_DEPTH"),
        ({"integer_sizing": False}, "NON_INTEGER_SIZING"),
    ],
)
def test_arbitrage_gate_failures(kwargs: dict[str, bool], reason: str) -> None:
    gates = dict(
        executable=True,
        all_outcomes_modeled=True,
        settlement_compatible=True,
        depth_sufficient=True,
        integer_sizing=True,
    )
    gates.update(kwargs)
    result = classify([0, 1], 0.5, set(), basis_stress_pnl_dollars=[0, 0.5], **gates)
    assert result.label != TradeClassification.TRUE_ARBITRAGE
    assert reason in result.reason_codes


def test_tail_state_and_fees_destroy_apparent_arbitrage() -> None:
    tail_loss = classify(
        [1, 1, -10],
        0.2,
        {RiskFlag.TAIL_RISK},
        executable=True,
        all_outcomes_modeled=True,
        settlement_compatible=True,
        depth_sufficient=True,
        integer_sizing=True,
        basis_stress_pnl_dollars=[-10],
    )
    assert tail_loss.label == TradeClassification.RELATIVE_VALUE
    fee_loss = classify(
        [-0.01, -0.01],
        -0.01,
        {RiskFlag.TRANSACTION_COSTS},
        executable=True,
        all_outcomes_modeled=True,
        settlement_compatible=True,
        depth_sufficient=True,
        integer_sizing=True,
        basis_stress_pnl_dollars=[-0.01],
    )
    assert fee_loss.label == TradeClassification.NO_TRADE


def test_milp_respects_whole_contracts_depth_and_capital() -> None:
    instrument = Instrument("bundle", 2, (3, 4), max_quantity=2, whole_contracts=True)
    result = maximize_worst_case([instrument], capital_dollars=3)
    assert result.quantities == {"bundle": 1}
    assert result.worst_case_dollars == pytest.approx(1)


def test_no_liquidity_cannot_manufacture_profit() -> None:
    instrument = Instrument("empty", 0, (10, 10), max_quantity=0)
    result = maximize_worst_case([instrument], capital_dollars=100)
    assert result.quantities == {}
    assert result.worst_case_dollars == pytest.approx(0)


def test_milp_respects_executability_and_separate_capital_usage() -> None:
    blocked = Instrument("blocked", 0, (10, 10), 2, executable=False)
    assert maximize_worst_case([blocked], 100).quantities == {}
    package = Instrument("package", 0, (2, 3), 5, capital_required_dollars=60)
    result = maximize_worst_case([package], 100)
    assert result.quantities == {"package": 1}
    assert result.worst_case_dollars == pytest.approx(2)


def test_warning_age_alone_does_not_apply_hard_no_trade_gate() -> None:
    result = classify(
        [0, 1],
        0.5,
        {RiskFlag.STALE_QUOTE},
        executable=True,
        all_outcomes_modeled=True,
        settlement_compatible=True,
        depth_sufficient=True,
        integer_sizing=True,
        basis_stress_pnl_dollars=[0, 0.5],
    )
    assert result.label == TradeClassification.TRUE_ARBITRAGE


def test_certain_loss_prefers_cash() -> None:
    result = maximize_worst_case([Instrument("loss", 10, (5, 5), 1)], 10)
    assert result.quantities == {}
    assert result.worst_case_dollars == 0


@pytest.mark.parametrize("capital", [0, 2, 4, 8])
def test_optimizer_matches_exhaustive_net_portfolios(capital: int) -> None:
    from itertools import product

    instruments = [Instrument("a", 2, (5, 0), 3), Instrument("b", 1, (0, 3), 3)]
    expected = max(
        min(3 * a - b, -2 * a + 2 * b)
        for a, b in product(range(4), repeat=2)
        if 2 * a + b <= capital
    )
    solution = maximize_worst_case(instruments, capital)
    assert solution.worst_case_dollars == pytest.approx(expected)


def test_short_credit_uses_explicit_collateral_and_net_cashflows() -> None:
    short = Instrument("short", -4, (-2, -3), 5, capital_required_dollars=10)
    result = maximize_worst_case([short], 20)
    assert result.quantities == {"short": 2}
    assert result.state_net_payoffs_dollars == pytest.approx((4, 2))


@pytest.mark.parametrize(
    "instrument",
    [
        Instrument("empty", 1, (), 1),
        Instrument("nan", float("nan"), (2,), 1),
        Instrument("infinite", 1, (float("inf"),), 1),
        Instrument("fraction", 1, (2,), 1.5),
        Instrument("short", -1, (-2,), 1),
    ],
)
def test_invalid_optimizer_inputs(instrument: Instrument) -> None:
    with pytest.raises(ValueError):
        maximize_worst_case([instrument], 10)


def test_fractional_sizing_blocks_other_classifications() -> None:
    for payoff in ([0, 1], [-1, 2]):
        result = classify(
            payoff,
            0.5,
            set(),
            executable=True,
            all_outcomes_modeled=True,
            settlement_compatible=True,
            depth_sufficient=True,
            integer_sizing=False,
            basis_stress_pnl_dollars=payoff,
        )
        assert result.label == TradeClassification.NO_TRADE
