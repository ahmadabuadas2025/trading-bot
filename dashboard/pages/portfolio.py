"""Portfolio balance, PnL, and win/loss dashboard page."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

from dashboard.components.charts import equity_curve, win_rate_pie
from dashboard.components.metrics import metric_row

DEFAULT_DB = "data/bot.db"


def render(db_path: str = DEFAULT_DB) -> None:
    """Render the portfolio dashboard page."""
    st.header("Portfolio Overview")

    if not Path(db_path).exists():
        st.warning("Database not found. Start the bot to generate data.")
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    portfolio_rows = conn.execute(
        "SELECT * FROM portfolio ORDER BY updated_at DESC LIMIT 100"
    ).fetchall()

    if not portfolio_rows:
        st.info("No portfolio data yet. The bot will populate this once running.")
        conn.close()
        return

    latest = dict(portfolio_rows[0])

    balance = latest.get("balance_usd", 0)
    equity = latest.get("equity_usd", 0)
    daily_pnl = latest.get("daily_pnl", 0)
    total_pnl = latest.get("total_pnl", 0)
    wins = latest.get("win_count", 0)
    losses = latest.get("loss_count", 0)
    open_pos = latest.get("open_positions", 0)

    metric_row([
        {"label": "Balance", "value": f"${balance:,.2f}"},
        {"label": "Equity", "value": f"${equity:,.2f}"},
        {"label": "Daily PnL", "value": f"${daily_pnl:,.2f}", "delta": f"${daily_pnl:,.2f}"},
        {"label": "Total PnL", "value": f"${total_pnl:,.2f}", "delta": f"${total_pnl:,.2f}"},
    ])

    col1, col2, col3 = st.columns(3)
    with col1:
        total_trades = wins + losses
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        st.metric("Win Rate", f"{win_rate:.1f}%")
    with col2:
        st.metric("Open Positions", str(open_pos))
    with col3:
        st.metric("Total Trades", str(wins + losses))

    st.subheader("Equity Curve")
    timestamps = [dict(r)["updated_at"] for r in reversed(portfolio_rows)]
    equities = [dict(r)["equity_usd"] for r in reversed(portfolio_rows)]
    st.plotly_chart(equity_curve(timestamps, equities), width="stretch")

    st.subheader("Win/Loss Distribution")
    if wins + losses > 0:
        st.plotly_chart(win_rate_pie(wins, losses), width="stretch")
    else:
        st.info("No completed trades yet.")

    trades_df = pd.read_sql_query(
        "SELECT * FROM trades ORDER BY timestamp DESC LIMIT 20", conn
    )
    if not trades_df.empty:
        st.subheader("Recent Trades")
        st.dataframe(trades_df, width="stretch")

    conn.close()
