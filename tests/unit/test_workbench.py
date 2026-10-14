from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from api.index import app
from fomc_basis.math.fed_funds import exact_settlement_price
from fomc_basis.models import AnalysisRequest
from fomc_basis.services.analysis import example_request
from fomc_basis.services.generic_analysis import analyze_request
from fomc_basis.services.workbench import ledger_bounds

client = TestClient(app)


def changed(**values):
    return AnalysisRequest.model_validate(
        {**example_request(True).model_dump(mode="json"), **values}
    )


def test_independent_reference_a():
    # Pencil cashflows using the supplied exchange multiplier, with no
    # implementation calls to derive the expected answers.
    fee = 8.74
    binary = 0.5 * 500 - 0.48 * 500 - fee
    zero = 0 - 240 - fee + (96.5 - 96.44) * 4167 - 6.04
    hike = 500 - 240 - fee + (96.375 - 96.44) * 4167 - 6.04
    assert binary == pytest.approx(1.26, abs=1e-10)
    assert [zero, hike] == pytest.approx([-4.76, -25.635], abs=1e-10)
    result = analyze_request(example_request(True))["workbench"]
    e = result["selected"]["economics"]
    assert e["combined_model_ev_dollars"] == pytest.approx(-15.1975, abs=1e-8)
    assert e["binary_model_ev_dollars"] == pytest.approx(binary, abs=1e-8)
    assert [r["combined_net_pnl_dollars"] for r in result["selected"]["ledger"]] == pytest.approx(
        [zero, hike], abs=1e-8
    )
    assert result["expected_move_bp"] == pytest.approx(12.5, abs=1e-8)


@pytest.mark.parametrize("side", ["yes", "no"])
@pytest.mark.parametrize("hedge", [-1, 0, 1, 2])
def test_slippage_identity(side, hedge):
    base = analyze_request(changed(selected_binary_side=side, selected_futures_contracts=hedge))[
        "workbench"
    ]["selected"]
    slipped = analyze_request(
        changed(
            selected_binary_side=side,
            selected_futures_contracts=hedge,
            slippage_per_kalshi_contract_dollars=".01",
        )
    )["workbench"]["selected"]
    for a, b in zip(base["ledger"], slipped["ledger"], strict=True):
        assert b["combined_net_pnl_dollars"] == pytest.approx(a["combined_net_pnl_dollars"] - 5)
        assert b["kalshi_acquisition_cost_dollars"] == a["kalshi_acquisition_cost_dollars"] + 5
        assert b["futures_gross_pnl_dollars"] == a["futures_gross_pnl_dollars"]
        assert b["combined_net_pnl_dollars"] == pytest.approx(
            b["kalshi_settlement_dollars"]
            - b["kalshi_acquisition_cost_dollars"]
            - b["kalshi_fees_dollars"]
            + b["futures_gross_pnl_dollars"]
            - b["futures_costs_dollars"]
        )


def test_joint_bounds_reference_b():
    r = changed(state_grid_bp=[0, 25, 50], probability_mode="bounds")
    # At mean 25, p0=p50=t and p25=1-2t for t in [0,.5].
    # This makes a payoff [10,-20,40] equal -20+90t.
    assert ledger_bounds(r, 25, [10, -20, 40]) == pytest.approx((-20, 25))
    assert ledger_bounds(r, 25, [0, 1, 0]) == pytest.approx((0, 1))
    fixed = changed(
        state_grid_bp=[0, 25, 50],
        probability_mode="tails",
        state_probability_bounds={50: [0.1, 0.1]},
    )
    assert ledger_bounds(fixed, 25, [0, 1, 0]) == pytest.approx((0.8, 0.8))
    with pytest.raises(ValueError, match="infeasible"):
        ledger_bounds(
            changed(
                state_grid_bp=[0, 25, 50],
                probability_mode="tails",
                state_probability_bounds={50: [0.6, 0.6]},
            ),
            25,
            [0, 1, 0],
        )


def test_pair_actual_settlement_difference_and_enum():
    r = changed(state_grid_bp=[-25, 25], two_state_low_bp=-25)
    w = analyze_request(r)["workbench"]
    # 50 bp pair changes half-month settlement by .25 points.
    assert w["continuous_pair_hedge"] == pytest.approx(500 / (0.25 * 4167))
    candidates = w["candidates"]
    assert [c["futures_contracts"] for c in candidates[2:5]] == [0, 0, 1]
    # Independently enumerate all signed positions and no-position by value.
    values = [0.0]
    for q in range(-10, 11):
        entry = 96.440 if q > 0 else 96.435
        pay = [
            (0 if s == -25 else 500) - 248.74 + q * 4167 * (settle - entry) - abs(q) * 6.04
            for s, settle in [(-25, 96.625), (25, 96.375)]
        ]
        if min(pay) >= -1000 and 248.74 + abs(q) * (2000 + 6.04) <= 10000:
            values.append(0.25 * pay[0] + 0.75 * pay[1])
    rec = candidates[-1]
    assert rec["economics"]["combined_feasible_ev_dollars"][0] == pytest.approx(
        max(values), abs=1e-8
    )


@pytest.mark.parametrize(
    "average,settlement",
    [("2.591499999", "97.409"), ("2.5915", "97.408"), ("2.591500001", "97.408")],
)
def test_exact_half_up(average, settlement):
    assert exact_settlement_price(Decimal(average)) == float(settlement)


def test_effective_next_month_calendar_and_leap():
    r = changed(
        meeting={
            "start_date": "2028-01-31",
            "decision_date": "2028-01-31",
            "effective_date": "2028-02-01",
        },
        futures_quote={
            **example_request(True).futures_quote.model_dump(mode="json"),
            "instrument": "ZQG28.CBT",
        },
    )
    a = analyze_request(r)
    assert a["day_count"]["days_in_month"] == 29
    assert a["day_count"]["pre_decision_days"] == 0
    assert a["workbench"]["conventions"]["settlement_month"] == "2028-02-01"


@pytest.mark.parametrize(
    "change,check",
    [
        ({"futures_quote": {"instrument": "ZQG27.CBT"}}, "Contract month"),
        ({"futures_ask_depth": None}, "Futures selected depth"),
        ({"kalshi_yes_ask_depth": 250}, "Binary selected depth"),
        ({"multiple_meetings_in_contract": True}, "Single meeting"),
        ({"all_outcomes_modeled": False}, "State coverage"),
        ({"capital_limit_dollars": 100}, "Capital"),
        ({"futures_quote": {"delayed": True}}, "Futures delay"),
        ({"futures_quote": {"bid": "96.45", "ask": "96.44"}}, "Futures two sided quote"),
        (
            {"futures_quote": {"source_timestamp": "2026-09-15T16:00:00+00:00"}},
            "Futures source time",
        ),
        (
            {"futures_quote": {"source_timestamp": "2026-09-13T16:00:00+00:00"}},
            "Futures source time",
        ),
    ],
)
def test_api_evidence_blockers(change, check):
    data = example_request(True).model_dump(mode="json")
    for k, v in change.items():
        data[k] = {**data[k], **v} if isinstance(v, dict) else v
    response = client.post("/api/analyze", json={"analysis": data})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["analysis"]["classification"]["label"] != "TRUE_ARBITRAGE"
    evidence = {r["check"]: r["status"] for r in body["analysis"]["workbench"]["evidence"]}
    assert evidence[check] in ("fail", "unknown")


def test_api_fractional_quantity():
    data = example_request(True).model_dump(mode="json")
    data["selected_futures_contracts"] = 1.5
    assert client.post("/api/analyze", json={"analysis": data}).status_code == 422


def test_user_view_never_changes_market_masses():
    base = analyze_request(example_request(True))["workbench"]
    view = analyze_request(changed(user_distribution=[0.2, 0.8]))["workbench"]
    assert view["market_distribution"] == base["market_distribution"]
    assert view["user_expected_move_bp"] == 20
    assert view["user_moment_residual_bp"] == pytest.approx(7.5)


def test_generated_fixtures_reproduce():
    import json
    from pathlib import Path

    for name in ["reference-a", "september"]:
        cached = json.loads((Path(__file__).parents[2] / "lib" / f"{name}-v2.json").read_text())
        rerun = analyze_request(AnalysisRequest.model_validate(cached["inputs"]))
        expected = cached["workbench"]["selected"]["economics"]
        actual = rerun["workbench"]["selected"]["economics"]
        for key, value in expected.items():
            if value is None:
                assert actual[key] is None
            else:
                assert actual[key] == pytest.approx(value, abs=1e-8)


def test_prior_published_carry_and_missing():
    from datetime import UTC, date, datetime, timedelta

    from fomc_basis.math.fed_funds import published_calendar_average

    friday = date(2026, 10, 9)
    saturday = date(2026, 10, 10)
    sunday = date(2026, 10, 11)
    published = datetime(2026, 10, 12, 13, tzinfo=UTC)
    observations = {friday: (Decimal("3.5"), published), saturday: None, sunday: None}
    assert published_calendar_average(observations, saturday, sunday, published) == Decimal("3.5")
    with pytest.raises(ValueError, match="preceding"):
        published_calendar_average(observations, saturday, sunday, published - timedelta(days=1))
    with pytest.raises(ValueError, match="missing"):
        published_calendar_average(
            {friday: observations[friday], saturday: None}, saturday, sunday, published
        )


@pytest.mark.parametrize("winning", [[0], [25, 50]])
def test_no_change_and_bucket_cash_vectors(winning):
    req = changed(
        state_grid_bp=[0, 25, 50], probability_mode="bounds", kalshi_winning_states_bp=winning
    )
    w = analyze_request(req)["workbench"]
    assert [r["kalshi_settlement_dollars"] for r in w["selected"]["ledger"]] == [
        500 if s in winning else 0 for s in [0, 25, 50]
    ]
    assert w["selected"]["economics"]["combined_model_ev_dollars"] == pytest.approx(
        sum(
            p * r["combined_net_pnl_dollars"]
            for p, r in zip(w["market_distribution"], w["selected"]["ledger"], strict=True)
        ),
        abs=1e-8,
    )


def test_current_schedule_centicient_and_maker():
    from fomc_basis.fees import KalshiFeeSchedule

    schedule = KalshiFeeSchedule(
        rounding_dollars=Decimal("0.0001"), maker_coefficient=Decimal(".0175")
    )
    assert schedule.order_fee(500, Decimal(".48")) == Decimal("8.7360")
    assert schedule.order_fee(500, Decimal(".48"), maker=True) == Decimal("2.1840")


def test_zero_total_position_is_rerunnable():
    w = analyze_request(changed(selected_no_position=True, selected_futures_contracts=0))[
        "workbench"
    ]
    assert w["selected"]["binary_contracts"] == 0
    assert w["selected"]["economics"]["combined_model_ev_dollars"] == 0


def test_separate_pre_average_and_post_basis():
    r = changed(
        pre_effective_effr_average_pct=3.4,
        no_change_effr_pct=3.6,
        current_effr_target_basis_bp=0,
        assumed_post_meeting_basis_bp=1,
    )
    w = analyze_request(r)["workbench"]
    # 15*3.4 + 15*(3.6 + .01 + expected_move/100) over 30 equals 3.5625.
    assert w["expected_move_bp"] == pytest.approx(11.5, abs=1e-8)
    assert w["conventions"]["pre_effective_average_pct"] == 3.4
    assert w["conventions"]["no_change_post_effr_pct"] == 3.6


def test_binary_return_denominator_excludes_hypothetical_futures_margin():
    result = analyze_request(changed(selected_futures_contracts=3))
    binary = result["expected_value"]["yes"]
    assert binary["estimated_total_capital_dollars"] == pytest.approx(248.74)
    assert binary["expected_return_on_total_capital"] == pytest.approx(1.26 / 248.74)
    assert result["workbench"]["selected"]["economics"][
        "estimated_cash_and_margin_dollars"
    ] == pytest.approx(248.74 + 3 * (2000 + 6.04))


@pytest.mark.parametrize("mean,expected", [(0, [1, 0, 0]), (50, [0, 0, 1])])
def test_identified_boundary_probability_mass(mean, expected):
    from fomc_basis.math.probabilities import regularized_distribution

    result = regularized_distribution([0, 25, 50], mean, [1, 1, 1])
    assert result.probabilities == pytest.approx(expected, abs=1e-10)
    assert result.residuals["sum"] == pytest.approx(0, abs=1e-10)
