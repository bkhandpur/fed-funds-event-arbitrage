from __future__ import annotations

import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any, Literal

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from fomc_basis.models import AnalysisRequest  # noqa: E402
from fomc_basis.providers.federal_reserve import FederalReserveCalendarProvider  # noqa: E402
from fomc_basis.services.analysis import analyze_september_2026_fixture  # noqa: E402
from fomc_basis.services.generic_analysis import analyze_request  # noqa: E402
from fomc_basis.services.live_analysis import analyze_live_meeting  # noqa: E402


class ErrorDetail(BaseModel):
    code: str
    message: str
    field: str | None = None


class ErrorResponse(BaseModel):
    ok: Literal[False] = False
    error: ErrorDetail


class HealthResponse(BaseModel):
    ok: Literal[True] = True
    service: str = "fomc-basis-api"
    python_model: str = "fomc_basis"
    timestamp: datetime


class MeetingsResponse(BaseModel):
    ok: bool
    year: int
    meetings: list[dict[str, Any]]
    source: str
    receipt_timestamp: datetime
    error: ErrorDetail | None = None


class AnalysisEnvelope(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    ok: bool
    mode: Literal["live", "case-study", "manual"]
    status: Literal["complete", "degraded", "unavailable"]
    analysis: dict[str, Any] | None = None
    data_quality: dict[str, Any]
    provenance: list[dict[str, Any]]
    error: ErrorDetail | None = None


class ManualAnalysisRequest(BaseModel):
    analysis: AnalysisRequest


app = FastAPI(
    title="FOMC Basis Monitor API",
    version="1.0.0",
    description="Research-only API. It does not place or authenticate trading orders.",
)

_cache_lock = Lock()
_cache: dict[str, tuple[datetime, dict[str, Any]]] = {}
_ttl = timedelta(seconds=45)


@app.api_route("/api", methods=["GET", "POST"], include_in_schema=False)
async def vercel_entry(request: Request) -> Any:
    """Dispatch Vercel rewrites through the Python function's concrete /api route."""
    endpoint = request.query_params.get("__endpoint")
    try:
        if endpoint == "health":
            return health()
        if endpoint == "meetings":
            return meetings(int(request.query_params["year"]))
        if endpoint == "case-study":
            return case_study()
        if endpoint == "live":
            return live(
                date.fromisoformat(request.query_params["meeting"]),
                int(request.query_params.get("outcome_bp", 25)),
                int(request.query_params.get("contracts", 500)),
            )
        if endpoint == "analyze" and request.method == "POST":
            return analyze(ManualAnalysisRequest.model_validate(await request.json()))
    except (KeyError, ValueError, ValidationError) as exc:
        return JSONResponse(
            status_code=422,
            content=ErrorResponse(
                error=ErrorDetail(code="VALIDATION_ERROR", message=str(exc))
            ).model_dump(mode="json"),
        )
    return JSONResponse(
        status_code=404,
        content=ErrorResponse(
            error=ErrorDetail(code="NOT_FOUND", message="Unknown API endpoint")
        ).model_dump(mode="json"),
    )


def _quality(analysis: dict[str, Any] | None, *, mode: str) -> dict[str, Any]:
    if mode == "case-study":
        return {
            "source_timestamp": None,
            "receipt_timestamp": None,
            "synchronization": "not_established",
            "futures_depth": None,
            "kalshi_depth": None,
            "executability": "not_established",
            "settlement_compatibility": "not_independently_verified",
            "limitations": [
                "Source timestamp unavailable",
                "Synchronization not established",
                "Futures depth unavailable",
                "Kalshi depth unavailable",
                "Executability not established",
                "Settlement compatibility not independently verified",
            ],
        }
    if not analysis:
        return {
            "source_timestamp": None,
            "receipt_timestamp": datetime.now(UTC).isoformat(),
            "synchronization": "unavailable",
            "executability": "unavailable",
            "limitations": ["Analysis inputs could not be assembled from the available sources"],
        }
    quality = analysis.get("quote_quality", {})
    inputs = analysis.get("inputs", {})
    fq = inputs.get("futures_quote", {})
    kq = inputs.get("kalshi_yes_quote", {})
    return {
        **quality,
        "source_timestamps": {
            "futures": fq.get("source_timestamp"),
            "kalshi": kq.get("source_timestamp"),
        },
        "receipt_timestamps": {
            "futures": fq.get("receipt_timestamp"),
            "kalshi": kq.get("receipt_timestamp"),
        },
        "executability": (
            "established"
            if quality.get("futures_mode") == quality.get("kalshi_mode") == "EXECUTABLE"
            else "not_established"
        ),
        "synchronization": (
            "established"
            if quality.get("cross_venue_timestamp_difference_seconds") is not None
            and quality["cross_venue_timestamp_difference_seconds"]
            <= quality.get("synchronization_threshold_seconds", 0)
            else "not_established"
        ),
    }


def _provenance(analysis: dict[str, Any] | None, *, mode: str) -> list[dict[str, Any]]:
    if mode == "case-study":
        return [
            {
                "input": "ZQ futures, Kalshi contract, and EFFR",
                "source": "Historical user-supplied observation",
                "source_timestamp": None,
                "designation": "non-executable",
            }
        ]
    if not analysis:
        return []
    inputs = analysis.get("inputs", {})
    return [
        {
            "input": "ZQ futures",
            "source": inputs.get("futures_quote", {}).get("source"),
            "source_timestamp": inputs.get("futures_quote", {}).get("source_timestamp"),
            "receipt_timestamp": inputs.get("futures_quote", {}).get("receipt_timestamp"),
            "designation": "indicative" if mode == "live" else "user-supplied",
        },
        {
            "input": "Kalshi market",
            "source": inputs.get("kalshi_yes_quote", {}).get("source"),
            "source_timestamp": inputs.get("kalshi_yes_quote", {}).get("source_timestamp"),
            "receipt_timestamp": inputs.get("kalshi_yes_quote", {}).get("receipt_timestamp"),
            "designation": "public order book" if mode == "live" else "user-supplied",
        },
        {
            "input": "EFFR and target context",
            "source": "Federal Reserve Bank of New York" if mode == "live" else "user-supplied",
            "source_timestamp": None,
            "designation": "reference rate",
        },
        {
            "input": "FOMC calendar",
            "source": "Board of Governors of the Federal Reserve System",
            "source_timestamp": None,
            "designation": "official schedule",
        },
    ]


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    first = exc.errors()[0]
    field = ".".join(str(item) for item in first.get("loc", [])[1:]) or None
    return JSONResponse(
        status_code=422,
        content=ErrorResponse(
            error=ErrorDetail(code="VALIDATION_ERROR", message=first["msg"], field=field)
        ).model_dump(mode="json"),
    )


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(timestamp=datetime.now(UTC))


@app.get("/api/meetings", response_model=MeetingsResponse)
def meetings(year: int = Query(ge=2000, le=2100)) -> MeetingsResponse:
    receipt = datetime.now(UTC)
    try:
        items = FederalReserveCalendarProvider(timeout_seconds=8).meetings(year)
        return MeetingsResponse(
            ok=True,
            year=year,
            meetings=[item.model_dump(mode="json") for item in items],
            source="Board of Governors of the Federal Reserve System",
            receipt_timestamp=receipt,
        )
    except Exception as exc:
        return MeetingsResponse(
            ok=False,
            year=year,
            meetings=[],
            source="Board of Governors of the Federal Reserve System",
            receipt_timestamp=receipt,
            error=ErrorDetail(code="CALENDAR_UNAVAILABLE", message=str(exc)),
        )


@app.get("/api/case-study", response_model=AnalysisEnvelope)
def case_study() -> AnalysisEnvelope:
    analysis = analyze_september_2026_fixture()
    analysis["input_provenance"] = {
        "source_timestamp": None,
        "synchronization": "not_established",
        "futures_depth": None,
        "kalshi_depth": None,
        "executability": "not_established",
        "provenance": "historical user-supplied observation",
        "settlement_compatibility": "not independently verified",
    }
    return AnalysisEnvelope(
        ok=True,
        mode="case-study",
        status="complete",
        analysis=analysis,
        data_quality=_quality(analysis, mode="case-study"),
        provenance=_provenance(analysis, mode="case-study"),
    )


@app.get("/api/live", response_model=AnalysisEnvelope)
def live(
    meeting: date,
    outcome_bp: int = Query(default=25, ge=-100, le=100),
    contracts: int = Query(default=500, ge=1, le=10_000),
) -> AnalysisEnvelope:
    key = f"{meeting.isoformat()}:{outcome_bp}:{contracts}"
    now = datetime.now(UTC)
    with _cache_lock:
        cached = _cache.get(key)
        if cached and now - cached[0] < _ttl:
            return AnalysisEnvelope.model_validate(cached[1])
    try:
        analysis = analyze_live_meeting(meeting, outcome_bp, contracts)
        envelope = AnalysisEnvelope(
            ok=True,
            mode="live",
            status="complete",
            analysis=analysis,
            data_quality=_quality(analysis, mode="live"),
            provenance=_provenance(analysis, mode="live"),
        )
    except Exception as exc:
        message = str(exc)
        code = (
            "MARKET_UNAVAILABLE" if "no open Kalshi market" in message else "LIVE_DATA_UNAVAILABLE"
        )
        envelope = AnalysisEnvelope(
            ok=False,
            mode="live",
            status="unavailable",
            data_quality=_quality(None, mode="live"),
            provenance=[],
            error=ErrorDetail(code=code, message=message),
        )
    dumped = envelope.model_dump(mode="json")
    with _cache_lock:
        _cache[key] = (now, dumped)
    return envelope


@app.post("/api/analyze", response_model=AnalysisEnvelope)
def analyze(payload: ManualAnalysisRequest) -> AnalysisEnvelope:
    try:
        analysis = analyze_request(payload.analysis)
    except ValueError as exc:
        return AnalysisEnvelope(
            ok=False,
            mode="manual",
            status="unavailable",
            data_quality=_quality(None, mode="manual"),
            provenance=[],
            error=ErrorDetail(code="ANALYSIS_ERROR", message=str(exc)),
        )
    return AnalysisEnvelope(
        ok=True,
        mode="manual",
        status="complete",
        analysis=analysis,
        data_quality=_quality(analysis, mode="manual"),
        provenance=_provenance(analysis, mode="manual"),
    )
