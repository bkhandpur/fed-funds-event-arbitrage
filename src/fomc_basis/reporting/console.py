from __future__ import annotations

import json
from typing import Any

from rich.console import Console
from rich.table import Table


def render_analysis(result: dict[str, Any], as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(result, indent=2, default=str))
        return
    console = Console()
    console.print(f"[bold]Classification:[/] {result['classification']['label']}")
    if warning := result.get("warning"):
        console.print(warning, style="yellow")
    table = Table("Side", "ZQ price", "Post EFFR %", "Expected move bp", "25bp equivalent")
    for side, values in result["futures_implied"].items():
        table.add_row(
            side,
            f"{values['futures_price_points']:.4f}",
            f"{values['post_meeting_effr_pct']:.6f}",
            f"{values['expected_move_bp']:.4f}",
            f"{values['25_BP_EQUIVALENT_PROBABILITY']:.4%}",
        )
    console.print(table)
