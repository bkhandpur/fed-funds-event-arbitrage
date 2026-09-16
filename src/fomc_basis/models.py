from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from .enums import ObservationKind, QuoteMode, RiskFlag, TradeClassification


def utc_now() -> datetime:
    return datetime.now(UTC)


class Quote(BaseModel):
    """Market observation. Futures prices are points; binary prices are dollars."""

    model_config = ConfigDict(frozen=True)
    instrument: str
    source: str
    source_timestamp: datetime | None = None
    receipt_timestamp: datetime = Field(default_factory=utc_now)
    bid: Decimal | None = None
    ask: Decimal | None = None
    last: Decimal | None = None
    previous_close: Decimal | None = None
    price_precision: Decimal | None = None
    delayed: bool = False
    market_open: bool | None = None
    raw: dict[str, Any] = Field(default_factory=dict)
    observation_kind: ObservationKind = ObservationKind.LIVE_INDICATIVE

    @model_validator(mode="after")
    def validate_market(self) -> Quote:
        for value in (self.bid, self.ask, self.last, self.previous_close):
            if value is not None and not value.is_finite():
                raise ValueError("quote prices must be finite")
        for timestamp in (self.source_timestamp, self.receipt_timestamp):
            if timestamp is not None and timestamp.tzinfo is None:
                raise ValueError("quote timestamps must include a UTC offset")
        return self

    @computed_field
    @property
    def mode(self) -> QuoteMode:
        return (
            QuoteMode.EXECUTABLE
            if self.bid is not None and self.ask is not None
            else QuoteMode.INDICATIVE_ONLY
        )

    @computed_field
    @property
    def has_bid_and_ask(self) -> bool:
        return self.bid is not None and self.ask is not None

    @computed_field
    @property
    def crossed(self) -> bool:
        return self.bid is not None and self.ask is not None and self.bid > self.ask

    @property
    def midpoint(self) -> Decimal | None:
        if self.bid is None or self.ask is None:
            return None
        return (self.bid + self.ask) / Decimal("2")

    def age_seconds(self, at: datetime | None = None) -> float | None:
        if self.source_timestamp is None:
            return None
        at = at or utc_now()
        return (at - self.source_timestamp).total_seconds()


class FOMCMeeting(BaseModel):
    start_date: date
    decision_date: date
    statement_time_et: str = "14:00"
    effective_date: date
    source: str = "Federal Reserve"

    @model_validator(mode="after")
    def validate_dates(self) -> FOMCMeeting:
        if self.start_date > self.decision_date:
            raise ValueError("meeting start date cannot follow its decision date")
        if self.effective_date < self.decision_date:
            raise ValueError("effective date cannot precede the decision date")
        return self


class DayCount(BaseModel):
    decision_date: date
    effective_date: date
    days_in_month: int
    pre_decision_days: int
    post_decision_days: int


class OrderBookLevel(BaseModel):
    price_dollars: Decimal = Field(ge=0, le=1)
    quantity: Decimal = Field(ge=0)


class KalshiMarket(BaseModel):
    ticker: str
    title: str
    subtitle: str = ""
    rules: str = ""
    settlement_description: str = ""
    close_time: datetime | None = None
    status: str = "unknown"
    volume: Decimal | None = Field(default=None, ge=0)
    open_interest: Decimal | None = Field(default=None, ge=0)
    yes_bids: list[OrderBookLevel] = Field(default_factory=list)
    no_bids: list[OrderBookLevel] = Field(default_factory=list)
    outcome_move_bp: int | None = None
    outcome_bucket: str | None = None
    strike_type: str | None = None
    threshold_pct: Decimal | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class ProbabilityResult(BaseModel):
    states_bp: list[int]
    probabilities: list[float]
    bounds: dict[int, tuple[float, float]] = Field(default_factory=dict)
    prior: list[float] | None = None
    status: str = "ok"
    residuals: dict[str, float] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class ClassificationResult(BaseModel):
    label: TradeClassification
    reason_codes: list[str]
    risk_flags: set[RiskFlag] = Field(default_factory=set)
    worst_case_pnl_dollars: float | None = None
    expected_value_dollars: float | None = None


class AnalysisRequest(BaseModel):
    """Validated inputs for one meeting-level cross-market analysis.

    Rates use percentage points, policy moves use basis points, futures prices
    use price points, and binary prices use dollars.
    """

    meeting: FOMCMeeting
    futures_quote: Quote
    kalshi_yes_quote: Quote
    current_effr_pct: float
    no_change_effr_pct: float | None = None
    kalshi_outcome_bp: int = 25
    kalshi_winning_states_bp: list[int] | None = None
    two_state_low_bp: int = 0
    kalshi_contracts: int = Field(default=500, gt=0)
    state_grid_bp: list[int] = Field(default_factory=lambda: [-50, -25, 0, 25, 50, 75])
    probability_mode: str = "bounds"
    prior: list[float] | None = None
    state_probability_bounds: dict[int, tuple[float, float]] = Field(default_factory=dict)
    basis_scenarios_bp: list[int] = Field(default_factory=lambda: [0, -1, 1, -2, 2])
    kalshi_fee_coefficient: Decimal = Field(default=Decimal("0.07"), ge=0)
    kalshi_maker_fee_coefficient: Decimal = Field(default=Decimal("0"), ge=0)
    kalshi_settlement_fee_per_contract_dollars: Decimal = Field(default=Decimal("0"), ge=0)
    kalshi_fee_schedule_name: str = "configurable-standard"
    kalshi_fee_schedule_effective_date: date | None = None
    kalshi_order_is_maker: bool = False
    kalshi_zero_fee: bool = False
    futures_round_trip_cost_per_contract_dollars: float = Field(default=6.04, ge=0)
    futures_margin_per_contract_dollars: float = Field(default=2_000, ge=0)
    slippage_per_kalshi_contract_dollars: Decimal = Field(default=Decimal("0"), ge=0)
    ev_hurdle_dollars: float = Field(default=0.01, ge=0)
    probability_tolerance: float = Field(default=1e-8, gt=0)
    arbitrage_tolerance_dollars: float = Field(default=1e-8, gt=0)
    capital_limit_dollars: float = Field(default=10_000, gt=0)
    max_kalshi_contracts: int = Field(default=1_000, gt=0)
    max_futures_contracts: int = Field(default=10, ge=0)
    settlement_compatible: bool = False
    all_outcomes_modeled: bool = True
    multiple_meetings_in_contract: bool = False
    kalshi_yes_ask_depth: Decimal | None = Field(default=None, ge=0)
    kalshi_no_ask_depth: Decimal | None = Field(default=None, ge=0)
    analysis_timestamp: datetime = Field(default_factory=utc_now)
    stale_warning_seconds: float = 60
    stale_hard_seconds: float = 300
    sync_hard_seconds: float = 60
    minimum_futures_precision: Decimal = Decimal("0.0025")
    current_effr_target_basis_bp: float | None = None
    assumed_post_meeting_basis_bp: float | None = None
    historical_average_basis_bp: float | None = None

    @model_validator(mode="after")
    def validate_analysis(self) -> AnalysisRequest:
        if self.kalshi_outcome_bp not in self.state_grid_bp:
            raise ValueError("Kalshi outcome must be present in the modeled state grid")
        if self.kalshi_winning_states_bp is not None:
            if not self.kalshi_winning_states_bp:
                raise ValueError("Kalshi winning states must not be empty")
            if any(state not in self.state_grid_bp for state in self.kalshi_winning_states_bp):
                raise ValueError("Kalshi winning states must be present in the modeled state grid")
        if self.probability_mode == "two_state" and self.two_state_low_bp not in self.state_grid_bp:
            raise ValueError("two-state low outcome must be present in the modeled state grid")
        if len(set(self.state_grid_bp)) != len(self.state_grid_bp):
            raise ValueError("state grid must not contain duplicates")
        if self.probability_mode not in {"two_state", "tails", "bounds", "regularized"}:
            raise ValueError("probability_mode must be two_state, tails, bounds, or regularized")
        if self.prior is not None and len(self.prior) != len(self.state_grid_bp):
            raise ValueError("prior must match the state grid")
        if self.prior is not None and any(value <= 0 for value in self.prior):
            raise ValueError("prior probabilities must be positive")
        if any(state not in self.state_grid_bp for state in self.state_probability_bounds):
            raise ValueError("probability bounds must refer to states in the state grid")
        for lower, upper in self.state_probability_bounds.values():
            if not 0 <= lower <= upper <= 1:
                raise ValueError("probability bounds must satisfy 0 <= lower <= upper <= 1")
        if self.probability_mode == "tails" and not self.state_probability_bounds:
            raise ValueError("tails mode requires at least one state probability or bound")
        for value in (
            self.kalshi_yes_quote.bid,
            self.kalshi_yes_quote.ask,
            self.kalshi_yes_quote.last,
            self.kalshi_yes_quote.previous_close,
        ):
            if value is not None and not Decimal("0") <= value <= Decimal("1"):
                raise ValueError("Kalshi prices must be between zero and one dollar")
        if self.analysis_timestamp.tzinfo is None:
            raise ValueError("analysis timestamp must include a UTC offset")
        if not 0 <= self.stale_warning_seconds <= self.stale_hard_seconds:
            raise ValueError("stale warning threshold must not exceed the hard threshold")
        if self.sync_hard_seconds < 0:
            raise ValueError("synchronization threshold must be nonnegative")
        if (
            self.assumed_post_meeting_basis_bp is not None
            or self.historical_average_basis_bp is not None
        ) and self.current_effr_target_basis_bp is None:
            raise ValueError("post-meeting basis assumptions require the current observed basis")
        return self


class SnapshotResult(BaseModel):
    analysis_timestamp: datetime = Field(default_factory=utc_now)
    meeting: FOMCMeeting | None = None
    curve_meetings: list[FOMCMeeting] = Field(default_factory=list)
    futures_quotes: list[Quote] = Field(default_factory=list)
    kalshi_markets: list[KalshiMarket] = Field(default_factory=list)
    effr_effective_date: date | None = None
    effr_pct: float | None = None
    target_lower_pct: float | None = None
    target_upper_pct: float | None = None
    target_midpoint_pct: float | None = None
    effr_target_basis_bp: float | None = None
    provider_status: dict[str, str] = Field(default_factory=dict)
    stored_ids: dict[str, list[str]] = Field(default_factory=dict)
