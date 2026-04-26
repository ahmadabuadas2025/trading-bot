"""Arbitrage opportunities and history dashboard page."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

from dashboard.components.charts import cumulative_profit_chart
from dashboard.components.metrics import metric_row

DEFAULT_DB = "data/bot.db"


def render(db_path: str = DEFAULT_DB) -> None:
    """Render the arbitrage monitoring page."""
    st.header("Arbitrage Engine")

    if not Path(db_path).exists():
        st.warning("Database not found. Start the bot to generate data.")
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        "SELECT * FROM arbitrage_history ORDER BY timestamp DESC LIMIT 200"
    ).fetchall()

    if not rows:
        st.info("No arbitrage history yet. The engine will populate this once running.")
        conn.close()
        return

    records = [dict(r) for r in rows]

    total_profit = sum(r.get("actual_profit_usd", 0) or 0 for r in records)
    executed = sum(1 for r in records if r.get("status") == "executed")
    failed = sum(1 for r in records if r.get("status") == "failed")
    total = len(records)
    success_rate = (executed / total * 100) if total > 0 else 0

    metric_row([
        {"label": "Total Arb Profit", "value": f"${total_profit:,.4f}", "delta": f"${total_profit:,.4f}"},
        {"label": "Executed", "value": str(executed)},
        {"label": "Failed", "value": str(failed)},
        {"label": "Success Rate", "value": f"{success_rate:.1f}%"},
    ])

    st.subheader("Cumulative Arbitrage Profit")
    timestamps = [r.get("timestamp", "") for r in reversed(records)]
    profits = [r.get("actual_profit_usd", 0) or 0 for r in reversed(records)]
    st.plotly_chart(
        cumulative_profit_chart(timestamps, profits, "Cumulative Arb Profit"),
        width="stretch",
    )

    st.subheader("Recent Opportunities")
    df = pd.DataFrame(records[:50])
    display_cols = [
        "id", "input_mint", "output_mint", "expected_profit_pct",
        "expected_profit_usd", "actual_profit_usd", "status", "timestamp",
    ]
    available_cols = [c for c in display_cols if c in df.columns]
    st.dataframe(df[available_cols], width="stretch")

    conn.close()
