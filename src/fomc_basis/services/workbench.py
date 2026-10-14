from __future__ import annotations

import math
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict
from scipy.optimize import linprog

from ..contracts import yahoo_zq_symbol
from ..fees import KalshiFeeSchedule
from ..math.fed_funds import futures_settlement_price
from ..math.payoff import state_payoff_table
from ..models import AnalysisRequest, DayCount

MODEL_VERSION = "2.0.0"


class Economics(BaseModel):
    binary_model_ev_dollars: float
    binary_feasible_ev_dollars: tuple[float, float]
    combined_model_ev_dollars: float
    combined_feasible_ev_dollars: tuple[float, float]
    futures_gross_ev_dollars: float
    total_costs_dollars: float
    acquisition_cash_dollars: float
    estimated_margin_dollars: float
    estimated_cash_and_margin_dollars: float
    worst_state_dollars: float
    stressed_worst_dollars: float
    worst_move_bp: int
    worst_basis_change_bp: float
    user_combined_ev_dollars: float | None = None


class Candidate(BaseModel):
    label: str
    futures_contracts: int
    binary_contracts: int
    entry_side: str
    entry_price_points: float
    feasible: bool
    economics: Economics
    ledger: list[dict[str, float | int]]
    stress: list[dict[str, float | int]]


class Evidence(BaseModel):
    check: str
    status: Literal["pass", "fail", "unknown"]
    detail: str


class Workbench(BaseModel):
    schema_version: Literal["2.0"] = "2.0"
    model_version: str = MODEL_VERSION
    expected_move_bp: float
    probability_interval: tuple[float, float]
    continuous_pair_hedge: float
    benchmark_states_bp: tuple[int, int]
    hedge_objective: str
    recommendation: str
    recommended_futures_contracts: int
    selected_futures_contracts: int
    candidates: list[Candidate]
    selected: Candidate
    evidence: list[Evidence]
    execution_status: str = "unverified"
    economics_status: str
    market_distribution: list[float]
    user_distribution: list[float] | None
    user_expected_move_bp: float | None
    user_moment_residual_bp: float | None
    break_even_binary_price_dollars: float | None
    sensitivities: list[dict[str, float]]
    futures_price_sensitivities: list[dict[str, Any]]
    conventions: dict[str, Any]


def ledger_bounds(
    request: AnalysisRequest, expected_move: float, values: list[float]
) -> tuple[float, float]:
    """Optimize the entire ledger over one jointly feasible probability set."""
    states = request.state_grid_bp
    bounds = [request.state_probability_bounds.get(s, (0, 1)) for s in states]
    if request.probability_mode == "two_state":
        for index, state in enumerate(states):
            if state not in (request.two_state_low_bp, request.kalshi_outcome_bp):
                if bounds[index][0] > 0:
                    raise ValueError(
                        "two-state assumptions are infeasible with declared tail bounds"
                    )
                bounds[index] = (0, 0)
    constraints = np.array([np.ones(len(states)), states])
    minimum = linprog(
        values, A_eq=constraints, b_eq=[1, expected_move], bounds=bounds, method="highs"
    )
    maximum = linprog(
        -np.asarray(values),
        A_eq=constraints,
        b_eq=[1, expected_move],
        bounds=bounds,
        method="highs",
    )
    if not minimum.success or not maximum.success:
        raise ValueError("joint probability constraints are infeasible")
    return float(minimum.fun), float(-maximum.fun)


def minimum_tick(settlement_month: date, observation_date: date) -> float:
    """Chapter 22 reduced tick starts on the specified trading-day boundary.

    At a holiday boundary conservatively retain the deferred tick until the
    following weekday. This is a tick indication, not exchange certification.
    """
    first = settlement_month.replace(day=1)
    if first.weekday() in (0, 5, 6):
        boundary = first
        while boundary.weekday() >= 5:
            boundary += timedelta(days=1)
    else:
        boundary = first - timedelta(days=first.weekday())
    return 0.0025 if observation_date >= boundary else 0.005


def construct_workbench(
    request: AnalysisRequest,
    days: DayCount,
    side: str,
    price: Decimal,
    probabilities: list[float],
    mean: float,
    probability_interval: tuple[float, float],
    central_basis: float,
) -> Workbench:
    schedule = KalshiFeeSchedule(
        coefficient=request.kalshi_fee_coefficient,
        maker_coefficient=request.kalshi_maker_fee_coefficient,
        settlement_fee_per_contract=request.kalshi_settlement_fee_per_contract_dollars,
        rounding_dollars=request.kalshi_fee_rounding_dollars,
    )
    fee = float(
        schedule.total_fee(
            request.kalshi_contracts,
            price,
            maker=request.kalshi_order_is_maker,
            zero_fee=request.kalshi_zero_fee,
        )
    )
    winning = request.kalshi_winning_states_bp or [request.kalshi_outcome_bp]
    pre = (
        request.pre_effective_effr_average_pct
        if request.pre_effective_effr_average_pct is not None
        else request.current_effr_pct
    )
    base = (
        request.no_change_effr_pct
        if request.no_change_effr_pct is not None
        else request.current_effr_pct
    )
    low = request.two_state_low_bp
    if low not in request.state_grid_bp or low in winning:
        low = next((s for s in request.state_grid_bp if s not in winning), request.state_grid_bp[0])
    high = winning[0]

    def settlement(s: int) -> float:
        return futures_settlement_price(
            pre,
            s,
            days.days_in_month,
            days.pre_decision_days,
            days.post_decision_days,
            central_basis,
            base,
        )

    payout_difference = (
        request.kalshi_contracts
        * ((high in winning) - (low in winning))
        * (1 if side == "yes" else -1)
    )
    price_difference = (settlement(high) - settlement(low)) * 4167
    continuous = -payout_difference / price_difference if price_difference else 0.0
    floor, nearest, ceiling = math.floor(continuous), round(continuous), math.ceil(continuous)

    def candidate(q: int, label: str, zero: bool = False) -> Candidate:
        entry = request.futures_quote.ask if q > 0 else request.futures_quote.bid
        if entry is None:
            entry = (
                request.futures_quote.midpoint
                or request.futures_quote.last
                or request.futures_quote.previous_close
            )
        if entry is None:
            raise ValueError("no futures entry reference")

        def rows(basis: float) -> list[dict[str, float | int]]:
            return [
                r.as_dict()
                for r in state_payoff_table(
                    request.state_grid_bp,
                    kalshi_outcome_bp=request.kalshi_outcome_bp,
                    kalshi_winning_states_bp=winning,
                    kalshi_side=side,
                    kalshi_contracts=0 if zero else request.kalshi_contracts,
                    kalshi_entry_price_dollars=float(price),
                    kalshi_total_fees_dollars=0 if zero else fee,
                    futures_contracts=q,
                    futures_entry_price_points=float(entry),
                    futures_round_trip_cost_per_contract_dollars=request.futures_round_trip_cost_per_contract_dollars,
                    pre_effr_pct=pre,
                    no_change_effr_pct=base,
                    days_in_month=days.days_in_month,
                    pre_days=days.pre_decision_days,
                    post_days=days.post_decision_days,
                    futures_margin_per_contract_dollars=request.futures_margin_per_contract_dollars,
                    slippage_per_kalshi_contract_dollars=float(
                        request.slippage_per_kalshi_contract_dollars
                    ),
                    basis_change_bp=basis,
                )
            ]

        ledger = rows(central_basis)
        stress = [r for b in request.basis_scenarios_bp for r in rows(central_basis + b)]
        if not stress:
            stress = ledger.copy()
        net = [float(r["combined_net_pnl_dollars"]) for r in ledger]
        binary = [
            float(
                r["kalshi_settlement_dollars"]
                - r["kalshi_acquisition_cost_dollars"]
                - r["kalshi_fees_dollars"]
            )
            for r in ledger
        ]

        def weighted(values: list[float]) -> float:
            return float(np.dot(probabilities, values))

        worst = min(ledger, key=lambda r: r["combined_net_pnl_dollars"])
        stressed = min(stress, key=lambda r: r["combined_net_pnl_dollars"])
        first = ledger[0]
        cash = float(
            first["kalshi_acquisition_cost_dollars"]
            + first["kalshi_fees_dollars"]
            + first["futures_costs_dollars"]
        )
        margin = abs(q) * request.futures_margin_per_contract_dollars
        feasible = (
            cash + margin <= request.capital_limit_dollars
            and abs(q) <= request.max_futures_contracts
            and (zero or request.kalshi_contracts <= request.max_kalshi_contracts)
            and float(stressed["combined_net_pnl_dollars"]) >= -request.worst_loss_limit_dollars
        )
        return Candidate(
            label=label,
            futures_contracts=q,
            binary_contracts=0 if zero else request.kalshi_contracts,
            entry_side=(
                "none"
                if q == 0
                else "ask"
                if q > 0 and request.futures_quote.ask is not None
                else "bid"
                if q < 0 and request.futures_quote.bid is not None
                else "indicative reference"
            ),
            entry_price_points=float(entry),
            feasible=feasible,
            ledger=ledger,
            stress=stress,
            economics=Economics(
                binary_model_ev_dollars=weighted(binary),
                binary_feasible_ev_dollars=ledger_bounds(request, mean, binary),
                combined_model_ev_dollars=weighted(net),
                combined_feasible_ev_dollars=ledger_bounds(request, mean, net),
                futures_gross_ev_dollars=weighted(
                    [float(r["futures_gross_pnl_dollars"]) for r in ledger]
                ),
                total_costs_dollars=float(
                    first["kalshi_fees_dollars"]
                    + first["kalshi_slippage_dollars"]
                    + first["futures_costs_dollars"]
                ),
                acquisition_cash_dollars=cash,
                estimated_margin_dollars=margin,
                estimated_cash_and_margin_dollars=cash + margin,
                worst_state_dollars=float(worst["combined_net_pnl_dollars"]),
                stressed_worst_dollars=float(stressed["combined_net_pnl_dollars"]),
                worst_move_bp=int(stressed["move_bp"]),
                worst_basis_change_bp=float(stressed["basis_change_bp"]),
                user_combined_ev_dollars=float(np.dot(request.user_distribution, net))
                if request.user_distribution is not None
                else None,
            ),
        )

    enumerated = [
        candidate(q, "Enumerated")
        for q in range(-request.max_futures_contracts, request.max_futures_contracts + 1)
    ]
    no_position = candidate(0, "No position", True)
    admissible = [c for c in enumerated if c.feasible] + [no_position]
    recommended = max(
        admissible,
        key=lambda c: (c.economics.combined_feasible_ev_dollars[0], -abs(c.futures_contracts)),
    )
    selected_q = (
        request.selected_futures_contracts
        if request.selected_futures_contracts is not None
        else nearest
    )
    selected = candidate(selected_q, "Selected", request.selected_no_position)
    candidates = [
        no_position,
        candidate(0, "Unhedged"),
        candidate(floor, "Floor"),
        candidate(nearest, "Nearest"),
        candidate(ceiling, "Ceiling"),
        selected,
    ]
    candidates.append(recommended.model_copy(update={"label": "Recommended"}))
    evidence: list[Evidence] = []

    def check(name: str, value: bool | None, detail: str) -> None:
        evidence.append(
            Evidence(
                check=name,
                status="unknown" if value is None else "pass" if value else "fail",
                detail=detail,
            )
        )

    month = request.meeting.effective_date.replace(day=1)
    expected_symbol = yahoo_zq_symbol(month.year, month.month)
    check("Contract month", request.futures_quote.instrument == expected_symbol, expected_symbol)
    check(
        "Settlement compatibility",
        None if request.settlement_compatible else False,
        "User assertion requires independent rule verification",
    )
    check(
        "State coverage",
        None if request.all_outcomes_modeled else False,
        "Finite states cannot certify unmodeled tails",
    )
    check(
        "Single meeting",
        not request.multiple_meetings_in_contract,
        "Multiple meetings contaminate the single-meeting inversion",
    )
    for label, quote in (("Futures", request.futures_quote), ("Binary", request.kalshi_yes_quote)):
        age = quote.age_seconds(request.analysis_timestamp)
        check(
            label + " source time",
            None if age is None else -5 <= age <= request.stale_hard_seconds,
            "Five second future clock tolerance",
        )
        check(
            label + " receipt time",
            (quote.receipt_timestamp - request.analysis_timestamp).total_seconds() <= 5,
            quote.receipt_timestamp.isoformat(),
        )
        check(label + " market", quote.market_open, "Open market status required")
        check(
            label + " delay", not quote.delayed, "Delayed observations cannot establish execution"
        )
        check(
            label + " two sided quote",
            quote.has_bid_and_ask and not quote.crossed,
            "Both uncrossed sides required",
        )
    times = [q.source_timestamp for q in (request.futures_quote, request.kalshi_yes_quote)]
    check(
        "Synchronization",
        None
        if times[0] is None or times[1] is None
        else abs((times[0] - times[1]).total_seconds()) <= request.sync_hard_seconds,
        "Source time separation",
    )
    depth = request.kalshi_yes_ask_depth if side == "yes" else request.kalshi_no_ask_depth
    check(
        "Binary selected depth",
        None if depth is None else depth >= request.kalshi_contracts,
        "Selected ask quantity",
    )
    fd = request.futures_ask_depth if selected_q > 0 else request.futures_bid_depth
    check(
        "Futures selected depth",
        True if selected_q == 0 else None if fd is None else fd >= abs(selected_q),
        "Selected entry quantity",
    )
    check(
        "Capital",
        selected.economics.estimated_cash_and_margin_dollars <= request.capital_limit_dollars,
        "Estimated cash plus assumed margin",
    )
    check(
        "Position limits",
        abs(selected_q) <= request.max_futures_contracts
        and request.kalshi_contracts <= request.max_kalshi_contracts,
        "Declared research limits",
    )
    tick = minimum_tick(month, request.analysis_timestamp.date())
    quote_values = [
        v for v in (request.futures_quote.bid, request.futures_quote.ask) if v is not None
    ]
    check(
        "Tick grid",
        all(v % Decimal(str(tick)) == 0 for v in quote_values) if quote_values else None,
        f"{tick} points under Chapter 22 weekday boundary",
    )
    # Exact repricing of nonlinear rounded order fees in each solver evaluation.
    binary_win = sum(
        p
        for s, p in zip(request.state_grid_bp, probabilities, strict=True)
        if (s in winning) == (side == "yes")
    )

    def ev_at_price(x: float) -> float:
        cost = float(
            schedule.total_fee(
                request.kalshi_contracts,
                Decimal(str(x)),
                maker=request.kalshi_order_is_maker,
                zero_fee=request.kalshi_zero_fee,
            )
        )
        return (
            binary_win * request.kalshi_contracts
            - request.kalshi_contracts * (x + float(request.slippage_per_kalshi_contract_dollars))
            - cost
            + selected.economics.futures_gross_ev_dollars
            - abs(selected_q) * request.futures_round_trip_cost_per_contract_dollars
        )

    lo, hi = 0.0, 1.0
    break_even = None
    if ev_at_price(lo) >= 0:
        for _ in range(60):
            mid = (lo + hi) / 2
            if ev_at_price(mid) >= 0:
                lo = mid
            else:
                hi = mid
        break_even = lo
    sensitivities = [
        {"binary_price_dollars": x, "combined_ev_dollars": ev_at_price(x)}
        for x in [max(0, float(price) - 0.05), float(price), min(1, float(price) + 0.05)]
    ]
    from ..math.probabilities import regularized_distribution, two_state_distribution

    center = (
        request.futures_quote.midpoint
        or request.futures_quote.last
        or request.futures_quote.previous_close
    )
    futures_sensitivity = []
    if center is not None:
        for steps in (-2, -1, 0, 1, 2):
            delta = tick * steps
            shifted_mean = mean - delta * days.days_in_month / days.post_decision_days * 100
            item: dict[str, Any] = {
                "futures_reference_points": float(center) + delta,
                "expected_move_bp": shifted_mean,
            }
            try:
                shifted_values = [
                    float(r["combined_net_pnl_dollars"]) - selected_q * 4167 * delta
                    for r in selected.ledger
                ]
                bounds = ledger_bounds(request, shifted_mean, shifted_values)
                if request.probability_mode == "two_state":
                    dist = two_state_distribution(
                        shifted_mean, request.two_state_low_bp, request.kalshi_outcome_bp
                    )
                    masses = [
                        dict(zip(dist.states_bp, dist.probabilities, strict=True)).get(s, 0)
                        for s in request.state_grid_bp
                    ]
                else:
                    masses = regularized_distribution(
                        request.state_grid_bp,
                        shifted_mean,
                        request.prior
                        or [1 / len(request.state_grid_bp)] * len(request.state_grid_bp),
                        request.state_probability_bounds,
                    ).probabilities
                item.update(
                    {
                        "status": "feasible",
                        "combined_ev_dollars": float(np.dot(masses, shifted_values)),
                        "combined_feasible_ev_dollars": bounds,
                    }
                )
            except ValueError:
                item["status"] = "infeasible"
            futures_sensitivity.append(item)
    user_mean = (
        float(np.dot(request.user_distribution, request.state_grid_bp))
        if request.user_distribution is not None
        else None
    )
    return Workbench(
        expected_move_bp=mean,
        probability_interval=probability_interval,
        continuous_pair_hedge=continuous,
        benchmark_states_bp=(low, high),
        hedge_objective="Highest conservative combined EV within stressed loss and capital limits",
        recommendation="No position"
        if recommended.binary_contracts == 0
        else "Whole-contract candidate",
        recommended_futures_contracts=recommended.futures_contracts,
        selected_futures_contracts=selected_q,
        selected=selected,
        candidates=candidates,
        evidence=evidence,
        economics_status="Nonnegative modeled payoffs"
        if selected.economics.worst_state_dollars >= 0
        else "Modeled downside",
        market_distribution=probabilities,
        user_distribution=request.user_distribution,
        user_expected_move_bp=user_mean,
        user_moment_residual_bp=user_mean - mean if user_mean is not None else None,
        break_even_binary_price_dollars=break_even,
        sensitivities=sensitivities,
        futures_price_sensitivities=futures_sensitivity,
        conventions={
            "cash_multiplier_dollars_per_point": 4167,
            "settlement_rate_rounding_pct": 0.001,
            "settlement_rounding": "half up",
            "inversion": "continuous analytical approximation",
            "pre_effective_average_pct": pre,
            "no_change_post_effr_pct": base,
            "pre_effective_assumption": request.pre_effective_rate_assumption,
            "settlement_month": month.isoformat(),
            "minimum_tick_points": tick,
            "fee_rounding_dollars": str(request.kalshi_fee_rounding_dollars),
            "fee_reference_checked": "2026-10-14",
            "fee_reference_effective": "2026-07-07",
            "fee_reference_url": "https://kalshi.com/docs/kalshi-fee-schedule.pdf",
            "settlement_reference_url": "https://www.cmegroup.com/rulebook/CBOT/III/22.pdf",
            "return_denominator": "Estimated acquisition cash plus fees and assumed futures margin",
            "capital_limit": "Not brokerage approved or a variation-margin liquidity estimate",
        },
    )


class AnalysisResponse(BaseModel):
    """Versioned response common to historical, manual and live analysis."""

    model_config = ConfigDict(extra="allow")
    schema_version: Literal["2.0"]
    model_version: str
    inputs: AnalysisRequest
    workbench: Workbench
