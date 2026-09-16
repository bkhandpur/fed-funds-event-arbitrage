from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.express as px
import streamlit as st

from fomc_basis.config import load_config
from fomc_basis.enums import ObservationKind
from fomc_basis.math.fed_funds import event_move_value_dollars
from fomc_basis.models import AnalysisRequest, FOMCMeeting, Quote
from fomc_basis.services.analysis import analyze_september_2026_fixture
from fomc_basis.services.backtest import replay_frame
from fomc_basis.services.generic_analysis import analyze_request
from fomc_basis.services.live_analysis import analyze_live_meeting

st.set_page_config(page_title="FOMC Basis Monitor", layout="wide")
st.title("FOMC Basis Monitor")
st.caption("Research-only analytics. A displayed percentage difference is not proof of arbitrage.")


def meeting_countdown(decision: date) -> str:
    statement = datetime.combine(decision, time(14), ZoneInfo("America/New_York")).astimezone(UTC)
    seconds = int((statement - datetime.now(UTC)).total_seconds())
    if seconds <= 0:
        return "Announcement time has passed"
    days, remainder = divmod(seconds, 86_400)
    hours, remainder = divmod(remainder, 3_600)
    minutes = remainder // 60
    return f"{days}d {hours}h {minutes}m"


def quote_health_rows(analysis: dict) -> list[dict]:
    inputs = analysis.get("inputs", {})
    if "futures_quote" not in inputs:
        return [
            {
                "venue": "Yahoo fixture",
                "instrument": inputs.get("futures_symbol"),
                "mode": "INDICATIVE_ONLY",
                "source timestamp": "not supplied",
                "bid": inputs.get("futures_bid"),
                "ask": inputs.get("futures_ask"),
            },
            {
                "venue": "Kalshi fixture",
                "instrument": "exactly +25 bp",
                "mode": "INDICATIVE_ONLY",
                "source timestamp": "not supplied",
                "bid": inputs.get("kalshi_yes_bid"),
                "ask": inputs.get("kalshi_yes_ask"),
            },
        ]
    quality = analysis.get("quote_quality", {})
    rows = []
    for venue, key, age_key, mode_key in (
        ("Futures", "futures_quote", "futures_age_seconds", "futures_mode"),
        ("Kalshi", "kalshi_yes_quote", "kalshi_age_seconds", "kalshi_mode"),
    ):
        quote = inputs[key]
        rows.append(
            {
                "venue": venue,
                "source": quote.get("source"),
                "instrument": quote.get("instrument"),
                "mode": quality.get(mode_key),
                "source timestamp": quote.get("source_timestamp") or "unavailable",
                "receipt timestamp": quote.get("receipt_timestamp"),
                "age seconds": quality.get(age_key),
                "bid": quote.get("bid"),
                "ask": quote.get("ask"),
                "last": quote.get("last"),
                "delayed": quote.get("delayed"),
            }
        )
    return rows


def local_history() -> tuple[pd.DataFrame, pd.DataFrame, str | None]:
    database_path = Path(str(load_config().storage.get("sqlite_path", "")))
    if not database_path.is_file():
        return pd.DataFrame(), pd.DataFrame(), f"No local database at {database_path}"
    try:
        with sqlite3.connect(f"file:{database_path.resolve()}?mode=ro", uri=True) as connection:
            tables = [
                "futures_quotes",
                "kalshi_order_book_snapshots",
                "effr_observations",
                "model_runs",
                "realized_fomc_outcomes",
            ]
            counts = pd.DataFrame(
                [
                    {
                        "record type": table,
                        "rows": connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0],
                    }
                    for table in tables
                ]
            )
            runs = []
            for timestamp, payload_text in connection.execute(
                "SELECT analysis_timestamp, payload_json FROM model_runs "
                "ORDER BY analysis_timestamp DESC LIMIT 25"
            ):
                payload = json.loads(payload_text)
                classification = payload.get("classification", {})
                runs.append(
                    {
                        "analysis timestamp": timestamp,
                        "meeting": payload.get("meeting", {}).get("decision_date"),
                        "classification": classification.get("label"),
                        "expected value ($)": classification.get("expected_value_dollars"),
                        "worst case ($)": classification.get("worst_case_pnl_dollars"),
                    }
                )
        return counts, pd.DataFrame(runs), None
    except (sqlite3.Error, json.JSONDecodeError) as exc:
        return pd.DataFrame(), pd.DataFrame(), f"Local history could not be read: {exc}"


@st.cache_data(ttl=60, show_spinner=False)
def cached_live_analysis(decision: date, outcome_bp: int, contracts: int) -> dict:
    return analyze_live_meeting(decision, outcome_bp, contracts)


data_mode = st.sidebar.selectbox(
    "Data mode", ["September 2026 fixture", "Manual meeting", "Live public data"]
)
contracts = st.sidebar.number_input("Kalshi contracts", 1, 10_000, 500)

if data_mode == "September 2026 fixture":
    tail = st.sidebar.slider("Assumed +50 bp tail", 0.0, 0.10, 0.02, 0.005)
    result = analyze_september_2026_fixture(int(contracts), tail)
    decision_for_display = date(2026, 9, 16)
    meeting_title = "September 16, 2026 fixture"
    current_effr_pct = result["inputs"]["effr_pct"]
    bounds_mapping = result["probability_bounds"]
    selected_p25 = result["tail_adjustment"]["p25"]
    yes_ev = result["expected_value"]["yes_per_contract_dollars"]
    event_value = result["event_25bp_value_per_future_dollars"]
    kalshi_summary = {
        "outcome": "exactly +25 bp",
        "yes_bid": result["inputs"]["kalshi_yes_bid"],
        "yes_ask": result["inputs"]["kalshi_yes_ask"],
        "depth": "not supplied",
    }
    overview_warning = result["warning"]
elif data_mode == "Manual meeting":
    states = [-50, -25, 0, 25, 50, 75]
    outcome_bp = st.sidebar.selectbox("Kalshi outcome (bp)", states, index=3)
    probability_mode = st.sidebar.selectbox(
        "Probability mode", ["bounds", "regularized", "two_state"]
    )
    tail = st.sidebar.slider("Maximum +50 bp probability", 0.0, 1.0, 0.10, 0.01)
    decision = st.sidebar.date_input("Decision date", date(2027, 1, 27))
    decision_for_display = decision
    effective = st.sidebar.date_input("Effective date", decision + timedelta(days=1))
    current_effr_pct = st.sidebar.number_input("Current EFFR (%)", value=3.63, step=0.01)
    futures_bid = st.sidebar.number_input("ZQ bid", value=96.3380, format="%.4f")
    futures_ask = st.sidebar.number_input("ZQ ask", value=96.3400, format="%.4f")
    yes_bid = st.sidebar.number_input("Kalshi YES bid ($)", value=0.87, format="%.2f")
    yes_ask = st.sidebar.number_input("Kalshi YES ask ($)", value=0.88, format="%.2f")
    depth = st.sidebar.number_input("Executable ask depth", 0, 1_000_000, 500)
    settlement_compatible = st.sidebar.checkbox("Settlement definitions compatible", False)
    timestamp = datetime.now(UTC)
    meeting = FOMCMeeting(
        start_date=decision - timedelta(days=1),
        decision_date=decision,
        effective_date=effective,
        source="dashboard manual input",
    )
    request = AnalysisRequest(
        meeting=meeting,
        futures_quote=Quote(
            instrument=f"ZQ-{effective:%Y-%m}",
            source="dashboard manual input",
            source_timestamp=timestamp,
            receipt_timestamp=timestamp,
            bid=Decimal(str(futures_bid)),
            ask=Decimal(str(futures_ask)),
            price_precision=Decimal("0.0025"),
            observation_kind=ObservationKind.MANUAL,
        ),
        kalshi_yes_quote=Quote(
            instrument=f"MANUAL-{outcome_bp:+d}BP",
            source="dashboard manual input",
            source_timestamp=timestamp,
            receipt_timestamp=timestamp,
            bid=Decimal(str(yes_bid)),
            ask=Decimal(str(yes_ask)),
            price_precision=Decimal("0.01"),
            observation_kind=ObservationKind.MANUAL,
        ),
        current_effr_pct=float(current_effr_pct),
        kalshi_outcome_bp=int(outcome_bp),
        two_state_low_bp=25 if outcome_bp == 0 else 0,
        probability_mode=probability_mode,
        state_probability_bounds={50: (0, tail)} if probability_mode != "two_state" else {},
        kalshi_contracts=int(contracts),
        kalshi_yes_ask_depth=int(depth),
        kalshi_no_ask_depth=int(depth),
        settlement_compatible=settlement_compatible,
        analysis_timestamp=timestamp,
    )
    try:
        result = analyze_request(request)
    except ValueError as exc:
        st.error(f"Manual inputs are not feasible: {exc}")
        st.stop()
    meeting_title = f"Manual meeting: {decision.isoformat()}"
    bounds_mapping = result["probability_model"]["bounds"]
    selected_p25 = result["probability_model"]["selected_distribution"].get(outcome_bp, 0.0)
    yes_ev = result["expected_value"]["yes"].get("conservative_ev_per_contract_dollars", 0)
    counts = result["day_count"]
    event_value = event_move_value_dollars(
        abs(outcome_bp), counts["days_in_month"], counts["post_decision_days"]
    )
    kalshi_summary = {
        "outcome": f"exactly {outcome_bp:+d} bp",
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "depth": depth,
    }
    overview_warning = "Manual observations are user-supplied. Verify timestamps, depth, and exact settlement rules."
else:
    decision = st.sidebar.date_input("Official decision date", date.today())
    decision_for_display = decision
    outcome_bp = st.sidebar.selectbox("Kalshi outcome (bp)", [-50, -25, 0, 25, 50], index=3)
    try:
        with st.spinner("Loading public market data..."):
            result = cached_live_analysis(decision, int(outcome_bp), int(contracts))
    except (RuntimeError, LookupError, ValueError) as exc:
        st.error(f"Live analysis unavailable: {exc}")
        st.stop()
    meeting_title = f"Live public data: {decision.isoformat()}"
    current_effr_pct = result["inputs"]["current_effr_pct"]
    bounds_mapping = result["probability_model"]["bounds"]
    selected_p25 = result["probability_model"]["selected_distribution"].get(outcome_bp, 0.0)
    yes_ev = result["expected_value"]["yes"].get("conservative_ev_per_contract_dollars", 0)
    counts = result["day_count"]
    event_value = event_move_value_dollars(
        abs(outcome_bp), counts["days_in_month"], counts["post_decision_days"]
    )
    live_context = result["live_context"]
    kalshi_summary = {
        "outcome": live_context["market"].get("outcome_bucket") or f"{outcome_bp:+d} bp",
        **live_context["top_of_book"],
    }
    overview_warning = (
        "Public Yahoo data may be delayed or indicative. Live mode never upgrades missing bid/ask "
        "or missing source timestamps into executable quotes."
    )

tabs = st.tabs(
    [
        "Overview",
        "Futures Curve",
        "Kalshi",
        "Probability Model",
        "Trade Analysis",
        "Risk",
        "Historical",
    ]
)
with tabs[0]:
    st.subheader(meeting_title)
    st.warning(overview_warning)
    st.metric("Classification", result["classification"]["label"])
    st.metric("Countdown to 2:00 p.m. ET statement", meeting_countdown(decision_for_display))
    st.metric("Current EFFR assumption", f"{current_effr_pct:.2f}%")
    if "live_context" in result and result["live_context"]["target_midpoint_pct"] is not None:
        context = result["live_context"]
        st.metric(
            "Current target range",
            f"{context['target_lower_pct']:.2f}% – {context['target_upper_pct']:.2f}%",
        )
        st.metric("Target midpoint", f"{context['target_midpoint_pct']:.3f}%")
    st.write("Data-source health and timestamps")
    st.dataframe(pd.DataFrame(quote_health_rows(result)), width="stretch")
    st.write("Classification reasons:", result["classification"]["reason_codes"])
with tabs[1]:
    futures_frame = pd.DataFrame(result["futures_implied"]).T.reset_index(names="quote side")
    st.dataframe(futures_frame, width="stretch")
    st.plotly_chart(
        px.line(
            futures_frame,
            x="quote side",
            y="post_meeting_effr_pct",
            markers=True,
            title="Post-meeting EFFR implied by executable sides and midpoint",
        ),
        width="stretch",
    )
    counts = result["day_count"]
    st.write(
        "Monthly averaging weights:",
        {
            "pre-decision calendar days": counts["pre_decision_days"],
            "post-decision calendar days": counts["post_decision_days"],
            "days in contract month": counts["days_in_month"],
        },
    )
    if sensitivity := result.get("quote_sensitivity"):
        st.write("Quote and tick sensitivity:", sensitivity)
    st.caption("ZQ is 100 minus average calendar-month EFFR, not a direct meeting probability.")
with tabs[2]:
    st.dataframe(pd.DataFrame([kalshi_summary]), width="stretch")
    if fee_schedule := result.get("fee_schedule", result.get("fees")):
        st.write("Fee assumptions:", fee_schedule)
    if "live_context" in result:
        market = result["live_context"]["market"]
        st.write("Market title:", market.get("title"))
        st.write(
            "Settlement/rules excerpt:", market.get("rules") or market.get("settlement_description")
        )
    st.warning("Settlement wording and executable depth must be verified before conclusions.")
with tabs[3]:
    interval = result["25_BP_EQUIVALENT_PROBABILITY_INTERVAL"]
    st.metric("25 bp equivalent interval", f"{interval[0]:.2%} – {interval[1]:.2%}")
    st.metric("Model-selected outcome probability", f"{selected_p25:.2%}")
    bounds = pd.DataFrame(
        [
            {"state_bp": state, "minimum": pair[0], "maximum": pair[1]}
            for state, pair in bounds_mapping.items()
        ]
    )
    st.plotly_chart(
        px.bar(
            bounds,
            x="state_bp",
            y=["minimum", "maximum"],
            barmode="group",
            title="Feasible probability bounds",
        ),
        width="stretch",
    )
    if model := result.get("probability_model"):
        selected = pd.DataFrame(
            [
                {"state_bp": state, "selected_probability": probability}
                for state, probability in model["selected_distribution"].items()
            ]
        )
        st.plotly_chart(
            px.bar(
                selected,
                x="state_bp",
                y="selected_probability",
                title="Model-selected distribution (not uniquely identified)",
            ),
            width="stretch",
        )
    st.caption(
        "The selected distribution is model-dependent; one futures expectation does not identify it."
    )
with tabs[4]:
    st.metric("Conservative YES EV per contract", f"${yes_ev:.4f}")
    st.metric("Selected move value per future", f"${event_value:.2f}")
    if "limits" in result:
        st.json(result["limits"])
    if (expected_value := result.get("expected_value")) and "yes" in expected_value:
        st.dataframe(pd.DataFrame(expected_value).T, width="stretch")
    if hedge := result.get("hedge"):
        st.write("Integer hedge and residual:", hedge)
    payoffs = pd.DataFrame(result["state_payoffs"])
    st.dataframe(payoffs, width="stretch")
    st.plotly_chart(
        px.bar(
            payoffs,
            x="move_bp",
            y="combined_net_pnl_dollars",
            color_discrete_sequence=["#d97706"],
            title="State-contingent net P&L",
        ),
        width="stretch",
    )
with tabs[5]:
    for flag in result["classification"]["risk_flags"]:
        st.error(flag)
    st.write("Failed arbitrage conditions:", result["classification"]["reason_codes"])
    if "quote_quality" in result:
        st.write("Quote quality:", result["quote_quality"])
    if "basis_model" in result:
        st.write("Basis assumptions:", result["basis_model"])
    if basis_rows := result.get("basis_stress"):
        basis_frame = pd.DataFrame(basis_rows)
        worst_basis = (
            basis_frame.groupby("basis_change_bp", as_index=False)["combined_net_pnl_dollars"]
            .min()
            .rename(columns={"combined_net_pnl_dollars": "worst_net_pnl_dollars"})
        )
        st.plotly_chart(
            px.line(
                worst_basis,
                x="basis_change_bp",
                y="worst_net_pnl_dollars",
                markers=True,
                title="Worst state P&L under EFFR/target basis stress",
            ),
            width="stretch",
        )
with tabs[6]:
    counts, stored_runs, history_warning = local_history()
    if history_warning:
        st.info(history_warning)
    else:
        st.write("Locally accumulated research records")
        st.dataframe(counts, width="stretch")
        st.write("Recent saved signals")
        st.dataframe(stored_runs, width="stretch")
    st.info("Upload replay data; mixed provenance is explicitly disclosed in results.")
    upload = st.file_uploader("Replay CSV", type="csv")
    if upload is not None:
        try:
            replay = replay_frame(pd.read_csv(upload))
            st.json(replay["metrics"])
            st.dataframe(pd.DataFrame(replay["calibration"]), width="stretch")
            for warning in replay["warnings"]:
                st.warning(warning)
        except ValueError as exc:
            st.error(str(exc))
    st.write("Supported horizons: 30d, 14d, 7d, 3d, 1d, and 1h before a meeting.")
