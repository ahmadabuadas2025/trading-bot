"""Main entry point for the Solana Trading Bot dashboard.

Composes all dashboard sub-modules into a clean tabbed layout.
"""

from __future__ import annotations

from contextlib import closing

import streamlit as st

from dashboard import activity, components, metrics, positions, sidebar
from dashboard.db import open_ro


def main() -> None:
    """Render the full dashboard."""
    st.set_page_config(
        page_title="Trading Bot",
        page_icon=":chart_with_upwards_trend:",
        layout="wide",
    )

    st.title("Solana Trading Bot")
    st.caption("Live view of trading state. Sidebar controls write directly to SQLite.")

    if st.button("Refresh"):
        st.rerun()

    conn = open_ro()
    if conn is None:
        st.warning("Database not found at data/bot.db. Start the bot first.")
        return

    with closing(conn):
        sidebar.render(conn)
        components.render_safety_banner(conn)

        st.divider()

        tab_trading, tab_activity, tab_logs = st.tabs(
            ["Trading", "Bot Activity", "Raw Logs"],
        )

        with tab_trading:
            metrics.render_kpis(conn)
            st.divider()

            col_left, col_right = st.columns([2, 1])
            with col_left:
                metrics.render_equity_curve(conn)
            with col_right:
                metrics.render_pnl_by_bucket(conn)

            st.divider()
            components.render_wallet_info(conn)

            st.divider()
            components.render_buckets(conn)

            st.divider()
            tab_open, tab_closed, tab_llm, tab_bl = st.tabs(
                ["Open Positions", "Closed Positions", "LLM Verdicts", "Blacklist"],
            )
            with tab_open:
                positions.render_open(conn)
            with tab_closed:
                positions.render_closed(conn)
            with tab_llm:
                components.render_llm_verdicts(conn)
            with tab_bl:
                components.render_blacklist(conn)

        with tab_activity:
            activity.render_activity_log(conn)
            st.divider()
            activity.render_scan_activity(conn)

        with tab_logs:
            activity.render_log_tail()
