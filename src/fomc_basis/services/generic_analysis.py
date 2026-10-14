from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any, cast

from ..day_count import meeting_day_count
from ..enums import QuoteMode, RiskFlag
from ..fees import KalshiFeeSchedule
from ..math.arbitrage import classify
from ..math.expected_value import (
    binary_ev_per_contract,
    break_even_probability,
    expected_return_on_cash,
)
from ..math.fed_funds import (
    equivalent_probability_25bp,
    expected_move_bp,
    expected_post_meeting_effr_pct,
    quote_probability_sensitivity,
)
from ..math.probabilities import (
    probability_bounds,
    regularized_distribution,
    subset_probability_bounds,
    two_state_distribution,
)
from ..models import AnalysisRequest, ProbabilityResult
from ..validation import arbitrage_quote_gate, quote_risk_flags
from .workbench import construct_workbench


def _reference_prices(request: AnalysisRequest) -> dict[str, float]:
    quote = request.futures_quote
    prices: dict[str, float] = {}
    if quote.bid is not None:
        prices["bid"] = float(quote.bid)
    if quote.ask is not None:
        prices["ask"] = float(quote.ask)
    if quote.midpoint is not None:
        prices["midpoint"] = float(quote.midpoint)
    elif quote.last is not None:
        prices["indicative_last"] = float(quote.last)
    elif quote.previous_close is not None:
        prices["indicative_previous_close"] = float(quote.previous_close)
    if not prices:
        raise ValueError("futures quote has no usable bid, ask, last, or previous close")
    return prices


def _probability_model(
    request: AnalysisRequest, expected_move_bp_value: float
) -> tuple[ProbabilityResult, str]:
    states = request.state_grid_bp
    warnings: list[str] = []
    if request.probability_mode == "two_state":
        result = two_state_distribution(
            expected_move_bp_value, request.two_state_low_bp, request.kalshi_outcome_bp
        )
        if result.status != "ok":
            raise ValueError("two-state model is infeasible for the futures-implied expectation")
        exact = {
            state: (probability, probability)
            for state, probability in zip(result.states_bp, result.probabilities, strict=True)
        }
        selected = [exact.get(state, (0.0, 0.0))[0] for state in states]
        for state, probability in zip(states, selected, strict=True):
            lower, upper = request.state_probability_bounds.get(state, (0.0, 1.0))
            if (
                probability < lower - request.probability_tolerance
                or probability > upper + request.probability_tolerance
            ):
                raise ValueError(
                    "two-state assumptions are infeasible with declared probability bounds"
                )
        return (
            ProbabilityResult(
                states_bp=states,
                probabilities=selected,
                bounds={
                    state: (value, value) for state, value in zip(states, selected, strict=True)
                },
                status=result.status,
                residuals=result.residuals,
                warnings=result.warnings,
            ),
            "two_state_exact",
        )

    bounds = probability_bounds(states, expected_move_bp_value, request.state_probability_bounds)
    prior = request.prior or [1.0 / len(states)] * len(states)
    selected_result = regularized_distribution(
        states,
        expected_move_bp_value,
        prior,
        request.state_probability_bounds,
    )
    warnings.extend(selected_result.warnings)
    selection = "minimum_kl_to_prior"
    if request.probability_mode == "bounds":
        warnings.append("Bounds mode uses a model-selected distribution only for point EV display.")
    elif request.probability_mode == "tails":
        warnings.append(
            "User-specified tail constraints condition, but do not uniquely identify, the result."
        )
        selection = "user_constrained_minimum_kl_to_prior"
    selected_result.bounds = bounds
    selected_result.warnings = warnings
    return selected_result, selection


def _binary_prices(request: AnalysisRequest) -> tuple[Decimal | None, Decimal | None, bool]:
    quote = request.kalshi_yes_quote
    yes_price = quote.ask
    if yes_price is None:
        yes_price = quote.last if quote.last is not None else quote.previous_close
    no_price = Decimal("1") - quote.bid if quote.bid is not None else None
    if no_price is None and quote.last is not None:
        no_price = Decimal("1") - quote.last
    if no_price is None and quote.previous_close is not None:
        no_price = Decimal("1") - quote.previous_close
    executable = quote.mode == QuoteMode.EXECUTABLE
    return yes_price, no_price, executable


def _ev_summary(
    probability: float,
    probability_bounds_for_outcome: tuple[float, float],
    price: Decimal | None,
    contracts: int,
    fee_schedule: KalshiFeeSchedule,
    slippage: Decimal,
    side: str,
    futures_margin_dollars: float,
    maker: bool,
    zero_fee: bool,
) -> dict[str, Any]:
    if price is None:
        return {"available": False, "reason": "NO_USABLE_PRICE"}
    total_fee = fee_schedule.total_fee(contracts, price, maker=maker, zero_fee=zero_fee)
    fee_per_contract = total_fee / contracts
    low, high = probability_bounds_for_outcome
    model_ev = binary_ev_per_contract(probability, price, fee_per_contract, slippage, side=side)
    conservative_probability = low if side == "yes" else high
    conservative_ev = binary_ev_per_contract(
        conservative_probability,
        price,
        fee_per_contract,
        slippage,
        side=side,
    )
    acquisition_cost = float(price * contracts + total_fee + slippage * contracts)
    total_capital = acquisition_cost + futures_margin_dollars
    model_order_ev = float(model_ev) * contracts
    conservative_order_ev = float(conservative_ev) * contracts

    def roc_price(hurdle: float) -> float | None:
        win_probability = probability if side == "yes" else 1 - probability

        def excess(candidate: Decimal) -> float:
            fee = fee_schedule.total_fee(contracts, candidate, maker=maker, zero_fee=zero_fee)
            costs = fee + slippage * contracts
            ev = win_probability * contracts - float(candidate * contracts + costs)
            capital = float(candidate * contracts + costs) + futures_margin_dollars
            return ev - hurdle * capital

        low, high = Decimal("0"), Decimal("1")
        if excess(low) < 0:
            return None
        for _ in range(60):
            midpoint = (low + high) / 2
            if excess(midpoint) >= 0:
                low = midpoint
            else:
                high = midpoint
        return float(low)

    def price_hurdle(hurdle: float) -> float | None:
        win_probability = probability if side == "yes" else 1 - probability

        def excess(candidate: Decimal) -> float:
            fee = fee_schedule.total_fee(contracts, candidate, maker=maker, zero_fee=zero_fee)
            return win_probability - float(candidate + fee / contracts + slippage) - hurdle

        low, high = Decimal(0), Decimal(1)
        if excess(low) < 0:
            return None
        for _ in range(60):
            midpoint = (low + high) / 2
            if excess(midpoint) >= 0:
                low = midpoint
            else:
                high = midpoint
        return float(low)

    return {
        "available": True,
        "price_dollars": float(price),
        "fee_total_dollars": float(total_fee),
        "fee_per_contract_dollars": float(fee_per_contract),
        "model_probability": probability if side == "yes" else 1 - probability,
        "conservative_probability": low if side == "yes" else 1 - high,
        "model_ev_per_contract_dollars": float(model_ev),
        "conservative_ev_per_contract_dollars": float(conservative_ev),
        "model_ev_order_dollars": model_order_ev,
        "conservative_ev_order_dollars": conservative_order_ev,
        "expected_return_on_cash": expected_return_on_cash(
            model_order_ev,
            float(price * contracts),
            float(total_fee + slippage * contracts),
        ),
        "expected_return_on_total_capital": (
            model_order_ev / total_capital if total_capital > 0 else None
        ),
        "estimated_total_capital_dollars": total_capital,
        "return_denominator": "Binary acquisition cash including entry costs",
        "break_even_outcome_probability": break_even_probability(
            price, fee_per_contract, slippage, side=side
        ),
        "break_even_probability": break_even_probability(
            price, fee_per_contract, slippage, side=side
        ),
        "break_even_price_dollars": price_hurdle(0),
        "ev_hurdle_prices": {
            f"{int(hurdle * 100)}pct": price_hurdle(hurdle) for hurdle in (0.01, 0.02, 0.05, 0.10)
        },
        "return_on_capital_hurdle_prices": {
            f"{int(hurdle * 100)}pct": roc_price(hurdle) for hurdle in (0.01, 0.02, 0.05, 0.10)
        },
    }


def analyze_request(request: AnalysisRequest) -> dict[str, Any]:
    """Analyze any meeting using validated manual, replayed, or live provider quotes."""
    # Settlement is in the effective month, including first-day rollover.
    day_count = meeting_day_count(
        request.meeting.effective_date.replace(day=1), request.meeting.effective_date
    )
    day_count = day_count.model_copy(update={"decision_date": request.meeting.decision_date})
    if day_count.post_decision_days == 0:
        raise ValueError("decision effective after month-end; use the following ZQ contract")
    selected_post_basis_bp = (
        request.assumed_post_meeting_basis_bp
        if request.assumed_post_meeting_basis_bp is not None
        else request.historical_average_basis_bp
    )
    central_basis_change_bp = (
        selected_post_basis_bp - request.current_effr_target_basis_bp
        if selected_post_basis_bp is not None and request.current_effr_target_basis_bp is not None
        else 0.0
    )
    no_change_effr_pct = (
        request.no_change_effr_pct
        if request.no_change_effr_pct is not None
        else request.current_effr_pct
    ) + central_basis_change_bp / 100.0
    prices = _reference_prices(request)
    implied: dict[str, dict[str, float]] = {}
    for label, price in prices.items():
        post_effr = expected_post_meeting_effr_pct(
            price,
            day_count.days_in_month,
            day_count.pre_decision_days,
            day_count.post_decision_days,
            request.pre_effective_effr_average_pct
            if request.pre_effective_effr_average_pct is not None
            else request.current_effr_pct,
        )
        move = expected_move_bp(post_effr, no_change_effr_pct)
        implied[label] = {
            "futures_price_points": price,
            "post_meeting_effr_pct": post_effr,
            "expected_move_bp": move,
            "25_BP_EQUIVALENT_PROBABILITY": equivalent_probability_25bp(move),
        }
    center_label = "midpoint" if "midpoint" in implied else next(iter(implied))
    center_move = implied[center_label]["expected_move_bp"]
    probability_result, selection_method = _probability_model(request, center_move)
    bounds = probability_result.bounds
    selected = probability_result.probabilities
    winning_states = request.kalshi_winning_states_bp or [request.kalshi_outcome_bp]
    outcome_probability = sum(
        selected[request.state_grid_bp.index(state)] for state in winning_states
    )
    if len(winning_states) == 1:
        outcome_bounds = bounds[winning_states[0]]
    else:
        outcome_bounds = subset_probability_bounds(
            request.state_grid_bp,
            center_move,
            winning_states,
            request.state_probability_bounds,
        )

    fee_schedule = KalshiFeeSchedule(
        name=request.kalshi_fee_schedule_name,
        coefficient=request.kalshi_fee_coefficient,
        maker_coefficient=request.kalshi_maker_fee_coefficient,
        settlement_fee_per_contract=request.kalshi_settlement_fee_per_contract_dollars,
        effective_date=request.kalshi_fee_schedule_effective_date,
        rounding_dollars=request.kalshi_fee_rounding_dollars,
    )
    # These summaries describe the binary leg only. Combined cash and margin
    # are computed from actual selected whole-contract candidates below.
    estimated_margin = 0.0
    yes_price, no_price, binary_executable = _binary_prices(request)
    yes_ev = _ev_summary(
        outcome_probability,
        outcome_bounds,
        yes_price,
        request.kalshi_contracts,
        fee_schedule,
        request.slippage_per_kalshi_contract_dollars,
        "yes",
        estimated_margin,
        request.kalshi_order_is_maker,
        request.kalshi_zero_fee,
    )
    no_ev = _ev_summary(
        outcome_probability,
        outcome_bounds,
        no_price,
        request.kalshi_contracts,
        fee_schedule,
        request.slippage_per_kalshi_contract_dollars,
        "no",
        estimated_margin,
        request.kalshi_order_is_maker,
        request.kalshi_zero_fee,
    )
    sides = {"yes": yes_ev, "no": no_ev}
    midpoint_expected_value: dict[str, Any] | None = None
    if request.kalshi_yes_quote.midpoint is not None:
        midpoint = request.kalshi_yes_quote.midpoint
        midpoint_expected_value = {
            "yes": _ev_summary(
                outcome_probability,
                outcome_bounds,
                midpoint,
                request.kalshi_contracts,
                fee_schedule,
                request.slippage_per_kalshi_contract_dollars,
                "yes",
                estimated_margin,
                request.kalshi_order_is_maker,
                request.kalshi_zero_fee,
            ),
            "no": _ev_summary(
                outcome_probability,
                outcome_bounds,
                Decimal("1") - midpoint,
                request.kalshi_contracts,
                fee_schedule,
                request.slippage_per_kalshi_contract_dollars,
                "no",
                estimated_margin,
                request.kalshi_order_is_maker,
                request.kalshi_zero_fee,
            ),
        }
    available_sides = [
        (side, summary) for side, summary in sides.items() if summary.get("available") is True
    ]
    if not available_sides:
        raise ValueError("Kalshi quote has no usable bid, ask, last, or previous close")
    chosen_side, chosen_ev = max(
        available_sides,
        key=lambda item: item[1]["conservative_ev_order_dollars"],
    )

    if request.selected_binary_side is not None:
        chosen_side = request.selected_binary_side
        chosen_ev = sides[chosen_side]
        if not chosen_ev.get("available"):
            raise ValueError("selected binary side has no usable acquisition quote")
    chosen_binary_price = yes_price if chosen_side == "yes" else no_price
    if chosen_binary_price is None:
        raise ValueError("chosen side has no usable acquisition quote")
    workbench = construct_workbench(
        request,
        day_count,
        chosen_side,
        chosen_binary_price,
        selected,
        center_move,
        outcome_bounds,
        central_basis_change_bp,
    )
    selected_candidate = workbench.selected
    futures_contracts = selected_candidate.futures_contracts
    continuous_hedge = workbench.continuous_pair_hedge
    pair_low, pair_high = workbench.benchmark_states_bp
    pair_values = {
        int(row["move_bp"]): float(row["combined_net_pnl_dollars"])
        for row in selected_candidate.ledger
    }
    hedge_residual = pair_values[pair_high] - pair_values[pair_low]
    future_entry = Decimal(str(selected_candidate.entry_price_points))
    from ..math.payoff import StatePayoff

    payoffs = [StatePayoff(**cast(dict[str, Any], row)) for row in selected_candidate.ledger]
    basis_stress_rows = [
        StatePayoff(**cast(dict[str, Any], row)) for row in selected_candidate.stress
    ]

    quote_flags = quote_risk_flags(
        [request.futures_quote, request.kalshi_yes_quote],
        request.analysis_timestamp,
        stale_warning_seconds=request.stale_warning_seconds,
        stale_hard_seconds=request.stale_hard_seconds,
        sync_hard_seconds=request.sync_hard_seconds,
        minimum_futures_precision=request.minimum_futures_precision,
    )
    quote_gate, quote_reasons = arbitrage_quote_gate(
        [request.futures_quote, request.kalshi_yes_quote],
        request.analysis_timestamp,
        request.stale_hard_seconds,
        request.sync_hard_seconds,
    )
    quote_flags.update({RiskFlag.BASIS_RISK, RiskFlag.ASYNC_EXECUTION})
    if (
        request.kalshi_fee_coefficient
        or request.kalshi_settlement_fee_per_contract_dollars
        or request.futures_round_trip_cost_per_contract_dollars
        or request.slippage_per_kalshi_contract_dollars
    ):
        quote_flags.add(RiskFlag.TRANSACTION_COSTS)
    if request.probability_mode != "two_state" or len(request.state_grid_bp) > 2:
        quote_flags.add(RiskFlag.MODEL_UNDERIDENTIFIED)
    quote_flags.add(RiskFlag.HOLIDAY_CARRY)
    if request.futures_quote.price_precision is not None:
        quote_flags.add(RiskFlag.QUOTE_ROUNDING)
    if request.multiple_meetings_in_contract:
        quote_flags.add(RiskFlag.MULTIPLE_MEETINGS)
    if len(request.state_grid_bp) > 2:
        quote_flags.add(RiskFlag.TAIL_RISK)
    if not request.settlement_compatible:
        quote_flags.add(RiskFlag.SETTLEMENT_MISMATCH)
    selected_depth = (
        request.kalshi_yes_ask_depth if chosen_side == "yes" else request.kalshi_no_ask_depth
    )
    depth_sufficient = selected_depth is not None and selected_depth >= request.kalshi_contracts
    if not depth_sufficient:
        quote_flags.add(RiskFlag.INSUFFICIENT_DEPTH)
        if selected_depth is None:
            quote_flags.add(RiskFlag.MISSING_BOOK_SIDE)
        elif selected_depth > 0:
            quote_flags.add(RiskFlag.PARTIAL_FILL)
    if abs(hedge_residual) > 1e-8:
        quote_flags.add(RiskFlag.INTEGER_HEDGE)
    position_limits_satisfied = (
        request.kalshi_contracts <= request.max_kalshi_contracts
        and abs(futures_contracts) <= request.max_futures_contracts
    )
    if not position_limits_satisfied:
        quote_flags.add(RiskFlag.POSITION_LIMIT)
    capital_required = max(row.capital_required_dollars for row in payoffs)
    capital_sufficient = capital_required <= request.capital_limit_dollars
    if not capital_sufficient:
        quote_flags.add(RiskFlag.CAPITAL_LIMIT)
    classification = classify(
        [row.combined_net_pnl_dollars for row in payoffs],
        workbench.selected.economics.combined_feasible_ev_dollars[0],
        quote_flags,
        executable=False,  # Research inputs do not independently certify execution.
        all_outcomes_modeled=request.all_outcomes_modeled,
        settlement_compatible=request.settlement_compatible,
        depth_sufficient=depth_sufficient,
        integer_sizing=True,
        basis_stress_pnl_dollars=[row.combined_net_pnl_dollars for row in basis_stress_rows],
        position_limits_satisfied=position_limits_satisfied,
        capital_sufficient=capital_sufficient,
        ev_hurdle_dollars=request.ev_hurdle_dollars,
        tolerance=request.arbitrage_tolerance_dollars,
    )
    classification.reason_codes = sorted(
        set(
            classification.reason_codes
            + quote_reasons
            + [
                e.check.upper().replace(" ", "_") + "_UNVERIFIED"
                for e in workbench.evidence
                if e.status != "pass"
            ]
        )
    )
    classification.expected_value_dollars = workbench.selected.economics.combined_model_ev_dollars
    q_values = [item["25_BP_EQUIVALENT_PROBABILITY"] for item in implied.values()]
    source_timestamps = [
        quote.source_timestamp
        for quote in (request.futures_quote, request.kalshi_yes_quote)
        if quote.source_timestamp is not None
    ]
    cross_venue_seconds = (
        (max(source_timestamps) - min(source_timestamps)).total_seconds()
        if len(source_timestamps) == 2
        else None
    )
    spread = (
        float(request.futures_quote.ask - request.futures_quote.bid)
        if request.futures_quote.ask is not None and request.futures_quote.bid is not None
        else None
    )
    sensitivity = quote_probability_sensitivity(
        day_count.days_in_month, day_count.post_decision_days
    )
    return {
        "input_revision": hashlib.sha256(
            json.dumps(request.model_dump(mode="json"), sort_keys=True).encode()
        ).hexdigest(),
        "schema_version": "2.0",
        "model_version": "2.0.0",
        "workbench": workbench.model_dump(mode="json"),
        "meeting": request.meeting.model_dump(mode="json"),
        "analysis_timestamp": request.analysis_timestamp.isoformat(),
        "day_count": day_count.model_dump(mode="json"),
        "inputs": request.model_dump(mode="json"),
        "quote_quality": {
            "futures_mode": request.futures_quote.mode.value,
            "kalshi_mode": request.kalshi_yes_quote.mode.value,
            "futures_age_seconds": request.futures_quote.age_seconds(request.analysis_timestamp),
            "kalshi_age_seconds": request.kalshi_yes_quote.age_seconds(request.analysis_timestamp),
            "cross_venue_timestamp_difference_seconds": cross_venue_seconds,
            "warning_threshold_seconds": request.stale_warning_seconds,
            "hard_stale_threshold_seconds": request.stale_hard_seconds,
            "synchronization_threshold_seconds": request.sync_hard_seconds,
        },
        "futures_implied": implied,
        "25_BP_EQUIVALENT_PROBABILITY_INTERVAL": [min(q_values), max(q_values)],
        "probability_model": {
            "mode": request.probability_mode,
            "selection_method": selection_method,
            "selected_distribution": dict(zip(request.state_grid_bp, selected, strict=True)),
            "bounds": bounds,
            "prior": probability_result.prior,
            "optimization_status": probability_result.status,
            "constraint_residuals": probability_result.residuals,
            "warnings": probability_result.warnings,
            "kalshi_event_winning_states_bp": winning_states,
            "kalshi_event_probability": outcome_probability,
            "kalshi_event_probability_bounds": outcome_bounds,
        },
        "expected_value": sides,
        "midpoint_theoretical_expected_value": midpoint_expected_value,
        "fee_schedule": {
            "name": fee_schedule.name,
            "effective_date": fee_schedule.effective_date,
            "taker_coefficient": fee_schedule.coefficient,
            "maker_coefficient": fee_schedule.maker_coefficient,
            "settlement_fee_per_contract_dollars": fee_schedule.settlement_fee_per_contract,
            "zero_fee_override": request.kalshi_zero_fee,
        },
        "basis_model": {
            "current_effr_target_basis_bp": request.current_effr_target_basis_bp,
            "assumed_post_meeting_basis_bp": request.assumed_post_meeting_basis_bp,
            "historical_average_basis_bp": request.historical_average_basis_bp,
            "selected_post_meeting_basis_bp": selected_post_basis_bp,
            "central_basis_change_bp": central_basis_change_bp,
            "stress_changes_bp": request.basis_scenarios_bp,
        },
        "limits": {
            "capital_limit_dollars": request.capital_limit_dollars,
            "capital_required_dollars": capital_required,
            "max_kalshi_contracts": request.max_kalshi_contracts,
            "max_futures_contracts": request.max_futures_contracts,
            "position_limits_satisfied": position_limits_satisfied,
            "capital_sufficient": capital_sufficient,
        },
        "chosen_side": chosen_side,
        "hedge": {
            "continuous_futures_contracts": continuous_hedge,
            "nearest_futures_contracts": futures_contracts,
            "nearest_residual_dollars": hedge_residual,
            "entry_side": selected_candidate.entry_side,
            "entry_price_points": float(future_entry),
            "scope_warning": "Pair flattening benchmark uses actual payout and settlement differences",
        },
        "quote_sensitivity": {
            "dq_d_futures_price_point": sensitivity,
            "one_minimum_tick_probability_change": abs(sensitivity * 0.0025),
            "current_spread_probability_change": (
                abs(sensitivity * spread) if spread is not None else None
            ),
            "one_bp_no_change_effr_probability_change": 0.04,
        },
        "state_payoffs": [row.as_dict() for row in payoffs],
        "basis_stress": [row.as_dict() for row in basis_stress_rows],
        "classification": classification.model_dump(mode="json"),
        "research_only": True,
    }
