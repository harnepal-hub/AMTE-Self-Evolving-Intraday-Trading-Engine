"""AMTE research dashboard. Run with: streamlit run dashboard.py"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

st.set_page_config(page_title="AMTE Research Dashboard", layout="wide")
st.title("AMTE — Intraday Trading Research")
st.caption("Research and validation dashboard. This interface does not place live orders.")

root = Path(__file__).resolve().parent
stage3 = root / "research_artifacts" / "stage3"
stage45 = root / "research_artifacts" / "stage4_5"

final_path = stage45 / "FINAL_VALIDATION.json"
report3_path = stage3 / "stage3_report.json"

if final_path.exists():
    final = json.loads(final_path.read_text(encoding="utf-8"))
    selection = final.get("selection", {})
    stage4 = final.get("stage4", {})
    stage5 = final.get("stage5_holdout", {})
    base = stage5.get("base", {})

    st.subheader("Validation status")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Selected strategy", selection.get("winner", "—"))
    c2.metric("Holdout net P&L", f"₹{base.get('net_pnl', 0):,.2f}")
    c3.metric("Holdout profit factor", f"{base.get('profit_factor', 0):.2f}")
    c4.metric("Decision", stage5.get("decision", "—"))

    st.subheader("Stage 4 — Walk-forward")
    wf_path = stage45 / "stage4_walk_forward.csv"
    if wf_path.exists():
        wf = pd.read_csv(wf_path)
        st.metric("Profitable windows", f"{stage4.get('profitable_windows', 0)}/{stage4.get('windows', 0)}")
        st.line_chart(wf.set_index("window")["net_pnl"])
        st.dataframe(wf, use_container_width=True)

    st.subheader("Stage 5 — Cost / slippage stress")
    stress_path = stage45 / "stage5_cost_stress.csv"
    if stress_path.exists():
        stress = pd.read_csv(stress_path)
        st.bar_chart(stress.set_index("scenario")["net_pnl"])
        st.dataframe(stress, use_container_width=True)

    mc_path = stage45 / "stage5_monte_carlo.json"
    if mc_path.exists():
        mc = json.loads(mc_path.read_text(encoding="utf-8"))
        st.subheader("Monte Carlo trade-order robustness")
        st.json(mc)
else:
    st.warning("No Stage 4-5 validation artifact is available locally yet.")

if report3_path.exists():
    report = json.loads(report3_path.read_text(encoding="utf-8"))
    st.subheader("Stage 3 — Strategy leaderboard")
    leaderboard = pd.DataFrame(report.get("development_leaderboard", []))
    if not leaderboard.empty:
        st.dataframe(leaderboard, use_container_width=True)
    st.caption(
        f"Dataset: {report.get('dataset', {}).get('pair', '—')} / {report.get('dataset', {}).get('interval', '—')} | "
        f"Rows: {report.get('dataset', {}).get('rows', 0):,}"
    )
else:
    st.info("Stage 3 artifacts are not present locally. Run the Stage 3 GitHub Actions research job first.")

st.divider()
st.caption("GO/NO-GO is determined by the validation artifact. A positive backtest is not a guarantee of future profit; live trading remains separately gated.")
