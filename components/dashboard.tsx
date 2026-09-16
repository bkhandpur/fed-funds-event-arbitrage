"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import { normalizeEnvelope, reasonText } from "@/lib/analysis";
import type { Envelope, Mode, NormalizedAnalysis } from "@/lib/types";
import { PayoffChart } from "./payoff-chart";

const TABS = ["Overview", "Market Inputs", "Probability Decomposition", "Trade Construction", "State Payoffs", "Risk and Data Quality", "Methodology"] as const;
type Tab = (typeof TABS)[number];

const money = (value: number | null, digits = 0) => value === null ? "Unavailable" : new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: digits }).format(value);
const pct = (value: number | null) => value === null ? "Unavailable" : `${(value * 100).toFixed(1)}%`;
const rec = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const val = (value: unknown): string => value === null || value === undefined || value === "" ? "Unavailable" : String(value);

async function fetchEnvelope(url: string, init?: RequestInit): Promise<Envelope> {
  const response = await fetch(url, init);
  const payload = await response.json() as Envelope | { error?: { message?: string } };
  if (!response.ok) throw new Error("error" in payload ? payload.error?.message ?? "Request failed" : "Request failed");
  return payload as Envelope;
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return <div className={`metric ${tone ?? ""}`}><span>{label}</span><strong>{value}</strong></div>;
}

function QualityValue({ value }: { value: unknown }) {
  const unavailable = value === null || value === undefined || value === "not_established" || value === "unavailable";
  return <span className={unavailable ? "quality-missing" : "quality-present"}>{val(value).replaceAll("_", " ")}</span>;
}

function Decision({ analysis, mode }: { analysis: NormalizedAnalysis; mode: Mode }) {
  const qualifies = analysis.classification === "TRUE_ARBITRAGE";
  const conclusion = qualifies
    ? "All modeled execution and risk gates are satisfied under the supplied evidence."
    : "No executable trade under the supplied evidence.";
  return (
    <section className={`decision-card ${qualifies ? "pass" : "blocked"}`} aria-labelledby="decision-title">
      <div className="decision-lead">
        <div><p className="eyebrow">Current classification</p><h1 id="decision-title">{analysis.classification.replaceAll("_", " ")}</h1></div>
        <span className={`status-chip ${qualifies ? "pass" : "blocked"}`}>{qualifies ? "Gates satisfied" : "Blocked"}</span>
      </div>
      <p className="conclusion">{conclusion}</p>
      <div className="metric-grid decision-metrics">
        <Metric label="Expected value" value={money(analysis.expectedValue, 2)} />
        <Metric label="Worst modeled P&L" value={money(analysis.worstPnl, 2)} tone={(analysis.worstPnl ?? 0) < 0 ? "negative-text" : ""} />
        <Metric label="Capital required" value={money(analysis.capitalRequired)} />
        <Metric label="Data mode" value={mode === "case-study" ? "Case study" : mode === "live" ? "Live public" : "Manual"} />
        <Metric label="Analysis time" value={analysis.analysisTimestamp ? new Date(analysis.analysisTimestamp).toLocaleString() : "Unavailable"} />
        <Metric label="Quote sync" value={analysis.syncStatus.replaceAll("_", " ")} />
        <Metric label="Executability" value={analysis.executableStatus.replaceAll("_", " ")} />
      </div>
      {!qualifies && <div className="blockers"><h2>Blocking conditions</h2><ul>{analysis.reasons.map((code) => <li key={code}><span>{reasonText(code)}</span><code>{code}</code></li>)}</ul></div>}
    </section>
  );
}

function ManualForm({ onResult, setLoading }: { onResult: (value: Envelope) => void; setLoading: (value: boolean) => void }) {
  const [error, setError] = useState<string | null>(null);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError(null); setLoading(true);
    const f = new FormData(event.currentTarget);
    const s = (name: string) => String(f.get(name) ?? "").trim();
    const n = (name: string) => s(name) === "" ? null : Number(s(name));
    const now = new Date().toISOString();
    const sourceTime = (name: string) => s(name) ? new Date(s(name)).toISOString() : null;
    const decision = s("decision_date");
    try {
      const payload = {
        analysis: {
          meeting: { start_date: decision, decision_date: decision, effective_date: s("effective_date"), source: "Manual user input" },
          futures_quote: { instrument: s("zq_contract"), source: "Manual user input", source_timestamp: sourceTime("futures_timestamp"), receipt_timestamp: now, bid: n("futures_bid"), ask: n("futures_ask"), last: n("futures_last"), price_precision: 0.0025, delayed: false, observation_kind: "MANUAL_USER_SUPPLIED" },
          kalshi_yes_quote: { instrument: s("kalshi_ticker"), source: "Manual user input", source_timestamp: sourceTime("kalshi_timestamp"), receipt_timestamp: now, bid: n("yes_bid"), ask: n("yes_ask"), price_precision: 0.01, delayed: false, observation_kind: "MANUAL_USER_SUPPLIED" },
          current_effr_pct: n("effr"), kalshi_outcome_bp: n("outcome_bp"), kalshi_contracts: n("contracts"),
          state_grid_bp: [-50, -25, 0, 25, 50, 75], probability_mode: "bounds", prior: [0.01, 0.04, 0.45, 0.43, 0.05, 0.02],
          state_probability_bounds: { "-50": [0, n("tail_max")], "-25": [0, 0.02], "50": [0, n("tail_max")], "75": [0, 0.01] },
          basis_scenarios_bp: [-(n("basis_stress") ?? 0), 0, n("basis_stress") ?? 0],
          kalshi_fee_coefficient: n("kalshi_fee"), futures_round_trip_cost_per_contract_dollars: n("futures_cost"), slippage_per_kalshi_contract_dollars: n("slippage"),
          futures_margin_per_contract_dollars: n("margin"), capital_limit_dollars: n("capital"), max_kalshi_contracts: 10000, max_futures_contracts: 100,
          settlement_compatible: f.get("settlement") === "on", all_outcomes_modeled: true, multiple_meetings_in_contract: f.get("multiple") === "on",
          kalshi_yes_ask_depth: n("yes_depth"), kalshi_no_ask_depth: n("no_depth"), analysis_timestamp: now,
          stale_warning_seconds: 60, stale_hard_seconds: 300, sync_hard_seconds: n("sync_window"), minimum_futures_precision: 0.0025,
          current_effr_target_basis_bp: n("current_basis"), assumed_post_meeting_basis_bp: n("assumed_basis")
        }
      };
      const result = await fetchEnvelope("/api/analyze", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      onResult(result); if (!result.ok) setError(result.error?.message ?? "Analysis unavailable");
    } catch (err) { setError(err instanceof Error ? err.message : "Analysis failed"); } finally { setLoading(false); }
  }
  return (
    <form className="manual-form" onSubmit={submit} aria-label="Manual scenario inputs">
      <div className="form-header"><div><h2>Manual scenario</h2><p>Supply observed evidence. Blank quote or depth fields remain unavailable.</p></div><button className="primary" type="submit">Run Python model</button></div>
      <fieldset><legend>Contract and timing</legend><label>FOMC decision date<input name="decision_date" type="date" defaultValue="2026-09-16" required /></label><label>Effective date<input name="effective_date" type="date" defaultValue="2026-09-17" required /></label><label>Current EFFR (%)<input name="effr" type="number" step="0.001" defaultValue="3.63" required /></label><label>ZQ contract<input name="zq_contract" defaultValue="ZQU26.CBT" required /></label><label>Kalshi ticker<input name="kalshi_ticker" defaultValue="KXFED-26SEP-T3.75" required /></label><label>Modeled outcome (bp)<input name="outcome_bp" type="number" step="25" defaultValue="25" required /></label></fieldset>
      <fieldset><legend>Observed quotes</legend><label>Futures bid<input name="futures_bid" type="number" step="0.0001" defaultValue="96.2600" /></label><label>Futures ask<input name="futures_ask" type="number" step="0.0001" defaultValue="96.2625" /></label><label>Futures last<input name="futures_last" type="number" step="0.0001" /></label><label>Futures source time<input name="futures_timestamp" type="datetime-local" /></label><label>YES bid ($)<input name="yes_bid" type="number" min="0" max="1" step="0.01" defaultValue="0.87" /></label><label>YES ask ($)<input name="yes_ask" type="number" min="0" max="1" step="0.01" defaultValue="0.88" /></label><label>Kalshi source time<input name="kalshi_timestamp" type="datetime-local" /></label><label>YES ask depth<input name="yes_depth" type="number" min="0" /></label><label>NO ask depth<input name="no_depth" type="number" min="0" /></label></fieldset>
      <fieldset><legend>Costs and limits</legend><label>Contract count<input name="contracts" type="number" min="1" defaultValue="500" required /></label><label>Capital limit ($)<input name="capital" type="number" min="1" defaultValue="10000" required /></label><label>Futures round-trip cost ($)<input name="futures_cost" type="number" min="0" step="0.01" defaultValue="6.04" required /></label><label>Futures margin / contract ($)<input name="margin" type="number" min="0" defaultValue="2000" required /></label><label>Kalshi fee coefficient<input name="kalshi_fee" type="number" min="0" step="0.001" defaultValue="0.07" required /></label><label>Slippage / Kalshi contract ($)<input name="slippage" type="number" min="0" step="0.001" defaultValue="0" required /></label></fieldset>
      <fieldset><legend>Model and hard gates</legend><label>Basis stress (±bp)<input name="basis_stress" type="number" min="0" step="0.5" defaultValue="2" required /></label><label>Current EFFR/target basis (bp)<input name="current_basis" type="number" step="0.1" defaultValue="-12" /></label><label>Assumed post-meeting basis (bp)<input name="assumed_basis" type="number" step="0.1" defaultValue="-12" /></label><label>Maximum tail probability<input name="tail_max" type="number" min="0" max="1" step="0.01" defaultValue="0.02" required /></label><label>Maximum quote-age difference (seconds)<input name="sync_window" type="number" min="0" defaultValue="60" required /></label><label className="check"><input name="settlement" type="checkbox" /> Settlement definitions independently confirmed compatible</label><label className="check"><input name="multiple" type="checkbox" /> Multiple FOMC meetings in ZQ month</label></fieldset>
      {error && <p className="form-error" role="alert">{error}</p>}
    </form>
  );
}

export function Dashboard() {
  const [mode, setMode] = useState<Mode>("case-study");
  const [tab, setTab] = useState<Tab>("Overview");
  const [envelope, setEnvelope] = useState<Envelope | null>(null);
  const [loading, setLoading] = useState(true);
  const [meeting, setMeeting] = useState("2026-09-16");
  const [outcome, setOutcome] = useState(25);
  const [contracts, setContracts] = useState(500);

  useEffect(() => {
    if (mode === "manual") return;
    let active = true;
    const url = mode === "case-study" ? "/api/case-study" : `/api/live?meeting=${meeting}&outcome_bp=${outcome}&contracts=${contracts}`;
    fetchEnvelope(url).then((value) => { if (active) setEnvelope(value); }).catch((error: Error) => { if (active) setEnvelope({ ok: false, mode, status: "unavailable", analysis: null, data_quality: {}, provenance: [], error: { code: "REQUEST_FAILED", message: error.message } }); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [mode, meeting, outcome, contracts]);

  const analysis = useMemo(() => envelope ? normalizeEnvelope(envelope) : null, [envelope]);
  const a = envelope?.analysis ? rec(envelope.analysis) : {};
  const input = analysis?.inputs ?? {};
  const fq = rec(input.futures_quote);
  const kq = rec(input.kalshi_yes_quote);
  const live = rec(a.live_context); const market = rec(live.market); const top = rec(live.top_of_book);

  return (
    <main>
      <header className="site-header"><a className="brand" href="#top">FOMC / BASIS</a><div className="header-meta"><span>Cross-market research monitor</span><a href="https://github.com/bkhandpur/fed-funds-event-arbitrage">Source</a></div></header>
      <div id="top" className="intro"><div><p className="eyebrow">Kalshi × 30-Day Fed Funds futures</p><h2>Is the probability gap a trade—or a measurement problem?</h2><p>Normalize the monthly-average futures contract, price both legs, and test every execution gate. The Python model remains the analytical source of truth.</p></div><div className="research-note"><span>Research monitor</span><strong>No order placement</strong><p>Public and user-supplied data only.</p></div></div>
      <section className="controls" aria-label="Analysis controls"><div className="segmented" role="group" aria-label="Data mode">{(["live", "case-study", "manual"] as Mode[]).map((item) => <button key={item} className={mode === item ? "active" : ""} onClick={() => { setMode(item); if (item === "manual") { setEnvelope(null); setLoading(false); } else if (item !== mode) { setLoading(true); } }}>{item === "live" ? "Live public data" : item === "case-study" ? "Historical case study" : "Manual scenario"}</button>)}</div>{mode === "live" && <div className="live-controls"><label>Meeting<input type="date" value={meeting} onChange={(e) => { setMeeting(e.target.value); setLoading(true); }} /></label><label>Outcome (bp)<input type="number" step="25" value={outcome} onChange={(e) => { setOutcome(Number(e.target.value)); setLoading(true); }} /></label><label>Contracts<input type="number" min="1" value={contracts} onChange={(e) => { setContracts(Number(e.target.value)); setLoading(true); }} /></label></div>}</section>
      {mode === "live" && <div className="source-warning"><strong>Live means recently retrieved public data—not guaranteed executable data.</strong> Yahoo is indicative and potentially delayed; its last price is not a CME bid or ask. Kalshi and Yahoo observations may not be synchronized. True arbitrage is impossible while any hard execution gate fails.</div>}
      {mode === "manual" && !analysis && <ManualForm onResult={setEnvelope} setLoading={setLoading} />}
      {loading && <div className="loading" role="status">Running evidence checks…</div>}
      {!loading && envelope && !envelope.ok && !analysis && <section className="empty-state"><p className="eyebrow">{envelope.error?.code ?? "DATA UNAVAILABLE"}</p><h1>No analysis is available for this selection.</h1><p>{envelope.error?.message ?? "The required source inputs could not be retrieved."}</p>{mode === "live" && <p>Selected meeting: <strong>{meeting}</strong> · searched outcome: <strong>{outcome >= 0 ? "+" : ""}{outcome} bp</strong>. No quote or depth has been inferred.</p>}</section>}
      {analysis && envelope && <><Decision analysis={analysis} mode={mode} /><nav className="tabs" aria-label="Research sections">{TABS.map((item) => <button key={item} className={tab === item ? "active" : ""} onClick={() => setTab(item)}>{item}</button>)}</nav><div className="tab-panel">
        {tab === "Overview" && <section><div className="section-title"><p className="eyebrow">Decision summary</p><h2>Why the apparent gap does not clear the gate</h2></div><div className="two-col"><div className="panel"><h3>Model conclusion</h3><p>{analysis.classification === "TRUE_ARBITRAGE" ? "The supplied evidence passes every conservative test." : "Positive expected value is not enough. Unknown timing, depth, settlement, or a losing state prevents an arbitrage label."}</p><dl className="compact-list"><div><dt>Selected side</dt><dd>{analysis.chosenSide?.toUpperCase() ?? "Unavailable"}</dd></div><div><dt>Kalshi entry</dt><dd>{money(analysis.kalshiPrice, 2)}</dd></div><div><dt>Break-even probability</dt><dd>{pct(analysis.breakEven)}</dd></div><div><dt>Hedge</dt><dd>{analysis.futuresDirection ?? "Unavailable"} {analysis.futuresContracts === null ? "" : Math.abs(analysis.futuresContracts)}</dd></div></dl></div><div className="panel"><h3>Evidence standard</h3><p>Arbitrage requires executable, fresh, synchronized, sufficiently deep quotes; compatible settlement; nonnegative P&amp;L in every modeled and basis-stress state; and valid capital and position limits.</p><div className="tag-list">{analysis.riskFlags.map((flag) => <code key={flag}>{flag}</code>)}</div></div></div></section>}
        {tab === "Market Inputs" && <section><div className="section-title"><p className="eyebrow">Observed evidence</p><h2>Quotes stay attached to their source and timing</h2></div><div className="quote-grid"><article className="quote-card"><header><span>Kalshi event contract</span><strong>{val(market.ticker ?? kq.instrument ?? a.fixture)}</strong></header><div className="quote-main"><span>YES bid / ask</span><strong>{val(top.yes_bid ?? kq.bid ?? input.kalshi_yes_bid)} / {val(top.yes_ask ?? kq.ask ?? input.kalshi_yes_ask)}</strong></div><dl><div><dt>Title</dt><dd>{val(market.title)}</dd></div><div><dt>Depth</dt><dd>{val(top.yes_ask_quantity)}</dd></div><div><dt>Source time</dt><dd>{val(kq.source_timestamp)}</dd></div><div><dt>Receipt time</dt><dd>{val(kq.receipt_timestamp)}</dd></div><div><dt>Designation</dt><dd>{val(rec(envelope.data_quality).kalshi_mode ?? "not established")}</dd></div></dl></article><article className="quote-card"><header><span>30-Day Fed Funds future</span><strong>{val(fq.instrument ?? input.futures_symbol)}</strong></header><div className="quote-main"><span>Bid / ask / last</span><strong>{val(fq.bid ?? input.futures_bid)} / {val(fq.ask ?? input.futures_ask)} / {val(fq.last)}</strong></div><dl><div><dt>Source</dt><dd>{val(fq.source ?? (mode === "case-study" ? "Historical user-supplied observation" : null))}</dd></div><div><dt>Depth</dt><dd>Unavailable</dd></div><div><dt>Source time</dt><dd>{val(fq.source_timestamp)}</dd></div><div><dt>Receipt time</dt><dd>{val(fq.receipt_timestamp)}</dd></div><div><dt>Designation</dt><dd>{val(rec(envelope.data_quality).futures_mode ?? "not established")}</dd></div></dl></article></div><div className="panel provenance"><h3>Input provenance</h3><table><thead><tr><th>Input</th><th>Source</th><th>Source timestamp</th><th>Designation</th></tr></thead><tbody>{envelope.provenance.map((row, i) => <tr key={i}><td>{val(row.input)}</td><td>{val(row.source)}</td><td>{val(row.source_timestamp)}</td><td>{val(row.designation)}</td></tr>)}</tbody></table></div></section>}
        {tab === "Probability Decomposition" && <section><div className="section-title"><p className="eyebrow">Monthly-average normalization</p><h2>ZQ implies an expected rate, not a unique outcome probability</h2></div><div className="weights"><div style={{ flex: analysis.dayCount.pre_decision_days ?? 1 }}><strong>{analysis.dayCount.pre_decision_days ?? "—"} days</strong><span>Pre-meeting EFFR</span></div><div className="post" style={{ flex: analysis.dayCount.post_decision_days ?? 1 }}><strong>{analysis.dayCount.post_decision_days ?? "—"} days</strong><span>Post-meeting expectation</span></div></div><div className="equation"><span>100 − F</span><span>=</span><span>(d<sub>pre</sub>r<sub>pre</sub> + d<sub>post</sub>r<sub>post</sub>) / D</span></div><div className="two-col"><div className="panel"><h3>Futures-implied scenarios</h3><pre>{JSON.stringify(analysis.implied, null, 2)}</pre></div><div className="panel"><h3>Identified bounds vs selected model</h3><p>Bounds are supported by the expectation and constraints. The selected distribution is a regularized choice for point EV display; it is not uniquely implied by futures.</p><pre>{JSON.stringify(analysis.probability, null, 2)}</pre></div></div></section>}
        {tab === "Trade Construction" && <section><div className="section-title"><p className="eyebrow">Construction or threshold</p><h2>{analysis.classification === "TRUE_ARBITRAGE" ? "Modeled position" : "What would need to change?"}</h2></div><div className="metric-grid"><Metric label="Kalshi side" value={analysis.chosenSide?.toUpperCase() ?? "Unavailable"} /><Metric label="Entry price" value={money(analysis.kalshiPrice, 2)} /><Metric label="Contracts" value={val(analysis.kalshiContracts)} /><Metric label="ZQ hedge" value={`${analysis.futuresDirection ?? "Unavailable"} ${analysis.futuresContracts === null ? "" : Math.abs(analysis.futuresContracts)}`} /><Metric label="Estimated fees" value={money(analysis.fees, 2)} /><Metric label="Estimated margin" value={money(analysis.margin)} /><Metric label="Total capital" value={money(analysis.capitalRequired)} /><Metric label="Best / worst P&L" value={`${money(analysis.bestPnl)} / ${money(analysis.worstPnl)}`} /></div>{analysis.classification !== "TRUE_ARBITRAGE" && <div className="threshold-panel"><div><span>Maximum entry for zero EV</span><strong>{money(analysis.maxPrice, 3)}</strong></div>{Object.entries(analysis.hurdlePrices).map(([key, price]) => <div key={key}><span>Maximum price for {key.replace("pct", "%")} EV</span><strong>{money(price, 3)}</strong></div>)}<div><span>Minimum market depth</span><strong>{analysis.kalshiContracts ?? "Unavailable"} contracts</strong></div><div><span>Maximum quote-age difference</span><strong>{val(rec(envelope.data_quality).synchronization_threshold_seconds)} seconds</strong></div><div><span>Unresolved settlement assumption</span><strong>{analysis.reasons.includes("SETTLEMENT_MISMATCH") ? "Compatibility not verified" : "None recorded"}</strong></div></div>}</section>}
        {tab === "State Payoffs" && <section><div className="section-title"><p className="eyebrow">Downside before labels</p><h2>Net P&amp;L across policy states</h2></div><div className="panel chart-panel"><h3>Central basis assumption</h3><PayoffChart rows={analysis.statePayoffs} /></div><div className="panel chart-panel"><h3>Basis stress · all state/scenario rows</h3><PayoffChart rows={analysis.basisStress.length ? analysis.basisStress : analysis.statePayoffs} label="Basis-stressed net P&L" /></div></section>}
        {tab === "Risk and Data Quality" && <section><div className="section-title"><p className="eyebrow">Evidence ledger</p><h2>Known, unknown, and disqualifying inputs</h2></div><div className="quality-grid">{Object.entries(envelope.data_quality).filter(([, v]) => !Array.isArray(v) && typeof v !== "object").map(([key, value]) => <div key={key}><span>{key.replaceAll("_", " ")}</span><QualityValue value={value} /></div>)}</div><div className="panel"><h3>Risk flags retained from Python</h3><div className="tag-list">{analysis.riskFlags.map((flag) => <code key={flag}>{flag}</code>)}</div><h3>Basis model</h3><pre>{JSON.stringify(analysis.basisModel, null, 2)}</pre></div></section>}
        {tab === "Methodology" && <section><div className="section-title"><p className="eyebrow">Methodology</p><h2>Classification is harder than finding positive EV</h2></div><div className="method-grid"><article><span>01</span><h3>Normalize the contract</h3><p>ZQ settles to the calendar-month average EFFR. Day weights isolate the post-meeting expectation.</p></article><article><span>02</span><h3>Respect identification</h3><p>A futures expectation does not uniquely determine an exact +25 bp probability when tails are possible.</p></article><article><span>03</span><h3>Price executable legs</h3><p>Indicative last prices are not bid/ask quotes. Missing sides and depth remain missing.</p></article><article><span>04</span><h3>Stress every state</h3><p>Fees, integer sizing, basis changes, capital and settlement mapping determine the classification.</p></article></div><div className="classification-table"><div><strong>TRUE ARBITRAGE</strong><span>Every hard gate passes and no modeled state loses.</span></div><div><strong>NEAR ARBITRAGE</strong><span>State payoffs survive, with limited residual basis risk.</span></div><div><strong>RELATIVE VALUE</strong><span>Positive model EV with real downside in at least one state.</span></div><div><strong>NO TRADE</strong><span>Evidence, execution or payoff gates do not qualify.</span></div></div><p className="source-links">Inspect the <a href="https://github.com/bkhandpur/fed-funds-event-arbitrage/blob/main/src/fomc_basis/services/generic_analysis.py">analysis service</a>, <a href="https://github.com/bkhandpur/fed-funds-event-arbitrage/blob/main/src/fomc_basis/math/arbitrage.py">classification logic</a>, and <a href="https://github.com/bkhandpur/fed-funds-event-arbitrage#readme">README</a>.</p></section>}
      </div></>}
      {mode === "manual" && analysis && <button className="secondary reset-manual" onClick={() => setEnvelope(null)}>Edit manual scenario</button>}
      <footer><span>Research only. Never places orders.</span><span>Python model · public data · explicit uncertainty</span></footer>
    </main>
  );
}
