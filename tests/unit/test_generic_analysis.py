from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from fomc_basis.enums import ObservationKind
from fomc_basis.models import AnalysisRequest, FOMCMeeting, Quote
from fomc_basis.services.generic_analysis import analyze_request

NOW = datetime(2027, 1, 26, 17, tzinfo=UTC)


def request(*, executable: bool = True, mode: str = "bounds") -> AnalysisRequest:
    futures = Quote(
        instrument="ZQG27.CBT",
        source="manual",
        source_timestamp=NOW,
        receipt_timestamp=NOW,
        bid=Decimal("96.338") if executable else None,
        ask=Decimal("96.340") if executable else None,
        last=Decimal("96.339"),
        price_precision=Decimal("0.0025"),
        observation_kind=ObservationKind.USER_SUPPLIED,
    )
    kalshi = Quote(
        instrument="MANUAL-25",
        source="manual",
        source_timestamp=NOW,
        receipt_timestamp=NOW,
        bid=Decimal("0.87") if executable else None,
        ask=Decimal("0.88") if executable else None,
        last=Decimal("0.875"),
        price_precision=Decimal("0.01"),
        observation_kind=ObservationKind.USER_SUPPLIED,
    )
    return AnalysisRequest(
        meeting=FOMCMeeting(
            start_date=date(2027, 1, 26),
            decision_date=date(2027, 1, 27),
            effective_date=date(2027, 1, 28),
        ),
        futures_quote=futures,
        kalshi_yes_quote=kalshi,
        current_effr_pct=3.63,
        probability_mode=mode,
        state_probability_bounds={-50: (0, 0.01), -25: (0, 0.02), 50: (0, 0.02), 75: (0, 0.01)},
        kalshi_yes_ask_depth=500,
        kalshi_no_ask_depth=500,
        settlement_compatible=True,
        analysis_timestamp=NOW + timedelta(seconds=1),
    )


def test_generic_analysis_uses_actual_meeting_day_count() -> None:
    result = analyze_request(request())
    assert result["meeting"]["decision_date"] == "2027-01-27"
    assert result["day_count"]["days_in_month"] == 31
    assert result["day_count"]["pre_decision_days"] == 27
    assert result["day_count"]["post_decision_days"] == 4
    assert result["research_only"] is True
    assert set(result["expected_value"]) == {"yes", "no"}
    assert len(result["state_payoffs"]) == 6
    assert len(result["basis_stress"]) == 30


def test_probability_bounds_are_distinct_from_selected_distribution() -> None:
    result = analyze_request(request())
    model = result["probability_model"]
    assert model["mode"] == "bounds"
    assert model["selection_method"] == "minimum_kl_to_prior"
    assert model["warnings"]
    assert 25 in model["bounds"]


def test_last_only_analysis_is_indicative_and_never_arbitrage() -> None:
    result = analyze_request(request(executable=False))
    assert "indicative_last" in result["futures_implied"]
    assert result["classification"]["label"] == "NO_TRADE"
    assert "NON_EXECUTABLE_QUOTES" in result["classification"]["reason_codes"]


def test_final_calendar_day_requires_following_contract() -> None:
    invalid = request().model_copy(
        update={
            "meeting": FOMCMeeting(
                start_date=date(2027, 3, 30),
                decision_date=date(2027, 3, 31),
                effective_date=date(2027, 4, 1),
            )
        }
    )
    with pytest.raises(ValueError, match="following ZQ"):
        analyze_request(invalid)


def test_no_change_market_does_not_divide_by_zero() -> None:
    no_change = request().model_copy(update={"kalshi_outcome_bp": 0})
    result = analyze_request(no_change)
    assert result["hedge"]["nearest_futures_contracts"] == 0
    assert result["hedge"]["entry_side"] == "none"


def test_user_specified_tail_mode_reports_model_provenance() -> None:
    tailed = request().model_copy(update={"probability_mode": "tails"})
    result = analyze_request(tailed)
    model = result["probability_model"]
    assert model["selection_method"] == "user_constrained_minimum_kl_to_prior"
    assert model["optimization_status"] == "optimal"
    assert model["prior"] is not None
    assert "expected_move_bp" in model["constraint_residuals"]


def test_two_state_mode_accepts_a_user_selected_pair() -> None:
    two_state = request().model_copy(
        update={
            "probability_mode": "two_state",
            "two_state_low_bp": -25,
            "kalshi_outcome_bp": 25,
        }
    )
    result = analyze_request(two_state)
    distribution = result["probability_model"]["selected_distribution"]
    assert distribution[-25] + distribution[25] == pytest.approx(1)
    assert all(distribution[state] == 0 for state in (-50, 0, 50, 75))


def test_generic_analysis_reports_returns_fees_limits_and_basis() -> None:
    enriched = request().model_copy(
        update={
            "kalshi_settlement_fee_per_contract_dollars": Decimal("0.01"),
            "current_effr_target_basis_bp": -3.0,
            "assumed_post_meeting_basis_bp": -2.0,
            "historical_average_basis_bp": -2.5,
        }
    )
    result = analyze_request(enriched)
    yes = result["expected_value"]["yes"]
    assert "expected_return_on_cash" in yes
    assert "expected_return_on_total_capital" in yes
    assert set(yes["return_on_capital_hurdle_prices"]) == {"1pct", "2pct", "5pct", "10pct"}
    assert result["fee_schedule"]["settlement_fee_per_contract_dollars"] == Decimal("0.01")
    assert result["basis_model"]["current_effr_target_basis_bp"] == -3.0
    assert result["basis_model"]["central_basis_change_bp"] == 1.0
    assert result["limits"]["capital_sufficient"] is True


def test_position_and_capital_limits_are_hard_gates() -> None:
    limited = request().model_copy(
        update={
            "max_kalshi_contracts": 100,
            "capital_limit_dollars": 100,
        }
    )
    result = analyze_request(limited)
    assert result["classification"]["label"] == "NO_TRADE"
    assert "POSITION_LIMIT_EXCEEDED" in result["classification"]["reason_codes"]
    assert "CAPITAL_LIMIT_EXCEEDED" in result["classification"]["reason_codes"]


def test_analysis_rejects_invalid_binary_prices_and_thresholds() -> None:
    invalid_price = request().model_copy(
        update={
            "kalshi_yes_quote": request().kalshi_yes_quote.model_copy(
                update={"ask": Decimal("1.01")}
            )
        }
    )
    with pytest.raises(ValueError, match="Kalshi prices"):
        AnalysisRequest.model_validate(invalid_price.model_dump())
    with pytest.raises(ValueError, match="stale warning"):
        AnalysisRequest.model_validate(
            request()
            .model_copy(update={"stale_warning_seconds": 301, "stale_hard_seconds": 300})
            .model_dump()
        )


def test_multi_state_event_probability_and_payoff_include_all_bucket_states() -> None:
    bucket = request().model_copy(
        update={
            "kalshi_outcome_bp": 50,
            "kalshi_winning_states_bp": [50, 75],
        }
    )
    result = analyze_request(bucket)
    distribution = result["probability_model"]["selected_distribution"]
    assert result["probability_model"]["kalshi_event_probability"] == pytest.approx(
        distribution[50] + distribution[75]
    )
    settlements = {
        row["move_bp"]: row["kalshi_settlement_dollars"] for row in result["state_payoffs"]
    }
    chosen_side = result["chosen_side"]
    if chosen_side == "yes":
        assert settlements[50] == settlements[75] == 500
    else:
        assert settlements[50] == settlements[75] == 0


def test_post_meeting_basis_assumption_changes_central_settlement() -> None:
    baseline = analyze_request(request())
    shifted = analyze_request(
        request().model_copy(
            update={
                "current_effr_target_basis_bp": 0.5,
                "assumed_post_meeting_basis_bp": 1.5,
            }
        )
    )
    baseline_prices = [row["futures_settlement_price_points"] for row in baseline["state_payoffs"]]
    shifted_prices = [row["futures_settlement_price_points"] for row in shifted["state_payoffs"]]
    assert shifted_prices != baseline_prices
