"""Bot activity log, scan results, and raw log viewer."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import streamlit as st

from dashboard.db import df


def render_activity_log(conn: sqlite3.Connection) -> None:
    """Structured event log from the ``events`` table."""
    st.subheader("Bot Activity Log")

    col1, col2, col3 = st.columns(3)
    with col1:
        level_filter = st.selectbox(
            "Level",
            ["ALL", "INFO", "WARNING", "ERROR"],
            key="log_level",
        )
    with col2:
        component_filter = st.selectbox(
            "Component",
            ["ALL", "orchestrator", "HOT_TRADER", "COPY_TRADER", "GEM_HUNTER", "ARBITRAGE"],
            key="log_component",
        )
    with col3:
        limit = st.slider("Show last N events", 10, 500, 100, key="log_limit")

    where_clauses: list[str] = []
    params: list[str | int] = []
    if level_filter != "ALL":
        where_clauses.append("level = ?")
        params.append(level_filter)
    if component_filter != "ALL":
        where_clauses.append("component = ?")
        params.append(component_filter)

    where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    params.append(limit)

    data = df(
        conn,
        f"SELECT created_at, component, level, message "
        f"FROM events {where} ORDER BY created_at DESC LIMIT ?",
        tuple(params),
    )
    if data.empty:
        st.info("No events recorded yet. Start the bot to see activity.")
    else:
        st.dataframe(data, use_container_width=True)


def render_scan_activity(conn: sqlite3.Connection) -> None:
    """Recent scoring results from the ``scores`` table."""
    st.subheader("Recent Scan Results")
    data = df(
        conn,
        "SELECT created_at, coin_symbol, coin_address, bucket_name, profile, "
        "final_score, threshold, passed "
        "FROM scores ORDER BY created_at DESC LIMIT 50",
    )
    if data.empty:
        st.info("No scan results yet.")
    else:
        st.dataframe(data, use_container_width=True)


def render_log_tail() -> None:
    """Show the last 100 lines of the raw bot log file."""
    st.subheader("Raw Log (last 100 lines)")
    log_path = Path("logs/bot.log")
    if not log_path.exists():
        st.info("No log file found. Start the bot first.")
        return
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    tail = lines[-100:]
    st.code("\n".join(tail), language="log")
