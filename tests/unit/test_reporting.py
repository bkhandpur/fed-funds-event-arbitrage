from __future__ import annotations

import json

import pytest

from fomc_basis.reporting.charts import payoff_chart
from fomc_basis.reporting.console import render_analysis
from fomc_basis.reporting.tables import payoff_frame


def result() -> dict:
    return {
        "classification": {"label": "NO_TRADE"},
        "warning": "Indicative inputs only",
        "futures_implied": {
            "bid": {
                "futures_price_points": 96.1,
                "post_meeting_effr_pct": 3.8,
                "expected_move_bp": 17.5,
                "25_BP_EQUIVALENT_PROBABILITY": 0.7,
            }
        },
        "state_payoffs": [
            {"move_bp": 0, "combined_net_pnl_dollars": -12.5},
            {"move_bp": 25, "combined_net_pnl_dollars": 7.5},
        ],
    }


def test_payoff_reporting_builds_frame_and_chart() -> None:
    frame = payoff_frame(result())
    assert frame["move_bp"].tolist() == [0, 25]
    chart = payoff_chart(result())
    assert chart.layout.title.text == "State-contingent net P&L"
    assert list(chart.data[0].x) == [0, 25]


def test_console_reporting_supports_human_and_json_output(capsys) -> None:
    render_analysis(result())
    human = capsys.readouterr().out
    assert "NO_TRADE" in human
    assert "Indicative inputs only" in human

    render_analysis(result(), as_json=True)
    machine = json.loads(capsys.readouterr().out)
    assert machine["classification"]["label"] == "NO_TRADE"


def test_payoff_reporting_rejects_missing_state_payoffs() -> None:
    with pytest.raises(KeyError, match="state_payoffs"):
        payoff_frame({})
