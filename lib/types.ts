export type Mode = "live" | "case-study" | "manual";

export type ErrorDetail = { code: string; message: string; field?: string | null };

export type Envelope = {
  ok: boolean;
  mode: Mode;
  status: "complete" | "degraded" | "unavailable";
  analysis: Record<string, unknown> | null;
  data_quality: Record<string, unknown>;
  provenance: Array<Record<string, unknown>>;
  error?: ErrorDetail | null;
};

export type PayoffRow = {
  state_bp?: number;
  policy_move_bp?: number;
  move_bp?: number;
  combined_net_pnl_dollars: number;
  basis_change_bp?: number;
  capital_required_dollars?: number;
};

export type NormalizedAnalysis = {
  classification: string;
  reasons: string[];
  riskFlags: string[];
  expectedValue: number | null;
  worstPnl: number | null;
  capitalRequired: number | null;
  analysisTimestamp: string | null;
  syncStatus: string;
  executableStatus: string;
  chosenSide: string | null;
  kalshiPrice: number | null;
  kalshiContracts: number | null;
  futuresContracts: number | null;
  futuresDirection: string | null;
  fees: number | null;
  margin: number | null;
  bestPnl: number | null;
  breakEven: number | null;
  maxPrice: number | null;
  hurdlePrices: Record<string, number | null>;
  dayCount: { days_in_month?: number; pre_decision_days?: number; post_decision_days?: number };
  probability: Record<string, unknown>;
  inputs: Record<string, unknown>;
  implied: Record<string, unknown>;
  quoteSensitivity: Record<string, unknown>;
  basisModel: Record<string, unknown>;
  statePayoffs: PayoffRow[];
  basisStress: PayoffRow[];
};
