"use client";
import type { Ledger } from "@/lib/workbench-types";
const money = (v: number) =>
  new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 3,
  }).format(
    Math.abs(v) < 1e-8 ? 0 : (Math.sign(v) * Math.round((Math.abs(v) + 1e-10) * 1000)) / 1000,
  );
export function PayoffChart({ rows, unhedged }: { rows: Ledger[]; unhedged: Ledger[] }) {
  const max = Math.max(
    1,
    ...rows.map((r) => Math.abs(r.combined_net_pnl_dollars)),
    ...unhedged.map((r) => Math.abs(r.combined_net_pnl_dollars)),
  );
  const y = (v: number) => 130 - (v / max) * 100;
  return (
    <figure className="wb-chart">
      <figcaption>Signed state payoff in dollars</figcaption>
      <svg
        viewBox={`0 0 ${Math.max(480, rows.length * 100 + 80)} 270`}
        style={{ minWidth: Math.max(480, rows.length * 100 + 80) }}
        role="img"
        aria-label={`Selected combined payoffs ${rows.map((r) => `${r.move_bp} bp ${money(r.combined_net_pnl_dollars)}`).join(", ")}`}
      >
        {[max, 0, -max].map((v) => (
          <g key={v}>
            <line
              x1="70"
              x2={rows.length * 100 + 70}
              y1={y(v)}
              y2={y(v)}
              stroke={v === 0 ? "#17211b" : "#d6dad5"}
            />
            <text x="4" y={y(v) + 4} fontSize="11" fill="#465148">
              {money(v)}
            </text>
          </g>
        ))}
        {rows.map((r, i) => {
          const v = r.combined_net_pnl_dollars;
          const u = unhedged[i]?.combined_net_pnl_dollars ?? 0;
          return (
            <g key={r.move_bp}>
              <rect
                x={i * 100 + 85}
                y={Math.min(y(u), 130)}
                width="20"
                height={Math.abs(y(u) - 130)}
                fill="#8d9eaa"
              />
              <rect
                x={i * 100 + 110}
                y={Math.min(y(v), 130)}
                width="20"
                height={Math.abs(y(v) - 130)}
                fill={v < 0 ? "#a54b36" : "#2f6b4e"}
              />
              <text x={i * 100 + 105} y="254" textAnchor="middle" fontSize="12" fill="#17211b">
                {r.move_bp > 0 ? "+" : ""}
                {r.move_bp} bp
              </text>
            </g>
          );
        })}
      </svg>
      <p className="wb-note">Gray unhedged · green gain · red loss · common zero line</p>
    </figure>
  );
}
