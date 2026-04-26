"""Streamlit main dashboard application for SolanaJupiterBot."""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st
from streamlit_autorefresh import st_autorefresh

from core.config import ConfigManager
from dashboard.pages import arbitrage_panel, data_scanner, portfolio, strategies, trade_logs

STATE_FILE = Path("data/dashboard_state.json")


def _load_config():
    """Load bot config once per Streamlit session."""
    if "bot_config" not in st.session_state:
        cm = ConfigManager()
        st.session_state["bot_config"] = cm.load()
    return st.session_state["bot_config"]


def _save_dashboard_state() -> None:
    """Persist sidebar control values to a JSON sidecar so the bot can pick them up."""
    state = {
        "mode": st.session_state.get("trading_mode", "paper"),
        "emergency_stop": st.session_state.get("emergency_stop", False),
        "copy_trading_enabled": st.session_state.get("copy_trading_enabled", True),
        "hot_trading_enabled": st.session_state.get("hot_trading_enabled", True),
        "gem_detector_enabled": st.session_state.get("gem_detector_enabled", True),
        "arbitrage_enabled": st.session_state.get("arbitrage_enabled", True),
        "max_risk_pct": st.session_state.get("max_risk_pct", 2.0),
        "max_drawdown_pct": st.session_state.get("max_drawdown_pct", 5.0),
        "max_open_trades": st.session_state.get("max_open_trades", 3),
        "slippage_bps": st.session_state.get("slippage_bps", 100),
        "refresh_interval": st.session_state.get("refresh_interval", 5),
    }
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def main() -> None:
    """Main Streamlit dashboard entry point."""
    st.set_page_config(
        page_title="SolanaJupiterBot Dashboard",
        page_icon="\U0001f916",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    cfg = _load_config()

    # --- Sidebar Navigation ---------------------------------------------------
    st.sidebar.title("\U0001f916 SolanaJupiterBot")
    st.sidebar.markdown("---")

    page = st.sidebar.radio(
        "Navigation",
        ["Portfolio", "Strategies", "Arbitrage", "Trade Logs", "Data Scanner"],
    )

    st.sidebar.markdown("---")

    # --- Mode Toggle ----------------------------------------------------------
    st.sidebar.markdown("### Mode")
    mode_options = ["paper", "live"]
    default_mode_idx = mode_options.index(cfg.app.mode) if cfg.app.mode in mode_options else 0
    mode = st.sidebar.selectbox(
        "Trading Mode", mode_options, index=default_mode_idx, key="trading_mode",
    )
    if st.sidebar.button("Emergency Stop", type="primary"):
        st.session_state["emergency_stop"] = True

    # --- Strategy Toggles -----------------------------------------------------
    st.sidebar.markdown("### Strategies")
    st.sidebar.checkbox("Copy Trading", value=cfg.copy_trading.enabled, key="copy_trading_enabled")
    st.sidebar.checkbox("Hot Trading", value=cfg.hot_trading.enabled, key="hot_trading_enabled")
    st.sidebar.checkbox("Gem Detector", value=cfg.gem_detector.enabled, key="gem_detector_enabled")
    st.sidebar.checkbox("Arbitrage", value=cfg.arbitrage.enabled, key="arbitrage_enabled")

    # --- Risk Parameters ------------------------------------------------------
    st.sidebar.markdown("### Risk Management")
    st.sidebar.slider(
        "Max Risk Per Trade %", 0.5, 5.0,
        cfg.risk.max_risk_per_trade_pct * 100, 0.5,
        key="max_risk_pct",
    )
    st.sidebar.slider(
        "Max Daily Drawdown %", 1.0, 10.0,
        cfg.risk.max_daily_drawdown_pct * 100, 0.5,
        key="max_drawdown_pct",
    )
    st.sidebar.number_input(
        "Max Open Trades/Strategy", 1, 10,
        cfg.risk.max_open_trades_per_strategy,
        key="max_open_trades",
    )

    # --- Execution Parameters -------------------------------------------------
    st.sidebar.markdown("### Execution")
    st.sidebar.slider(
        "Default Slippage (bps)", 50, 300,
        cfg.jupiter.default_slippage_bps, 10,
        key="slippage_bps",
    )

    # --- Dashboard Settings ---------------------------------------------------
    st.sidebar.markdown("### Dashboard")
    refresh = st.sidebar.slider(
        "Refresh Interval (s)", 2, 30,
        cfg.dashboard.refresh_interval_seconds,
        key="refresh_interval",
    )

    st.sidebar.markdown("---")
    st.sidebar.caption("SolanaJupiterBot v2.0")
    st.sidebar.caption("Jupiter DEX Aggregator")

    # Persist control values for the bot to read
    _save_dashboard_state()

    # Dynamic auto-refresh based on dashboard slider
    st_autorefresh(interval=refresh * 1000, key="auto_refresh")

    # Live-mode warning banner
    if mode == "live":
        st.warning(
            "\u26a0\ufe0f **LIVE MODE ACTIVE** \u2014 Real transactions will be executed on "
            "Solana mainnet. Ensure your wallet is funded and you understand the risks."
        )

    # Emergency-stop banner
    if st.session_state.get("emergency_stop"):
        st.error(
            "\U0001f6d1 **EMERGENCY STOP TRIGGERED** \u2014 The bot has been signalled to halt "
            "all trading activity."
        )

    # --- Page Routing ---------------------------------------------------------
    if page == "Portfolio":
        portfolio.render()
    elif page == "Strategies":
        strategies.render()
    elif page == "Arbitrage":
        arbitrage_panel.render()
    elif page == "Trade Logs":
        trade_logs.render()
    elif page == "Data Scanner":
        data_scanner.render()


if __name__ == "__main__":
    main()
