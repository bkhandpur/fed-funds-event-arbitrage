import type { Envelope, NormalizedAnalysis, PayoffRow } from "./types";

const asRecord = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
const asNumber = (value: unknown): number | null => (typeof value === "number" && Number.isFinite(value) ? value : null);
const asStrings = (value: unknown): string[] => (Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : []);

export function normalizeEnvelope(envelope: Envelope): NormalizedAnalysis | null {
  if (!envelope.analysis) return null;
  const a = asRecord(envelope.analysis);
  const classification = asRecord(a.classification);
  const inputs = asRecord(a.inputs);
  const limits = asRecord(a.limits);
  const hedge = asRecord(a.hedge);
  const expected = asRecord(a.expected_value);
  const chosen = typeof a.chosen_side === "string" ? a.chosen_side : "yes";
  const chosenEv = asRecord(expected[chosen]);
  const fixtureEv = asRecord(a.expected_value);
  const fixtureOrder = asRecord(fixtureEv.yes_order_dollars === undefined ? {} : fixtureEv);
  const rows = (Array.isArray(a.state_payoffs) ? a.state_payoffs : []) as PayoffRow[];
  const pnls = rows.map((row) => row.combined_net_pnl_dollars).filter(Number.isFinite);
  const payoffCapital = rows.map((row) => row.capital_required_dollars).filter((value): value is number => typeof value === "number" && Number.isFinite(value));
  const historical = envelope.mode === "case-study";
  const fee = historical ? asNumber(asRecord(a.fees).total_dollars) : asNumber(chosenEv.fee_total_dollars);
  const classificationEv = asNumber(classification.expected_value_dollars);
  const chosenPrice = historical ? asNumber(inputs.kalshi_yes_ask) : asNumber(chosenEv.price_dollars);
  const contracts = historical ? 500 : asNumber(inputs.kalshi_contracts);
  const futuresContracts = historical ? asNumber(asRecord(hedge.nearest).futures_contracts) : asNumber(hedge.nearest_futures_contracts);
  return {
    classification: typeof classification.label === "string" ? classification.label : "NO_TRADE",
    reasons: asStrings(classification.reason_codes),
    riskFlags: asStrings(classification.risk_flags),
    expectedValue: classificationEv ?? asNumber(fixtureOrder.yes_order_dollars),
    worstPnl: asNumber(classification.worst_case_pnl_dollars),
    capitalRequired: asNumber(limits.capital_required_dollars) ?? asNumber(chosenEv.estimated_total_capital_dollars) ?? (payoffCapital.length ? Math.max(...payoffCapital) : null),
    analysisTimestamp: typeof a.analysis_timestamp === "string" ? a.analysis_timestamp : null,
    syncStatus: String(envelope.data_quality.synchronization ?? "not_established"),
    executableStatus: String(envelope.data_quality.executability ?? "not_established"),
    chosenSide: historical ? "yes" : chosen,
    kalshiPrice: chosenPrice,
    kalshiContracts: contracts,
    futuresContracts,
    futuresDirection: futuresContracts === null ? null : futuresContracts > 0 ? "Long ZQ" : futuresContracts < 0 ? "Short ZQ" : "No hedge",
    fees: fee,
    margin: futuresContracts === null ? null : Math.abs(futuresContracts) * (asNumber(inputs.futures_margin_per_contract_dollars) ?? 2000),
    bestPnl: pnls.length ? Math.max(...pnls) : null,
    breakEven: historical ? asNumber(fixtureEv.break_even_probability) : asNumber(chosenEv.break_even_probability),
    maxPrice: historical ? null : asNumber(chosenEv.break_even_price_dollars),
    hurdlePrices: historical ? {} : Object.fromEntries(Object.entries(asRecord(chosenEv.ev_hurdle_prices)).map(([k, v]) => [k, asNumber(v)])),
    dayCount: asRecord(a.day_count),
    probability: asRecord(a.probability_model),
    inputs,
    implied: asRecord(a.futures_implied),
    quoteSensitivity: asRecord(a.quote_sensitivity),
    basisModel: asRecord(a.basis_model),
    statePayoffs: rows,
    basisStress: (Array.isArray(a.basis_stress) ? a.basis_stress : []) as PayoffRow[],
  };
}

export const reasonCopy: Record<string, string> = {
  NON_EXECUTABLE_QUOTES: "Futures quote is indicative rather than executable.",
  STALE_QUOTES: "At least one quote lacks a current source timestamp.",
  UNSYNCHRONIZED_QUOTES: "Quotes were not observed within the synchronization window.",
  SETTLEMENT_MISMATCH: "Settlement definitions have not been confirmed compatible.",
  INSUFFICIENT_DEPTH: "Available depth is insufficient or unknown.",
  LOSING_MODELED_STATE: "At least one modeled state loses money after costs.",
  LOSING_BASIS_STRESS: "Basis stress produces a negative payoff.",
  UNMODELED_OUTCOMES: "The modeled state set is not exhaustive.",
  POSITION_LIMIT_EXCEEDED: "The proposed size exceeds a position limit.",
  CAPITAL_LIMIT_EXCEEDED: "The proposed size exceeds the capital limit.",
  EV_BELOW_HURDLE: "Expected value does not clear the configured hurdle.",
  NON_INTEGER_SIZING: "The hedge cannot be constructed in whole contracts.",
  NO_STRICTLY_POSITIVE_STATE: "No modeled state has a strictly positive payoff.",
  CROSSED_MARKET: "A supplied quote is crossed and cannot be trusted for execution.",
};

export function reasonText(code: string): string {
  return reasonCopy[code] ?? code.replaceAll("_", " ").toLowerCase().replace(/^./, (c) => c.toUpperCase()) + ".";
}
