"""Streamlit dashboard: site selector, date-range selector, time-series chart
with IQR fence band, and tier-distinguished anomaly flags (tab 1); a
tool-calling AI agent over the same store (tab 2, see src/agent.py).
"""

import sys
from pathlib import Path

import duckdb
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
import agent  # noqa: E402
import tools  # noqa: E402
from config import DB_PATH, DEMO_DB_PATH, METRIC_SOLAR, METRIC_WIND  # noqa: E402

st.set_page_config(page_title="ENGIE Take-Home -- Solar & Wind Dashboard", layout="wide")

METRIC_LABELS = {METRIC_SOLAR: "Solar Radiation (GHI)", METRIC_WIND: "Wind Speed (100m)"}
TIER_COLORS = {1: "#d62728", 2: "#ff7f0e"}
TIER_LABELS = {1: "Tier 1 -- physical validity", 2: "Tier 2 -- contextual (IQR)"}


@st.cache_resource
def get_connection(db_path: str):
    return duckdb.connect(db_path, read_only=True)


@st.cache_data
def load_observations(db_path: str) -> pd.DataFrame:
    con = get_connection(db_path)
    df = con.execute("SELECT * FROM observations ORDER BY site_id, metric, timestamp").fetchdf()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


def render_chart(df: pd.DataFrame, metric_label: str, unit: str) -> go.Figure:
    fig = go.Figure()

    fenced = df.dropna(subset=["fence_lower", "fence_upper"])
    if not fenced.empty:
        fig.add_trace(
            go.Scatter(
                x=fenced["timestamp"], y=fenced["fence_upper"], mode="lines",
                line=dict(width=0), showlegend=False, hoverinfo="skip",
            )
        )
        fig.add_trace(
            go.Scatter(
                x=fenced["timestamp"], y=fenced["fence_lower"], mode="lines",
                line=dict(width=0), fill="tonexty", fillcolor="rgba(31,119,180,0.15)",
                name="IQR fence band", hoverinfo="skip",
            )
        )

    fig.add_trace(
        go.Scatter(
            x=df["timestamp"], y=df["value"], mode="lines", name=metric_label,
            line=dict(color="#1f77b4", width=1.5),
            hovertemplate="%{x}<br>" + metric_label + ": %{y:.1f} " + unit + "<extra></extra>",
        )
    )

    for tier, color in TIER_COLORS.items():
        sub = df[df["anomaly_tier"] == tier]
        if sub.empty:
            continue
        fig.add_trace(
            go.Scatter(
                x=sub["timestamp"], y=sub["value"], mode="markers", name=TIER_LABELS[tier],
                marker=dict(color=color, size=9, symbol="x" if tier == 1 else "circle-open", line=dict(width=2)),
                text=sub["anomaly_reason"],
                hovertemplate="%{x}<br>%{y:.1f} " + unit + "<br>%{text}<extra></extra>",
            )
        )

    fig.update_layout(
        height=480, margin=dict(l=10, r=10, t=30, b=10),
        xaxis_title="Time", yaxis_title=f"{metric_label} ({unit})",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hovermode="closest",
    )
    return fig


def render_dashboard_tab(db_path):
    df = load_observations(str(db_path))

    with st.sidebar:
        st.header("Filters")
        site_names = sorted(df["site_name"].unique())
        site_name = st.selectbox("Site", site_names)
        metric = st.radio("Metric", [METRIC_SOLAR, METRIC_WIND], format_func=lambda m: METRIC_LABELS[m])

        min_ts, max_ts = df["timestamp"].min(), df["timestamp"].max()
        date_range = st.date_input(
            "Date range", value=(min_ts.date(), max_ts.date()), min_value=min_ts.date(), max_value=max_ts.date()
        )

    if len(date_range) != 2:
        st.info("Select a start and end date.")
        st.stop()
    start_date, end_date = date_range

    sub = df[
        (df["site_name"] == site_name)
        & (df["metric"] == metric)
        & (df["timestamp"].dt.date >= start_date)
        & (df["timestamp"].dt.date <= end_date)
    ].sort_values("timestamp")

    if sub.empty:
        st.warning("No data in this range.")
        st.stop()

    unit = sub["unit"].iloc[0]
    metric_label = METRIC_LABELS[metric]

    n_total = len(sub)
    n_tier1 = int((sub["anomaly_tier"] == 1).sum())
    n_tier2 = int((sub["anomaly_tier"] == 2).sum())
    n_missing = int((sub["quality_flag"] == "missing").sum())
    n_interp = int((sub["quality_flag"] == "interpolated").sum())

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Observations", n_total)
    c2.metric("Tier 1 flags", n_tier1)
    c3.metric("Tier 2 flags", n_tier2)
    c4.metric("Interpolated / missing", f"{n_interp} / {n_missing}")

    st.plotly_chart(render_chart(sub, metric_label, unit), width="stretch")

    flagged = sub[sub["is_anomaly"]][["timestamp", "value", "anomaly_tier", "anomaly_reason"]]
    with st.expander(f"Anomaly detail ({len(flagged)} flagged rows in range)", expanded=not flagged.empty):
        if flagged.empty:
            st.caption("No anomalies flagged in this range.")
        else:
            st.dataframe(flagged, width="stretch", hide_index=True)


def _resolve_preset_directly(preset: dict, con: duckdb.DuckDBPyConnection) -> dict:
    """Call a preset's fixed tool/params with no model involved -- the keyless
    path, and also the fallback when a live call fails mid-session (e.g. the
    account runs out of credit)."""
    result = tools.TOOL_DISPATCH[preset["tool"]](con, **preset["params"])
    return {
        "answer": None, "tool_name": preset["tool"], "tool_input": preset["params"],
        "tool_result": result, "raw_model_text": None, "error": None, "fallback_reason": None,
    }


def handle_question(question: str, con: duckdb.DuckDBPyConnection, preset: dict | None) -> dict:
    """Route a question through the real agent loop if a key is configured,
    otherwise resolve a preset directly against its fixed (tool, params) --
    no NL parsing without a model (part2-handoff.md section 5.5, keyless
    fallback).

    A key being *present* doesn't mean it still works -- credits can run out
    mid-session. If the live call fails on a preset question, fall back to
    the same direct-tool path used when there's no key at all, so preset
    buttons keep working regardless of what happens to the account."""
    if agent.has_api_key():
        result = agent.ask(question, con)
        if result["error"] is None:
            result["fallback_reason"] = None
            return result
        if preset is not None:
            fallback = _resolve_preset_directly(preset, con)
            fallback["fallback_reason"] = result["error"]
            return fallback
        result["fallback_reason"] = None
        return result
    if preset is None:
        return {
            "answer": None, "tool_name": None, "tool_input": None, "tool_result": None,
            "raw_model_text": None, "fallback_reason": None,
            "error": "No API key configured -- free text needs a model to parse it. Try a preset button.",
        }
    return _resolve_preset_directly(preset, con)


def render_answer(entry: dict) -> None:
    with st.chat_message("assistant"):
        if entry["error"]:
            st.error(entry["error"])
            return
        if entry["answer"]:
            st.write(entry["answer"])
        else:
            if entry.get("fallback_reason"):
                st.caption(
                    f"Live model call failed ({entry['fallback_reason']}) -- fell back to "
                    "calling the tool directly:"
                )
            else:
                st.caption("No model configured -- showing the tool's raw structured result:")
            st.json(entry["tool_result"])
        if entry["tool_name"]:
            with st.expander(f"Resolved tool call: `{entry['tool_name']}`"):
                st.json({"tool": entry["tool_name"], "params": entry["tool_input"]})
                if entry["raw_model_text"]:
                    st.caption("Raw model output:")
                    st.code(entry["raw_model_text"])


def render_agent_tab(db_path) -> None:
    st.header("Ask the data")
    con = get_connection(str(db_path))
    has_key = agent.has_api_key()

    if not has_key:
        st.warning(
            "No `ANTHROPIC_API_KEY` found in `.env` -- running in keyless fallback mode. "
            "Preset buttons below still call the real tools directly; free text needs a key."
        )

    st.caption("Try one of these, or ask your own question below.")
    cols = st.columns(2)
    for i, preset in enumerate(tools.PRESET_QUESTIONS):
        if cols[i % 2].button(preset["question"], key=f"preset_{i}"):
            entry = {"question": preset["question"], **handle_question(preset["question"], con, preset)}
            st.session_state.setdefault("agent_history", []).append(entry)

    question = st.chat_input(
        "Ask about solar radiation or wind speed across the 3 sites...", disabled=not has_key
    )
    if not has_key:
        st.caption("Free text is disabled without an API key -- use a preset button above.")
    if question:
        entry = {"question": question, **handle_question(question, con, preset=None)}
        st.session_state.setdefault("agent_history", []).append(entry)

    for entry in st.session_state.get("agent_history", [])[::-1]:
        with st.chat_message("user"):
            st.write(entry["question"])
        render_answer(entry)


def main():
    st.title("Solar Radiation & Wind Speed -- 3-Site Dashboard")

    with st.sidebar:
        st.header("Data source")
        use_demo = False
        if DEMO_DB_PATH.exists():
            use_demo = st.checkbox(
                "Use fault-injected demo data",
                value=False,
                help="Loads data/demo_faulty.duckdb -- synthetic, deliberately corrupted rows "
                "for demonstrating the Tier-1 detector and the interpolation path, which real "
                "Open-Meteo data doesn't exercise. See src/inject_faults.py.",
            )
    db_path = DEMO_DB_PATH if use_demo else DB_PATH

    if use_demo:
        st.error(
            "SYNTHETIC DATA -- this is `demo_faulty.duckdb`, deliberately corrupted by "
            "`src/inject_faults.py` to demonstrate the Tier-1 detector. It is not real "
            "Open-Meteo data. Untick the box in the sidebar to see the real dataset."
        )

    if not db_path.exists():
        if use_demo:
            st.error(f"No demo database found at `{db_path}`. Run `python src/inject_faults.py` first.")
        else:
            st.error(f"No database found at `{db_path}`. Run `python src/pipeline.py` first.")
        st.stop()

    dashboard_tab, agent_tab = st.tabs(["Dashboard", "AI Assistant"])
    with dashboard_tab:
        render_dashboard_tab(db_path)
    with agent_tab:
        render_agent_tab(db_path)


if __name__ == "__main__":
    main()
