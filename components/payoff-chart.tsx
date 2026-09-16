"use client";

import type { PayoffRow } from "@/lib/types";

const money = (value: number) =>
  new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(value);

export function PayoffChart({ rows, label = "Net P&L" }: { rows: PayoffRow[]; label?: string }) {
  if (!rows.length) return <p className="empty-copy">No state-payoff rows are available.</p>;
  const values = rows.map((row) => row.combined_net_pnl_dollars);
  const max = Math.max(...values.map(Math.abs), 1);
  const worst = Math.min(...values);
  return (
    <div className="chart" role="group" aria-label={`${label} by policy move state`}>
      {rows.map((row, index) => {
        const state = row.state_bp ?? row.policy_move_bp ?? row.move_bp ?? index;
        const value = row.combined_net_pnl_dollars;
        const height = 16 + (Math.abs(value) / max) * 126;
        const isWorst = value === worst;
        return (
          <button
            className={`bar-wrap ${isWorst ? "worst" : ""}`}
            key={`${state}-${index}`}
            title={`${state >= 0 ? "+" : ""}${state} bp · ${money(value)}${row.basis_change_bp === undefined ? "" : ` · basis ${row.basis_change_bp} bp`}`}
            type="button"
          >
            <span className="bar-value">{money(value)}</span>
            <span className={`bar ${value >= 0 ? "positive" : "negative"}`} style={{ height }} />
            <span className="bar-label">{state >= 0 ? "+" : ""}{state} bp</span>
          </button>
        );
      })}
    </div>
  );
}
