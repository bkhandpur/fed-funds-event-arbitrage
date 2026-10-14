"use client";

import { useEffect, useRef, useState } from "react";
import { PayoffChart } from "./payoff-chart";
import september from "@/lib/september-v2.json";
import reference from "@/lib/reference-a-v2.json";
import type { Candidate, ResearchAnalysis, ResearchRequest } from "@/lib/workbench-types";

const historical = september as unknown as ResearchAnalysis;
const synthetic = reference as unknown as ResearchAnalysis;
const money = (v: number) =>
  new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 3,
  }).format((Math.sign(v) * Math.round((Math.abs(v) + 1e-10) * 1000)) / 1000);
const pct = (v: number) => `${(v * 100).toFixed(2)}%`;
const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v));
const range = (v: [number, number]) => `${money(v[0])} to ${money(v[1])}`;

function download(name: string, text: string, type: string) {
  const u = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = u;
  a.download = name;
  a.click();
  URL.revokeObjectURL(u);
}
function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="wb-metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
function ScrollTable({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div
      className="wb-scroll"
      role="region"
      aria-label={label}
      // Focus is required for native keyboard scrolling in this region.
      // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex
      tabIndex={0}
    >
      {children}
    </div>
  );
}

export function Workspace() {
  const [analysis, setAnalysis] = useState<ResearchAnalysis>(historical);
  const [draft, setDraft] = useState<ResearchRequest>(clone(historical.inputs));
  const [mode, setMode] = useState("Historical September");
  const [resultMode, setResultMode] = useState("Historical September");
  const liveRequest = useRef(false);
  const [candidate, setCandidate] = useState<Candidate>(historical.workbench.selected);
  const [basis, setBasis] = useState(0);
  const [base, setBase] = useState<ResearchAnalysis | null>(null);
  const [baseCandidate, setBaseCandidate] = useState<Candidate | null>(null);
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const [meeting, setMeeting] = useState("2026-10-28");
  const [calendarStatus, setCalendarStatus] = useState("Calendar pending");
  const meetingEdited = useRef(false);
  const generation = useRef(0);
  const abort = useRef<AbortController | null>(null);
  useEffect(() => {
    try {
      const stored = localStorage.getItem("fomc-workbench-base-v2");
      if (!stored) return;
      const value = JSON.parse(stored);
      if (value.analysis?.model_version === historical.model_version && value.candidate?.ledger) {
        setBase(value.analysis);
        setBaseCandidate(value.candidate);
      }
    } catch {
      setError("Saved comparison could not be restored");
    }
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 8000);
    const today = new Date().toISOString().slice(0, 10);
    const year = Number(today.slice(0, 4));
    async function calendar() {
      try {
        for (const y of [year, year + 1]) {
          const response = await fetch(`/api/meetings?year=${y}`, { signal: controller.signal });
          const payload = await response.json();
          if (!payload.ok) throw new Error("Calendar unavailable");
          const next = payload.meetings.find(
            (m: { decision_date: string }) => m.decision_date >= today,
          );
          if (next) {
            if (!meetingEdited.current) setMeeting(next.decision_date);
            setCalendarStatus("Official calendar");
            return;
          }
        }
        setCalendarStatus("No upcoming meeting in the returned calendar");
      } catch {
        setCalendarStatus("Calendar unavailable · enter a live meeting date");
      } finally {
        clearTimeout(timer);
      }
    }
    void calendar();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, []);
  const stale = JSON.stringify(draft) !== JSON.stringify(analysis.inputs);
  const wb = analysis.workbench;
  const ec = candidate.economics;
  const plot = candidate.stress.filter((r) => r.basis_change_bp === basis);
  const rows = plot.length ? plot : candidate.ledger;
  const unhedged = wb.candidates.find((c) => c.label === "Unhedged")!;
  function cancel() {
    generation.current++;
    abort.current?.abort();
    setPending(false);
  }
  function edit(v: Partial<ResearchRequest>) {
    cancel();
    setDraft((d) => ({ ...d, ...v }));
    setError("");
    setMode("Manual scenario");
  }
  function load(result: ResearchAnalysis, label: string) {
    cancel();
    setAnalysis(result);
    setDraft(clone(result.inputs));
    setMode(label);
    setResultMode(label);
    setCandidate(result.workbench.selected);
    setBasis(Number(result.workbench.selected.ledger[0].basis_change_bp));
    setError("");
  }
  async function run(live = false) {
    cancel();
    liveRequest.current = live;
    const id = generation.current;
    const controller = new AbortController();
    abort.current = controller;
    setPending(true);
    setError("");
    const request = clone(draft);
    const timer = setTimeout(() => controller.abort(), 20000);
    try {
      const response = await fetch(
        live
          ? `/api/live?meeting=${meeting}&outcome_bp=${request.kalshi_outcome_bp}&contracts=${request.kalshi_contracts}`
          : "/api/analyze",
        live
          ? { signal: controller.signal }
          : {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ analysis: request }),
              signal: controller.signal,
            },
      );
      const value = await response.json();
      if (id !== generation.current) return;
      if (!response.ok || !value.ok || !value.analysis?.workbench)
        throw new Error(value.error?.message ?? "Analysis unavailable");
      const result = value.analysis as ResearchAnalysis;
      setAnalysis(result);
      setDraft(clone(result.inputs));
      setCandidate(result.workbench.selected);
      setBasis(Number(result.workbench.selected.ledger[0].basis_change_bp));
      setMode(live ? "Indicative live" : "Manual scenario");
      setResultMode(live ? "Indicative live" : "Manual scenario");
    } catch (e) {
      if (id === generation.current)
        setError(
          e instanceof Error && e.name !== "AbortError"
            ? e.message
            : "Request timed out or was canceled. Retry the analysis.",
        );
    } finally {
      clearTimeout(timer);
      if (id === generation.current) setPending(false);
    }
  }
  function number(key: string, label: string, step = "any") {
    return (
      <label>
        {label}
        <input
          type="number"
          step={step}
          value={String(draft[key] ?? "")}
          onChange={(e) => edit({ [key]: e.target.value === "" ? null : Number(e.target.value) })}
        />
      </label>
    );
  }
  function quote(leg: "futures_quote" | "kalshi_yes_quote", field: "bid" | "ask", label: string) {
    return (
      <label>
        {label}
        <input
          type="number"
          step="any"
          value={draft[leg][field] ?? ""}
          onChange={(e) => edit({ [leg]: { ...draft[leg], [field]: e.target.value || null } })}
        />
      </label>
    );
  }
  function choose(c: Candidate) {
    setCandidate(c);
    setBasis(c.ledger[0].basis_change_bp);
    setDraft((d) => ({
      ...d,
      selected_futures_contracts: c.futures_contracts,
      selected_no_position: c.binary_contracts === 0,
    }));
  }
  async function exportJson() {
    const request = {
      ...clone(analysis.inputs),
      selected_futures_contracts: candidate.futures_contracts,
      selected_no_position: candidate.binary_contracts === 0,
    };
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 20000);
    try {
      const response = await fetch("/api/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ analysis: request }),
        signal: controller.signal,
      });
      const value = await response.json();
      if (!response.ok || !value.ok)
        throw new Error(value.error?.message ?? "Export analysis unavailable");
      download(
        "fomc-scenario-v2.json",
        JSON.stringify(
          {
            schema_version: "2.0",
            model_version: analysis.model_version,
            request,
            result: value.analysis,
            units: { rates: "percent", policy: "bp", prices: "points", cash: "USD" },
            provenance: resultMode,
            displayed_basis_change_bp: basis,
          },
          null,
          2,
        ),
        "application/json",
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Export unavailable");
    } finally {
      clearTimeout(timer);
    }
  }
  function csv() {
    const keys = [
      "move_bp",
      "basis_change_bp",
      "kalshi_settlement_dollars",
      "kalshi_quote_acquisition_dollars",
      "kalshi_slippage_dollars",
      "kalshi_acquisition_cost_dollars",
      "kalshi_fees_dollars",
      "futures_settlement_price_points",
      "futures_gross_pnl_dollars",
      "futures_costs_dollars",
      "combined_net_pnl_dollars",
    ];
    download(
      "fomc-state-ledger.csv",
      [keys.join(","), ...rows.map((r) => keys.map((k) => r[k]).join(","))].join("\n"),
      "text/csv",
    );
  }
  return (
    <main className="workbench">
      <header className="wb-header">
        <div>
          <span className="eyebrow">FOMC cross-market research</span>
          <h1>Scenario workbench</h1>
        </div>
        <a href="https://github.com/bkhandpur/fed-funds-event-arbitrage">Source and methodology</a>
      </header>
      <nav className="wb-actions" aria-label="Data mode">
        <button onClick={() => load(historical, "Historical September")}>September example</button>
        <button onClick={() => load(synthetic, "Synthetic reference A")}>Reference A</button>
        <button
          onClick={() => {
            cancel();
            setMode("Manual scenario");
            setDraft(clone(analysis.inputs));
          }}
        >
          Clone to manual
        </button>
        <label>
          Live meeting
          <input
            type="date"
            value={meeting}
            onChange={(e) => {
              cancel();
              meetingEdited.current = true;
              setMeeting(e.target.value);
            }}
          />
        </label>
        <button
          onClick={() => {
            setMode("Live request pending");
            void run(true);
          }}
        >
          Retrieve indicative live
        </button>
      </nav>
      <div className="wb-context" aria-live="polite">
        <strong>{resultMode}</strong>
        <span>
          {String(analysis.chosen_side).toUpperCase()} × {candidate.binary_contracts} binary ·
          signed ZQ {candidate.futures_contracts}
        </span>
        <span>{calendarStatus}</span>
        <span>Decision {analysis.inputs.meeting.decision_date}</span>
        <span>Effective {analysis.inputs.meeting.effective_date}</span>
        <span>{analysis.inputs.futures_quote.instrument}</span>
        <span>{analysis.inputs.kalshi_yes_quote.instrument}</span>
        <span>
          Winning bucket{" "}
          {(analysis.inputs.kalshi_winning_states_bp ?? [analysis.inputs.kalshi_outcome_bp]).join(
            ", ",
          )}{" "}
          bp
        </span>
        <span>Reference date {String(analysis.inputs.reference_rate_date ?? "Unavailable")}</span>
        <span>Execution unverified</span>
        {stale && <strong className="wb-loss">Draft changed · displayed result is stale</strong>}
      </div>
      {pending && <p role="status">Running analysis with a 20 second timeout</p>}
      {error && (
        <div className="wb-error" role="alert">
          <strong>Analysis unavailable</strong>
          <p>{error}</p>
          <p>Previous result retains its original source dates and data mode.</p>
          <button onClick={() => void run(liveRequest.current)}>Retry</button>
        </div>
      )}
      <div className="wb-layout">
        <section className="wb-results" aria-label="Analysis results">
          <div className="wb-metrics">
            <Metric
              label="Futures-implied expected move"
              value={`${wb.expected_move_bp.toFixed(3)} bp`}
            />
            <Metric
              label="Exact bucket probability interval"
              value={`${pct(wb.probability_interval[0])} to ${pct(wb.probability_interval[1])}`}
            />
            <Metric label="Binary net model EV" value={money(ec.binary_model_ev_dollars)} />
            <Metric label="Combined net model EV" value={money(ec.combined_model_ev_dollars)} />
            <Metric
              label="Combined feasible EV interval"
              value={range(ec.combined_feasible_ev_dollars)}
            />
            <Metric label="Central worst state" value={money(ec.worst_state_dollars)} />
            <Metric
              label="Basis-stressed worst state"
              value={`${money(ec.stressed_worst_dollars)} · ${ec.worst_move_bp} bp · basis ${ec.worst_basis_change_bp} bp`}
            />
            <Metric
              label="Estimated cash plus margin"
              value={money(ec.estimated_cash_and_margin_dollars)}
            />
          </div>
          <p className="wb-note">
            Displayed basis scenario {basis} bp · central EV remains at the analyzed basis
            assumption
          </p>
          <PayoffChart rows={rows} unhedged={unhedged.ledger} />
          <section className="wb-section">
            <h2>Whole-contract hedge comparison</h2>
            <p className="wb-note">
              Pair flattening {wb.continuous_pair_hedge.toFixed(4)} contracts for{" "}
              {wb.benchmark_states_bp.join(" and ")} bp. Nearest is a benchmark.
            </p>
            <p className="wb-note">
              {wb.hedge_objective}. Recommendation {wb.recommendation}.
            </p>
            <ScrollTable label="Hedge candidate comparison">
              <table>
                <thead>
                  <tr>
                    {[
                      "Choice",
                      "Signed ZQ",
                      "Gross futures EV",
                      "Costs",
                      "Combined EV",
                      "Feasible EV",
                      "Worst",
                      "Stressed worst",
                      "Cash and margin",
                      "Limits",
                    ].map((s) => (
                      <th key={s}>{s}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {wb.candidates.map((c, i) => (
                    <tr key={i} className={c.label === candidate.label ? "wb-selected" : ""}>
                      <td>{<button onClick={() => choose(c)}>{c.label}</button>}</td>
                      <td>{c.futures_contracts}</td>
                      <td>{money(c.economics.futures_gross_ev_dollars)}</td>
                      <td>{money(c.economics.total_costs_dollars)}</td>
                      <td>{money(c.economics.combined_model_ev_dollars)}</td>
                      <td>{range(c.economics.combined_feasible_ev_dollars)}</td>
                      <td>{money(c.economics.worst_state_dollars)}</td>
                      <td>{money(c.economics.stressed_worst_dollars)}</td>
                      <td>{money(c.economics.estimated_cash_and_margin_dollars)}</td>
                      <td>{c.feasible ? "Within limits" : "Outside limits"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </ScrollTable>
          </section>
          <section className="wb-section">
            <h2>Basis by policy stress</h2>
            <p className="wb-note">Select a basis cell to inspect signed state payoffs.</p>
            <ScrollTable label="Basis stress cells">
              <table>
                <thead>
                  <tr>
                    <th>Policy bp</th>
                    {[...new Set(candidate.stress.map((r) => r.basis_change_bp))].map((b) => (
                      <th key={b}>Basis {b} bp</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {candidate.ledger.map((r) => (
                    <tr key={r.move_bp}>
                      <th>{r.move_bp} bp</th>
                      {candidate.stress
                        .filter((s) => s.move_bp === r.move_bp)
                        .map((s) => (
                          <td key={s.basis_change_bp}>
                            <button
                              aria-pressed={basis === s.basis_change_bp}
                              className={s.combined_net_pnl_dollars < 0 ? "wb-loss" : ""}
                              onClick={() => setBasis(s.basis_change_bp)}
                            >
                              {money(s.combined_net_pnl_dollars)}
                            </button>
                          </td>
                        ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </ScrollTable>
          </section>
          <section className="wb-section">
            <h2>State ledger</h2>
            <p className="wb-note">
              {candidate.futures_contracts} signed futures entered at {candidate.entry_side}{" "}
              {candidate.entry_price_points.toFixed(4)} points. Acquisition includes slippage.
            </p>
            <ScrollTable label="State cashflow ledger">
              <table>
                <thead>
                  <tr>
                    {[
                      "Policy bp",
                      "Binary payout",
                      "Quote cash",
                      "Slippage",
                      "Acquisition",
                      "Fees",
                      "ZQ settlement",
                      "ZQ gross P&L",
                      "ZQ costs",
                      "Net P&L",
                    ].map((s) => (
                      <th key={s}>{s}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.move_bp}>
                      <td>{r.move_bp}</td>
                      {[
                        r.kalshi_settlement_dollars,
                        r.kalshi_quote_acquisition_dollars,
                        r.kalshi_slippage_dollars,
                        r.kalshi_acquisition_cost_dollars,
                        r.kalshi_fees_dollars,
                      ].map((v, i) => (
                        <td key={i}>{money(v)}</td>
                      ))}
                      <td>{r.futures_settlement_price_points.toFixed(3)}</td>
                      {[
                        r.futures_gross_pnl_dollars,
                        r.futures_costs_dollars,
                        r.combined_net_pnl_dollars,
                      ].map((v, i) => (
                        <td key={i}>{money(v)}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </ScrollTable>
          </section>
          <section className="wb-section">
            <h2>Price sensitivity</h2>
            <p>
              Combined break-even binary acquisition quote{" "}
              {wb.break_even_binary_price_dollars === null
                ? "Unavailable"
                : money(wb.break_even_binary_price_dollars)}
            </p>
            <ScrollTable label="Binary price sensitivity">
              <table>
                <thead>
                  <tr>
                    <th>Binary quote USD</th>
                    <th>Combined net EV USD</th>
                  </tr>
                </thead>
                <tbody>
                  {wb.sensitivities.map((s) => (
                    <tr key={s.binary_price_dollars}>
                      <td>{money(s.binary_price_dollars)}</td>
                      <td>{money(s.combined_ev_dollars)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </ScrollTable>
            <ScrollTable label="Futures tick sensitivity">
              <table>
                <thead>
                  <tr>
                    <th>Futures reference points</th>
                    <th>Expected move bp</th>
                    <th>Combined model EV USD</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {wb.futures_price_sensitivities.map((s) => (
                    <tr key={s.futures_reference_points}>
                      <td>{s.futures_reference_points.toFixed(4)}</td>
                      <td>{s.expected_move_bp.toFixed(3)}</td>
                      <td>
                        {s.combined_ev_dollars === undefined
                          ? "Unavailable"
                          : money(s.combined_ev_dollars)}
                      </td>
                      <td>{s.status}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </ScrollTable>
            <p className="wb-note">
              Candidate-price fees are recalculated. Select a hedge and rerun to refresh its
              sensitivity.
            </p>
          </section>
          <section className="wb-section">
            <h2>Base and alternative</h2>
            <button
              onClick={() => {
                setBase(clone(analysis));
                setBaseCandidate(clone(candidate));
                try {
                  localStorage.setItem(
                    "fomc-workbench-base-v2",
                    JSON.stringify({ analysis, candidate }),
                  );
                } catch {
                  setError("Base retained in this tab. Browser storage is unavailable.");
                }
              }}
            >
              Save displayed base
            </button>
            {base && baseCandidate && (
              <>
                <p>
                  Base {base.inputs.meeting.decision_date} · {base.inputs.futures_quote.source}
                </p>
                <p>
                  Combined model EV delta{" "}
                  {money(
                    ec.combined_model_ev_dollars -
                      baseCandidate.economics.combined_model_ev_dollars,
                  )}{" "}
                  · stressed downside delta{" "}
                  {money(
                    ec.stressed_worst_dollars - baseCandidate.economics.stressed_worst_dollars,
                  )}
                </p>
                <button onClick={() => load(base, "Saved base scenario")}>Restore base</button>
              </>
            )}
          </section>
          <details className="wb-section">
            <summary>Execution evidence and provenance</summary>
            <ScrollTable label="Execution evidence">
              <table>
                <thead>
                  <tr>
                    <th>Check</th>
                    <th>Status</th>
                    <th>Evidence</th>
                  </tr>
                </thead>
                <tbody>
                  {wb.evidence.map((e) => (
                    <tr key={e.check}>
                      <th>{e.check}</th>
                      <td>{e.status}</td>
                      <td>{e.detail}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </ScrollTable>
            <p>Futures source {analysis.inputs.futures_quote.source}</p>
            <p>
              Futures observed {analysis.inputs.futures_quote.source_timestamp ?? "Unavailable"} ·
              received {analysis.inputs.futures_quote.receipt_timestamp}
            </p>
            <p>
              Binary observed {analysis.inputs.kalshi_yes_quote.source_timestamp ?? "Unavailable"} ·
              received {analysis.inputs.kalshi_yes_quote.receipt_timestamp}
            </p>
            <p>
              Quote ages seconds{" "}
              {String(analysis.quote_quality.futures_age_seconds ?? "Unavailable")} futures ·{" "}
              {String(analysis.quote_quality.kalshi_age_seconds ?? "Unavailable")} binary
            </p>
          </details>
          <details className="wb-section">
            <summary>Settlement and model conventions</summary>
            <p>
              Net P&L = binary payout − acquisition − binary fees + futures gross P&L − futures
              costs
            </p>
            <p>
              Final settlement uses $4,167 per point and half-up monthly-rate rounding to 0.001
              percentage point. Continuous inversion is an analytical approximation.
            </p>
            <p>
              Margin is locked capital. Estimated cash and margin do not establish sufficient
              variation-margin liquidity.
            </p>
            <p>
              Pre-effective average {String(wb.conventions.pre_effective_average_pct)}% · no-change
              post EFFR {String(wb.conventions.no_change_post_effr_pct)}%
            </p>
            <p>{String(wb.conventions.pre_effective_assumption)}</p>
            <p>
              Fee rounding ${String(wb.conventions.fee_rounding_dollars)} · supplied schedule{" "}
              {String(analysis.inputs.kalshi_fee_schedule_name)}
            </p>
            <p>
              Reference A uses synthetic cent rounding. The checked July 2026 schedule uses
              centicent rounding and series-specific maker fees.
            </p>
            <a href="https://www.cmegroup.com/rulebook/CBOT/III/22.pdf">CBOT Chapter 22</a> ·{" "}
            <a href="https://kalshi.com/docs/kalshi-fee-schedule.pdf">Kalshi fee schedule</a>
          </details>
          <div className="wb-actions">
            <button onClick={() => void exportJson()}>Export request and result JSON</button>
            <button onClick={csv}>Export ledger CSV</button>
          </div>
        </section>
        <aside className="wb-inputs">
          <h2>Working assumptions</h2>
          <p className="wb-note">{mode}</p>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void run();
            }}
          >
            <fieldset>
              <legend>Settlement context</legend>
              {["decision_date", "effective_date"].map((k) => (
                <label key={k}>
                  {k === "decision_date" ? "Decision date" : "Effective date"}
                  <input
                    type="date"
                    value={draft.meeting[k as "decision_date" | "effective_date"]}
                    onChange={(e) =>
                      edit({
                        meeting: {
                          ...draft.meeting,
                          [k]: e.target.value,
                          start_date:
                            k === "decision_date" ? e.target.value : draft.meeting.start_date,
                        },
                      })
                    }
                  />
                </label>
              ))}
              <label>
                ZQ symbol
                <input
                  value={draft.futures_quote.instrument}
                  onChange={(e) =>
                    edit({ futures_quote: { ...draft.futures_quote, instrument: e.target.value } })
                  }
                />
              </label>
              {number("pre_effective_effr_average_pct", "Pre-effective average EFFR percent")}
              {number("current_effr_pct", "Reference EFFR percent")}
              {number("no_change_effr_pct", "No-change post EFFR percent")}
              {number("current_effr_target_basis_bp", "Observed EFFR target basis bp")}
              {number("assumed_post_meeting_basis_bp", "Assumed post-meeting basis bp")}
            </fieldset>
            <fieldset>
              <legend>Position and execution costs</legend>
              {quote("futures_quote", "bid", "Futures bid points")}
              {quote("futures_quote", "ask", "Futures ask points")}
              {quote("kalshi_yes_quote", "bid", "YES bid dollars")}
              {quote("kalshi_yes_quote", "ask", "YES ask dollars")}
              <label>
                Binary side
                <select
                  value={draft.selected_binary_side ?? "auto"}
                  onChange={(e) =>
                    edit({
                      selected_binary_side: e.target.value === "auto" ? null : e.target.value,
                    })
                  }
                >
                  <option value="auto">Conservative binary EV choice</option>
                  <option value="yes">YES</option>
                  <option value="no">NO</option>
                </select>
              </label>
              {number("kalshi_contracts", "Binary contracts", "1")}
              {number("selected_futures_contracts", "Signed futures contracts", "1")}
              {number("slippage_per_kalshi_contract_dollars", "Binary entry slippage dollars")}
              {number(
                "futures_round_trip_cost_per_contract_dollars",
                "Futures round-trip cost dollars",
              )}
              {number("kalshi_fee_coefficient", "Taker fee coefficient")}
              {number("kalshi_maker_fee_coefficient", "Maker fee coefficient")}
              <label>
                Fee rounding dollars
                <select
                  value={String(draft.kalshi_fee_rounding_dollars)}
                  onChange={(e) => edit({ kalshi_fee_rounding_dollars: e.target.value })}
                >
                  <option value="0.01">0.01 synthetic or historical convention</option>
                  <option value="0.0001">0.0001 July 2026 schedule</option>
                </select>
              </label>
              {number("capital_limit_dollars", "Cash and margin limit dollars")}
              {number("worst_loss_limit_dollars", "Stressed loss limit dollars")}
              {number("max_futures_contracts", "Enumeration limit contracts", "1")}
            </fieldset>
            <fieldset>
              <legend>Policy distribution</legend>
              {number("two_state_low_bp", "Pair benchmark low state bp", "1")}
              <label>
                Probability mode
                <select
                  aria-label="Probability mode"
                  value={draft.probability_mode}
                  onChange={(e) => edit({ probability_mode: e.target.value })}
                >
                  <option value="two_state">Declared two states</option>
                  <option value="bounds">Joint bounds and selected prior</option>
                  <option value="tails">Tail-conditioned prior</option>
                  <option value="regularized">Market-conditioned prior</option>
                </select>
              </label>
              <ScrollTable label="Editable policy states">
                <table>
                  <thead>
                    <tr>
                      <th>Move bp</th>
                      <th>Wins</th>
                      <th>Lower</th>
                      <th>Upper</th>
                      <th>Prior</th>
                      <th>User view</th>
                    </tr>
                  </thead>
                  <tbody>
                    {draft.state_grid_bp.map((s, i) => (
                      <tr key={i}>
                        <td>
                          <input
                            aria-label={`Move state ${i + 1}`}
                            type="number"
                            value={s}
                            onChange={(e) => {
                              const states = [...draft.state_grid_bp];
                              const next = Number(e.target.value);
                              states[i] = next;
                              const bounds = { ...draft.state_probability_bounds };
                              if (bounds[s]) {
                                bounds[next] = bounds[s];
                                delete bounds[s];
                              }
                              edit({
                                state_grid_bp: states,
                                state_probability_bounds: bounds,
                                kalshi_winning_states_bp: (
                                  draft.kalshi_winning_states_bp ?? [draft.kalshi_outcome_bp]
                                ).map((v) => (v === s ? next : v)),
                                kalshi_outcome_bp:
                                  draft.kalshi_outcome_bp === s ? next : draft.kalshi_outcome_bp,
                                two_state_low_bp:
                                  draft.two_state_low_bp === s ? next : draft.two_state_low_bp,
                              });
                            }}
                          />
                        </td>
                        <td>
                          <input
                            aria-label={`Winning state ${i + 1}`}
                            type="checkbox"
                            checked={(
                              draft.kalshi_winning_states_bp ?? [draft.kalshi_outcome_bp]
                            ).includes(s)}
                            onChange={(e) => {
                              const wins = draft.kalshi_winning_states_bp ?? [
                                draft.kalshi_outcome_bp,
                              ];
                              edit({
                                kalshi_winning_states_bp: e.target.checked
                                  ? [...wins, s]
                                  : wins.filter((v) => v !== s),
                              });
                            }}
                          />
                        </td>
                        {[0, 1].map((k) => (
                          <td key={k}>
                            <input
                              aria-label={`${k === 0 ? "Lower" : "Upper"} probability state ${i + 1}`}
                              type="number"
                              step=".01"
                              min="0"
                              max="1"
                              value={draft.state_probability_bounds[String(s)]?.[k] ?? k}
                              onChange={(e) => {
                                const b = [
                                  ...(draft.state_probability_bounds[String(s)] ?? [0, 1]),
                                ] as [number, number];
                                b[k] = Number(e.target.value);
                                edit({
                                  state_probability_bounds: {
                                    ...draft.state_probability_bounds,
                                    [s]: b,
                                  },
                                });
                              }}
                            />
                          </td>
                        ))}
                        <td>
                          <input
                            aria-label={`Prior state ${i + 1}`}
                            type="number"
                            step=".01"
                            value={draft.prior?.[i] ?? 1 / draft.state_grid_bp.length}
                            onChange={(e) => {
                              const p =
                                draft.prior ??
                                draft.state_grid_bp.map(() => 1 / draft.state_grid_bp.length);
                              const next = [...p];
                              next[i] = Number(e.target.value);
                              edit({ prior: next });
                            }}
                          />
                        </td>
                        <td>
                          <input
                            aria-label={`User mass state ${i + 1}`}
                            type="number"
                            step=".01"
                            value={draft.user_distribution?.[i] ?? ""}
                            onChange={(e) => {
                              const p = draft.user_distribution ?? draft.state_grid_bp.map(() => 0);
                              const next = [...p];
                              next[i] = Number(e.target.value);
                              edit({ user_distribution: next });
                            }}
                          />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </ScrollTable>
              <button
                type="button"
                onClick={() =>
                  edit({
                    state_grid_bp: [...draft.state_grid_bp, Math.max(...draft.state_grid_bp) + 25],
                    prior: null,
                    user_distribution: null,
                  })
                }
              >
                Add policy state
              </button>
              <button type="button" onClick={() => edit({ user_distribution: null })}>
                Clear user view
              </button>
              <p className="wb-note">
                Market masses {wb.market_distribution.map((p) => pct(p)).join(" · ")} · sum{" "}
                {wb.market_distribution.reduce((a, b) => a + b, 0).toFixed(6)}
              </p>
              {draft.user_distribution && (
                <p>User sum {draft.user_distribution.reduce((a, b) => a + b, 0).toFixed(6)}</p>
              )}
              {wb.user_expected_move_bp !== null && (
                <p>
                  User expected move {wb.user_expected_move_bp.toFixed(3)} bp · market residual{" "}
                  {wb.user_moment_residual_bp?.toFixed(3)} bp · user combined EV{" "}
                  {money(ec.user_combined_ev_dollars ?? 0)}
                </p>
              )}
            </fieldset>
            <details>
              <summary>Costs and execution evidence inputs</summary>
              <label>
                Maker order
                <input
                  type="checkbox"
                  checked={Boolean(draft.kalshi_order_is_maker)}
                  onChange={(e) => edit({ kalshi_order_is_maker: e.target.checked })}
                />
              </label>
              <label>
                Zero fee override
                <input
                  type="checkbox"
                  checked={Boolean(draft.kalshi_zero_fee)}
                  onChange={(e) => edit({ kalshi_zero_fee: e.target.checked })}
                />
              </label>
              {number(
                "kalshi_settlement_fee_per_contract_dollars",
                "Binary settlement fee dollars",
              )}
              {number("futures_margin_per_contract_dollars", "Assumed futures margin dollars")}
              {number("futures_bid_depth", "Futures bid depth contracts", "1")}
              {number("futures_ask_depth", "Futures ask depth contracts", "1")}
              {number("kalshi_yes_ask_depth", "YES ask depth contracts", "1")}
              {number("kalshi_no_ask_depth", "NO ask depth contracts", "1")}
              {(["futures_quote", "kalshi_yes_quote"] as const).map((leg) => (
                <fieldset key={leg}>
                  <legend>
                    {leg === "futures_quote" ? "Futures evidence" : "Binary evidence"}
                  </legend>
                  <label>
                    Observed source time with UTC offset
                    <input
                      value={draft[leg].source_timestamp ?? ""}
                      onChange={(e) =>
                        edit({ [leg]: { ...draft[leg], source_timestamp: e.target.value || null } })
                      }
                    />
                  </label>
                  <label>
                    Receipt time with UTC offset
                    <input
                      value={draft[leg].receipt_timestamp}
                      onChange={(e) =>
                        edit({ [leg]: { ...draft[leg], receipt_timestamp: e.target.value } })
                      }
                    />
                  </label>
                  <label>
                    Delayed observation
                    <input
                      type="checkbox"
                      checked={draft[leg].delayed}
                      onChange={(e) =>
                        edit({ [leg]: { ...draft[leg], delayed: e.target.checked } })
                      }
                    />
                  </label>
                  <label>
                    Market open
                    <select
                      aria-label={
                        leg === "futures_quote" ? "Futures market open" : "Binary market open"
                      }
                      value={
                        draft[leg].market_open === null ? "unknown" : String(draft[leg].market_open)
                      }
                      onChange={(e) =>
                        edit({
                          [leg]: {
                            ...draft[leg],
                            market_open:
                              e.target.value === "unknown" ? null : e.target.value === "true",
                          },
                        })
                      }
                    >
                      <option value="unknown">Unknown</option>
                      <option value="true">User supplied open</option>
                      <option value="false">Closed</option>
                    </select>
                  </label>
                </fieldset>
              ))}
              <label>
                Multiple meetings in the contract month
                <input
                  type="checkbox"
                  checked={Boolean(draft.multiple_meetings_in_contract)}
                  onChange={(e) => edit({ multiple_meetings_in_contract: e.target.checked })}
                />
              </label>
              <label>
                Complete relevant state coverage assertion
                <input
                  type="checkbox"
                  checked={Boolean(draft.all_outcomes_modeled)}
                  onChange={(e) => edit({ all_outcomes_modeled: e.target.checked })}
                />
              </label>
              <p className="wb-note">
                User assertions do not establish independent execution evidence.
              </p>
            </details>
            <fieldset>
              <legend>Basis stress</legend>
              <label>
                Basis changes bp
                <input
                  value={draft.basis_scenarios_bp.join(",")}
                  onChange={(e) =>
                    edit({ basis_scenarios_bp: e.target.value.split(",").map(Number) })
                  }
                />
              </label>
            </fieldset>
            <button className="wb-primary" type="submit" disabled={pending}>
              Run scenario
            </button>
          </form>
          <label className="wb-import">
            Import versioned JSON
            <input
              type="file"
              accept="application/json"
              onChange={async (e) => {
                const f = e.target.files?.[0];
                if (!f) return;
                try {
                  const v = JSON.parse(await f.text());
                  if (
                    v.schema_version !== "2.0" ||
                    !v.request?.meeting?.effective_date ||
                    !v.request?.futures_quote?.instrument ||
                    !v.request?.kalshi_yes_quote ||
                    !Array.isArray(v.request?.state_grid_bp) ||
                    v.request.state_grid_bp.length < 2 ||
                    !v.request?.state_probability_bounds ||
                    !Array.isArray(v.request?.basis_scenarios_bp)
                  )
                    throw new Error("Version 2 request required");
                  cancel();
                  setDraft(v.request);
                  setMode("Imported manual scenario");
                  setError("");
                } catch (err) {
                  setError(String(err));
                }
              }}
            />
          </label>
        </aside>
      </div>
      <footer>Research only · no order routing · model {analysis.model_version}</footer>
    </main>
  );
}
