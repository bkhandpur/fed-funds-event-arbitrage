from __future__ import annotations

from typing import Any

import plotly.express as px

from .tables import payoff_frame


def payoff_chart(result: dict[str, Any]) -> Any:
    frame = payoff_frame(result)
    return px.bar(
        frame, x="move_bp", y="combined_net_pnl_dollars", title="State-contingent net P&L"
    )
