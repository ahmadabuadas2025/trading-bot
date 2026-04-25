"""Streamlit main dashboard application for SolanaJupiterBot."""

from __future__ import annotations

import streamlit as st
from streamlit_autorefresh import st_autorefresh

from dashboard.pages import arbitrage_panel, portfolio, strategies, trade_logs

REFRESH_INTERVAL_MS = 5000


def main() -> None:
    """Main Streamlit dashboard entry point."""
    st.set_page_config(
        page_title="SolanaJupiterBot Dashboard",
        page_icon="🤖",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    st_autorefresh(interval=REFRESH_INTERVAL_MS, key="auto_refresh")

    st.sidebar.title("🤖 SolanaJupiterBot")
    st.sidebar.markdown("---")

    page = st.sidebar.radio(
        "Navigation",
        ["Portfolio", "Strategies", "Arbitrage", "Trade Logs"],
    )

    st.sidebar.markdown("---")
    st.sidebar.caption("SolanaJupiterBot v2.0")
    st.sidebar.caption("Jupiter DEX Aggregator")

    if page == "Portfolio":
        portfolio.render()
    elif page == "Strategies":
        strategies.render()
    elif page == "Arbitrage":
        arbitrage_panel.render()
    elif page == "Trade Logs":
        trade_logs.render()


if __name__ == "__main__":
    main()
