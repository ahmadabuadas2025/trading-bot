"""Strategy monitoring panels dashboard page."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

from dashboard.components.charts import pnl_bar_chart
from dashboard.components.metrics import metric_row, status_indicator

DEFAULT_DB = "data/bot.db"

STRATEGIES = ["copy_trading", "hot_trading", "gem_detector"]


def render(db_path: str = DEFAULT_DB) -> None:
    """Render the strategies monitoring page."""
    st.header("Strategy Monitor")

    if not Path(db_path).exists():
        st.warning("Database not found. Start the bot to generate data.")
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    strategy_pnl: dict[str, float] = {}
    strategy_wins: dict[str, int] = {}
    strategy_losses: dict[str, int] = {}
    strategy_count: dict[str, int] = {}

    for strategy in STRATEGIES:
        rows = conn.execute(
            "SELECT pnl, status FROM trades WHERE strategy = ?", (strategy,)
        ).fetchall()

        total_pnl = 0.0
        wins = 0
        losses = 0
        for row in rows:
            r = dict(row)
            pnl = r.get("pnl", 0)
            total_pnl += pnl
            if pnl >= 0:
                wins += 1
            else:
                losses += 1

        strategy_pnl[strategy] = total_pnl
        strategy_wins[strategy] = wins
        strategy_losses[strategy] = losses
        strategy_count[strategy] = len(rows)

    st.subheader("PnL by Strategy")
    labels = [s.replace("_", " ").title() for s in STRATEGIES]
    values = [strategy_pnl.get(s, 0) for s in STRATEGIES]
    st.plotly_chart(pnl_bar_chart(labels, values), use_container_width=True)

    for strategy in STRATEGIES:
        display_name = strategy.replace("_", " ").title()
        with st.expander(f"📊 {display_name}", expanded=True):
            wins = strategy_wins.get(strategy, 0)
            losses = strategy_losses.get(strategy, 0)
            total = wins + losses
            win_rate = (wins / total * 100) if total > 0 else 0

            status_indicator(display_name, total > 0)
            metric_row([
                {"label": "Total PnL", "value": f"${strategy_pnl.get(strategy, 0):,.2f}"},
                {"label": "Win Rate", "value": f"{win_rate:.1f}%"},
                {"label": "Total Trades", "value": str(total)},
                {"label": "Wins / Losses", "value": f"{wins} / {losses}"},
            ])

            trades_df = pd.read_sql_query(
                "SELECT token_address, side, amount_usd, price, pnl, status, timestamp "
                "FROM trades WHERE strategy = ? ORDER BY timestamp DESC LIMIT 10",
                conn,
                params=(strategy,),
            )
            if not trades_df.empty:
                st.dataframe(trades_df, use_container_width=True)
            else:
                st.info(f"No trades recorded for {display_name} yet.")

    conn.close()
