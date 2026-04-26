"""Trade logs viewer dashboard page with filtering and export."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

DEFAULT_DB = "data/bot.db"


def render(db_path: str = DEFAULT_DB) -> None:
    """Render the trade logs page with filtering and CSV export."""
    st.header("Trade Logs")

    if not Path(db_path).exists():
        st.warning("Database not found. Start the bot to generate data.")
        return

    conn = sqlite3.connect(db_path)

    df = pd.read_sql_query("SELECT * FROM trades ORDER BY timestamp DESC", conn)

    if df.empty:
        st.info("No trade logs yet.")
        conn.close()
        return

    col1, col2, col3 = st.columns(3)

    with col1:
        strategies = ["All"] + sorted(df["strategy"].unique().tolist())
        selected_strategy = st.selectbox("Strategy", strategies)

    with col2:
        if "timestamp" in df.columns and not df["timestamp"].isna().all():
            min_date = pd.to_datetime(df["timestamp"]).min().date()
            max_date = pd.to_datetime(df["timestamp"]).max().date()
            date_range = st.date_input(
                "Date Range",
                value=(min_date, max_date),
                min_value=min_date,
                max_value=max_date,
            )
        else:
            date_range = None

    with col3:
        token_filter = st.text_input("Token Address (contains)", "")

    filtered = df.copy()

    if selected_strategy != "All":
        filtered = filtered[filtered["strategy"] == selected_strategy]

    if date_range and len(date_range) == 2:  # type: ignore[arg-type]
        filtered["timestamp_dt"] = pd.to_datetime(filtered["timestamp"])
        start, end = date_range  # type: ignore[misc]
        filtered = filtered[
            (filtered["timestamp_dt"].dt.date >= start)
            & (filtered["timestamp_dt"].dt.date <= end)
        ]
        filtered = filtered.drop(columns=["timestamp_dt"])

    if token_filter:
        filtered = filtered[
            filtered["token_address"].str.contains(token_filter, case=False, na=False)
        ]

    st.subheader(f"Showing {len(filtered)} trades")
    st.dataframe(filtered, use_container_width=True, height=500)

    st.subheader("Summary")
    summary_col1, summary_col2, summary_col3, summary_col4 = st.columns(4)
    with summary_col1:
        st.metric("Total Trades", str(len(filtered)))
    with summary_col2:
        total_pnl = filtered["pnl"].sum() if "pnl" in filtered.columns else 0
        st.metric("Total PnL", f"${total_pnl:,.2f}")
    with summary_col3:
        wins = len(filtered[filtered["pnl"] > 0]) if "pnl" in filtered.columns else 0
        st.metric("Winning Trades", str(wins))
    with summary_col4:
        avg_pnl = filtered["pnl"].mean() if "pnl" in filtered.columns and len(filtered) > 0 else 0
        st.metric("Avg PnL", f"${avg_pnl:,.2f}")

    st.subheader("Export")
    csv = filtered.to_csv(index=False)
    st.download_button(
        label="Download CSV",
        data=csv,
        file_name="trade_logs.csv",
        mime="text/csv",
    )

    conn.close()
