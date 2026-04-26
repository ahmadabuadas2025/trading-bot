"""Miscellaneous dashboard components.

Safety banner, LLM verdicts, blacklist, and fund-buckets section.
"""

from __future__ import annotations

import sqlite3

import plotly.express as px
import streamlit as st

from dashboard.db import df


def render_safety_banner(conn: sqlite3.Connection) -> None:
    """Show a safety-status bar at the top of the page."""
    row = df(conn, "SELECT * FROM safety_state WHERE id = 1")
    if row.empty:
        st.info("No safety state found.")
        return
    state = row.iloc[0]
    mode = state.get("trading_mode", "paper")
    mode_badge = "PAPER" if mode == "paper" else "LIVE"
    if int(state.get("emergency_stop", 0)):
        st.error(
            f"EMERGENCY STOP ACTIVE — {state.get('stop_reason')} | "
            f"Daily loss: {float(state.get('daily_loss_pct') or 0):.2%} | Mode: {mode_badge}"
        )
    else:
        st.success(f"Bot running normally | Mode: {mode_badge}")


def render_llm_verdicts(conn: sqlite3.Connection) -> None:
    """Latest LLM verdicts table."""
    data = df(
        conn,
        "SELECT scan_time, coin_symbol, bucket, llm_score, verdict, confidence, "
        "social_buzz, kol_mentioned, red_flags, reason, expires_at "
        "FROM llm_scan_results ORDER BY scan_time DESC LIMIT 50",
    )
    if data.empty:
        st.info("No LLM scans yet.")
    else:
        st.dataframe(data, use_container_width=True)


def render_blacklist(conn: sqlite3.Connection) -> None:
    """Active blacklist table."""
    data = df(
        conn,
        "SELECT coin_symbol, coin_address, reason, source, permanent, expires_at, "
        "blacklisted_at FROM blacklist "
        "WHERE permanent = 1 OR expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP "
        "ORDER BY blacklisted_at DESC LIMIT 100",
    )
    if data.empty:
        st.info("Blacklist is empty.")
    else:
        st.dataframe(data, use_container_width=True)


def render_buckets(conn: sqlite3.Connection) -> None:
    """Fund-buckets overview with balance bar chart."""
    st.subheader("Fund buckets")
    buckets = df(
        conn,
        "SELECT bucket_name, allocation_pct, balance, enabled, description, updated_at "
        "FROM fund_buckets ORDER BY bucket_name",
    )
    positions = df(
        conn,
        "SELECT bucket_name, COUNT(*) AS open_positions FROM positions "
        "WHERE status = 'OPEN' GROUP BY bucket_name",
    )
    if buckets.empty:
        st.info("No fund buckets yet.")
        return
    merged = buckets.merge(positions, on="bucket_name", how="left").fillna({"open_positions": 0})
    st.dataframe(merged, use_container_width=True)

    fig = px.bar(
        merged,
        x="bucket_name",
        y="balance",
        color="bucket_name",
        text="balance",
        title="Balance per bucket",
    )
    fig.update_traces(texttemplate="$%{text:,.2f}", textposition="outside")
    fig.update_layout(
        showlegend=False,
        margin={"l": 0, "r": 0, "t": 30, "b": 0},
        height=280,
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig, use_container_width=True)
