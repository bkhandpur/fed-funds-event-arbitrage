from __future__ import annotations

import pandas as pd


def payoff_frame(result: dict) -> pd.DataFrame:
    return pd.DataFrame(result["state_payoffs"])
