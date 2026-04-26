"""Data scanning activity dashboard page."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

DEFAULT_DB = "data/bot.db"
LOG_PATH = "logs/bot.log"


def render(db_path: str = DEFAULT_DB) -> None:
    """Render the data scanning activity page."""
    st.header("Data Scanner Activity")

    # --- Section 1: API Status ------------------------------------------------
    st.subheader("API Connection Status")

    apis = {
        "WALLET_PRIVATE_KEY": {
            "set": bool(os.getenv("WALLET_PRIVATE_KEY", "")),
            "required_for": "Live Mode",
            "used_by": "Jupiter Executor",
        },
        "HELIUS_API_KEY": {
            "set": bool(os.getenv("HELIUS_API_KEY", "")),
            "required_for": "Token Data, Wallet Tracking",
            "used_by": "SolanaDataFeed, WalletTracker",
        },
        "BIRDEYE_API_KEY": {
            "set": bool(os.getenv("BIRDEYE_API_KEY", "")),
            "required_for": "Liquidity Data",
            "used_by": "LiquidityTracker",
        },
        "LUNARCRUSH_API_KEY": {
            "set": bool(os.getenv("LUNARCRUSH_API_KEY", "")),
            "required_for": "Sentiment (Optional)",
            "used_by": "SentimentScanner",
        },
    }

    cols = st.columns(4)
    for i, (key, info) in enumerate(apis.items()):
        with cols[i]:
            status = "Configured" if info["set"] else "Not Set"
            st.metric(key, status)
            st.caption(f"Used by: {info['used_by']}")

    # --- Section 2: Data Sources ----------------------------------------------
    st.subheader("Active Data Sources")
    st.markdown(
        """
| Module | API Endpoint | Purpose | Requires Key |
|---|---|---|---|
| JupiterClient | `quote-api.jup.ag/v6/quote` | Swap quotes & routing | No |
| JupiterClient | `api.jup.ag/price/v2` | Token prices | No |
| SolanaDataFeed | Solana RPC / Helius RPC | Token info, holders, age | HELIUS_API_KEY (optional) |
| WalletTracker | Helius API / Solana RPC | Smart money tx tracking | HELIUS_API_KEY (optional) |
| LiquidityTracker | Birdeye API | Token liquidity | BIRDEYE_API_KEY (**required**) |
| SentimentScanner | LunarCrush API | Sentiment scores | LUNARCRUSH_API_KEY (optional) |
"""
    )

    # --- Section 3: Strategy Data Flow Status ---------------------------------
    st.subheader("Strategy Data Flow")

    cfg = None
    try:
        from core.config import ConfigManager

        cfg = ConfigManager().load()

        flow_data = []

        wallet_count = len(cfg.copy_trading.tracked_wallets)
        flow_data.append(
            {
                "Strategy": "Copy Trading",
                "Data Source": "WalletTracker -> Helius/RPC",
                "Input Status": (
                    f"{wallet_count} wallets configured"
                    if wallet_count > 0
                    else "No wallets configured"
                ),
                "Issue": (
                    ""
                    if wallet_count > 0
                    else "Add wallet addresses to config.yaml -> copy_trading.tracked_wallets"
                ),
            }
        )

        flow_data.append(
            {
                "Strategy": "Hot Trading",
                "Data Source": "VolumeFeedWorker -> DexScreener/Birdeye",
                "Input Status": "Active (data/volume_feed.py)",
                "Issue": "",
            }
        )

        flow_data.append(
            {
                "Strategy": "Gem Detector",
                "Data Source": "TokenDiscoveryWorker -> DexScreener/Birdeye",
                "Input Status": "Active (data/token_discovery.py)",
                "Issue": "",
            }
        )

        flow_data.append(
            {
                "Strategy": "Arbitrage",
                "Data Source": "Jupiter Route Scanner",
                "Input Status": "Self-contained (scans Jupiter routes directly)",
                "Issue": (
                    "Only scanning SOL<>USDC — very liquid pair,"
                    " arb opportunities rare"
                ),
            }
        )

        st.dataframe(
            pd.DataFrame(flow_data), use_container_width=True, hide_index=True
        )

    except Exception as e:
        st.error(f"Could not load config: {e}")

    # --- Section 4: Recent Logs -----------------------------------------------
    st.subheader("Recent Bot Logs")
    log_file = Path(LOG_PATH)
    if log_file.exists():
        try:
            lines = log_file.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()
            recent = lines[-100:] if len(lines) > 100 else lines

            log_filter = st.selectbox(
                "Filter logs by",
                [
                    "All",
                    "jupiter",
                    "arbitrage",
                    "executor",
                    "wallet",
                    "liquidity",
                    "solana_data",
                    "token_discovery",
                    "volume_feed",
                    "ERROR",
                    "WARNING",
                ],
            )

            if log_filter != "All":
                recent = [
                    line
                    for line in recent
                    if log_filter.lower() in line.lower()
                ]

            st.text_area("Log Output", "\n".join(recent), height=400)
        except Exception as e:
            st.error(f"Could not read log file: {e}")
    else:
        st.warning(f"Log file not found at {LOG_PATH}. Start the bot first.")

    # --- Section 5: Database Stats --------------------------------------------
    st.subheader("Database Statistics")
    if Path(db_path).exists():
        conn = sqlite3.connect(db_path)
        try:
            tables = [
                "trades",
                "portfolio",
                "signals",
                "arbitrage_history",
                "risk_events",
                "token_risk_scores",
            ]
            db_cols = st.columns(3)
            for i, table in enumerate(tables):
                with db_cols[i % 3]:
                    try:
                        count = conn.execute(
                            f"SELECT COUNT(*) FROM {table}"  # noqa: S608
                        ).fetchone()[0]
                        st.metric(table, f"{count} rows")
                    except Exception:
                        st.metric(table, "N/A")
        finally:
            conn.close()
    else:
        st.warning("Database not found.")

    # --- Section 6: Scan Cycle Info -------------------------------------------
    st.subheader("Scan Cycle Monitor")
    scan_interval = 2
    try:
        if cfg is not None:
            scan_interval = cfg.arbitrage.scan_interval_seconds
    except Exception:
        pass
    st.info(
        "The bot runs strategy scans every ~2 seconds and arbitrage scans"
        f" every ~{scan_interval}s. Check the logs above to see scan activity."
    )
