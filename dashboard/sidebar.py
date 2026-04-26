"""Sidebar controls for the Streamlit dashboard.

Provides bucket allocations, risk parameters, trading mode,
scan interval, emergency stop, and blacklist management.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import streamlit as st

from dashboard.db import df, open_rw, scalar


def render(conn_ro: sqlite3.Connection) -> None:
    """Render full sidebar and flush any changes to the DB."""

    st.sidebar.title("Bot controls")
    st.sidebar.caption("Changes are written immediately to SQLite.")

    # -- 1. emergency stop ------------------------------------------------
    st.sidebar.subheader("Emergency stop")
    safety = df(conn_ro, "SELECT * FROM safety_state WHERE id = 1")
    current_stop = bool(int(safety.iloc[0]["emergency_stop"])) if not safety.empty else False

    new_stop = st.sidebar.toggle(
        "Activate emergency stop",
        value=current_stop,
        help="Immediately halts all new trades and cancels pending orders.",
    )
    if new_stop != current_stop:
        reason = st.sidebar.text_input(
            "Stop reason (required to activate)",
            placeholder="e.g. Manual override — high volatility",
        )
        if st.sidebar.button("Confirm emergency stop change", type="primary"):
            with open_rw() as rw:
                rw.execute(
                    "UPDATE safety_state SET emergency_stop = ?, stop_reason = ? WHERE id = 1",
                    (int(new_stop), reason if new_stop else None),
                )
                rw.commit()
            st.sidebar.success("Saved.")
            st.rerun()

    st.sidebar.divider()

    # -- 2. bucket allocations --------------------------------------------
    st.sidebar.subheader("Budget allocation")
    buckets_df = df(
        conn_ro,
        "SELECT bucket_name, allocation_pct, balance, enabled FROM fund_buckets ORDER BY bucket_name",
    )

    if buckets_df.empty:
        st.sidebar.info("No buckets found.")
    else:
        new_allocs: dict[str, float] = {}
        new_enabled: dict[str, bool] = {}

        for _, row in buckets_df.iterrows():
            name = row["bucket_name"]
            col1, col2 = st.sidebar.columns([3, 1])
            with col1:
                new_allocs[name] = st.slider(
                    name,
                    min_value=0,
                    max_value=100,
                    value=int(row["allocation_pct"]),
                    step=1,
                    key=f"alloc_{name}",
                )
            with col2:
                new_enabled[name] = st.checkbox(
                    "On",
                    value=bool(int(row["enabled"])),
                    key=f"ena_{name}",
                )

        total_alloc = sum(new_allocs.values())
        st.sidebar.metric(
            "Total allocation",
            f"{total_alloc}%",
            delta=f"{total_alloc - 100:+d}% vs 100%",
        )
        if total_alloc != 100:
            st.sidebar.warning("Allocations must sum to 100 % before saving.")

        if st.sidebar.button("Save bucket settings", disabled=(total_alloc != 100)):
            with open_rw() as rw:
                for name, pct in new_allocs.items():
                    rw.execute(
                        "UPDATE fund_buckets SET allocation_pct = ?, enabled = ? WHERE bucket_name = ?",
                        (pct, int(new_enabled[name]), name),
                    )
                rw.commit()
            st.sidebar.success("Bucket settings saved.")
            st.rerun()

    st.sidebar.divider()

    # -- 3. risk parameters -----------------------------------------------
    st.sidebar.subheader("Risk parameters")

    sl_default = float(safety.iloc[0].get("default_stop_loss_pct", 5.0)) if not safety.empty else 5.0
    tp_default = float(safety.iloc[0].get("default_take_profit_pct", 15.0)) if not safety.empty else 15.0
    max_pos_default = float(safety.iloc[0].get("max_position_usd", 500.0)) if not safety.empty else 500.0
    daily_loss_limit = float(safety.iloc[0].get("daily_loss_limit_pct", 10.0)) if not safety.empty else 10.0
    max_open = int(safety.iloc[0].get("max_open_positions", 10)) if not safety.empty else 10

    new_sl = st.sidebar.slider("Stop-loss %", 1, 30, int(sl_default), 1, key="sl")
    new_tp = st.sidebar.slider("Take-profit %", 5, 100, int(tp_default), 1, key="tp")
    new_max_pos = st.sidebar.number_input(
        "Max position size (USD)",
        min_value=10.0,
        max_value=10_000.0,
        value=max_pos_default,
        step=10.0,
        key="max_pos",
    )
    new_daily_loss = st.sidebar.slider(
        "Daily loss circuit-breaker %",
        1,
        50,
        int(daily_loss_limit),
        1,
        help="Bot halts automatically when daily loss exceeds this %.",
        key="daily_loss",
    )
    new_max_open = st.sidebar.slider("Max open positions", 1, 50, max_open, 1, key="max_open")

    if st.sidebar.button("Save risk parameters"):
        with open_rw() as rw:
            rw.execute(
                """UPDATE safety_state SET
                    default_stop_loss_pct = ?,
                    default_take_profit_pct = ?,
                    max_position_usd = ?,
                    daily_loss_limit_pct = ?,
                    max_open_positions = ?
                WHERE id = 1""",
                (new_sl, new_tp, new_max_pos, new_daily_loss, new_max_open),
            )
            rw.commit()
        st.sidebar.success("Risk parameters saved.")
        st.rerun()

    st.sidebar.divider()

    # -- 4. trading mode --------------------------------------------------
    st.sidebar.subheader("Trading mode")
    current_mode = scalar(conn_ro, "SELECT trading_mode FROM safety_state WHERE id = 1", default="paper")

    new_mode = st.sidebar.radio(
        "Mode",
        options=["paper", "live"],
        index=0 if current_mode == "paper" else 1,
        horizontal=True,
        help="'paper' = simulated trades only. 'live' = real money.",
        key="trading_mode",
    )
    if new_mode != current_mode:
        if new_mode == "live":
            st.sidebar.warning("Switching to LIVE mode will use real funds.")
        if st.sidebar.button("Confirm mode switch", type="primary"):
            with open_rw() as rw:
                rw.execute("UPDATE safety_state SET trading_mode = ? WHERE id = 1", (new_mode,))
                rw.commit()
            st.sidebar.success(f"Mode set to {new_mode}.")
            st.rerun()

    st.sidebar.divider()

    # -- 5. scan interval -------------------------------------------------
    st.sidebar.subheader("Scan interval")
    current_interval = int(
        scalar(conn_ro, "SELECT scan_interval_seconds FROM safety_state WHERE id = 1", default=60)
    )

    new_interval = st.sidebar.slider(
        "Seconds between LLM scans",
        10,
        300,
        current_interval,
        10,
        help="Lower = more API calls. Higher = slower reaction.",
        key="scan_interval",
    )
    if new_interval != current_interval:
        if st.sidebar.button("Save interval"):
            with open_rw() as rw:
                rw.execute(
                    "UPDATE safety_state SET scan_interval_seconds = ? WHERE id = 1",
                    (new_interval,),
                )
                rw.commit()
            st.sidebar.success("Interval saved.")
            st.rerun()

    st.sidebar.divider()

    # -- 6. quick blacklist -----------------------------------------------
    st.sidebar.subheader("Add to blacklist")
    bl_symbol = st.sidebar.text_input("Coin symbol (e.g. BONK)", key="bl_sym").upper().strip()
    bl_address = st.sidebar.text_input("Coin address (optional)", key="bl_addr").strip()
    bl_reason = st.sidebar.text_input("Reason", key="bl_reason").strip()
    bl_permanent = st.sidebar.checkbox("Permanent ban", value=False, key="bl_perm")
    bl_hours = st.sidebar.number_input(
        "Temporary ban duration (hours)",
        min_value=1,
        max_value=720,
        value=24,
        step=1,
        key="bl_hours",
        disabled=bl_permanent,
    )

    if st.sidebar.button("Add to blacklist", disabled=(not bl_symbol or not bl_reason)):
        expires = None if bl_permanent else (
            datetime.utcnow() + timedelta(hours=int(bl_hours))
        ).strftime("%Y-%m-%d %H:%M:%S")
        with open_rw() as rw:
            rw.execute(
                """INSERT OR REPLACE INTO blacklist
                   (coin_symbol, coin_address, reason, source, permanent, expires_at, blacklisted_at)
                   VALUES (?, ?, ?, 'dashboard', ?, ?, CURRENT_TIMESTAMP)""",
                (bl_symbol, bl_address or None, bl_reason, int(bl_permanent), expires),
            )
            rw.commit()
        st.sidebar.success(f"{bl_symbol} blacklisted.")
        st.rerun()
