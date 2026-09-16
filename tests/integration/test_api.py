from datetime import date

from fastapi.testclient import TestClient

import api.index as api_module

client = TestClient(api_module.app)


def test_health_schema_and_serialization() -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["python_model"] == "fomc_basis"
    assert body["timestamp"].endswith("Z")


def test_vercel_rewrite_dispatcher_preserves_public_health_contract() -> None:
    response = client.get("/api?__endpoint=health")
    assert response.status_code == 200
    assert response.json()["python_model"] == "fomc_basis"


def test_case_study_preserves_unavailable_evidence() -> None:
    response = client.get("/api/case-study")
    assert response.status_code == 200
    body = response.json()
    assert body["analysis"]["inputs"]["futures_symbol"] == "ZQU26.CBT"
    assert body["analysis"]["inputs"]["futures_bid"] == 96.26
    assert body["data_quality"]["source_timestamp"] is None
    assert body["data_quality"]["futures_depth"] is None
    assert body["data_quality"]["executability"] == "not_established"
    assert body["analysis"]["classification"]["label"] == "NO_TRADE"


def test_data_quality_executability_respects_model_hard_gate() -> None:
    analysis = {
        "inputs": {"futures_quote": {}, "kalshi_yes_quote": {}},
        "quote_quality": {
            "futures_mode": "EXECUTABLE",
            "kalshi_mode": "EXECUTABLE",
        },
        "classification": {"reason_codes": ["NON_EXECUTABLE_QUOTES"]},
    }

    quality = api_module._quality(analysis, mode="manual")

    assert quality["executability"] == "not_established"


def test_live_unavailable_is_structured_degraded_result(monkeypatch) -> None:
    def unavailable(*_: object, **__: object) -> dict:
        raise ValueError("no open Kalshi market mapped to +25 bp")

    monkeypatch.setattr(api_module, "analyze_live_meeting", unavailable)
    api_module._cache.clear()
    response = client.get("/api/live?meeting=2027-01-27&outcome_bp=25&contracts=500")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["status"] == "unavailable"
    assert body["error"]["code"] == "MARKET_UNAVAILABLE"
    assert body["analysis"] is None


def test_live_cache_avoids_repeat_provider_call(monkeypatch) -> None:
    calls = 0

    def fixture(*_: object, **__: object) -> dict:
        nonlocal calls
        calls += 1
        return {
            "analysis_timestamp": "2027-01-01T00:00:00+00:00",
            "inputs": {"futures_quote": {}, "kalshi_yes_quote": {}},
            "quote_quality": {},
            "classification": {"label": "NO_TRADE", "reason_codes": []},
        }

    monkeypatch.setattr(api_module, "analyze_live_meeting", fixture)
    api_module._cache.clear()
    url = "/api/live?meeting=2027-01-28&outcome_bp=25&contracts=501"
    assert client.get(url).status_code == 200
    assert client.get(url).status_code == 200
    assert calls == 1


def test_api_rejects_invalid_query_with_stable_error_shape() -> None:
    response = client.get("/api/live?meeting=not-a-date&contracts=0")
    assert response.status_code == 422
    body = response.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["field"] in {"meeting", "contracts"}


def test_meetings_returns_degraded_payload(monkeypatch) -> None:
    def fail(_: object, year: int) -> list[object]:
        raise RuntimeError(f"calendar unavailable for {year}")

    monkeypatch.setattr(api_module.FederalReserveCalendarProvider, "meetings", fail)
    response = client.get("/api/meetings?year=2027")
    assert response.status_code == 200
    assert response.json()["error"]["code"] == "CALENDAR_UNAVAILABLE"


def test_meeting_query_accepts_iso_date() -> None:
    assert date.fromisoformat("2027-01-27").year == 2027
