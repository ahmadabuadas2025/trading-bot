"""Open and closed positions tables."""

from __future__ import annotations

import sqlite3

import streamlit as st

from dashboard.db import df


def render_open(conn: sqlite3.Connection) -> None:
    """Render the open-positions table."""
    st.dataframe(
        df(
            conn,
            "SELECT id, bucket_name, coin_symbol, entry_price, size_usd, "
            "stop_loss_pct, take_profit_pct, opened_at FROM positions "
            "WHERE status = 'OPEN' ORDER BY opened_at DESC",
        ),
        use_container_width=True,
    )


def render_closed(conn: sqlite3.Connection) -> None:
    """Render the recent closed-positions table (last 50)."""
    closed = df(
        conn,
        "SELECT id, bucket_name, coin_symbol, entry_price, exit_price, "
        "pnl_usd, pnl_pct, close_reason, closed_at FROM positions "
        "WHERE status = 'CLOSED' ORDER BY closed_at DESC LIMIT 50",
    )
    if not closed.empty:
        def _colour_pnl(val):
            if isinstance(val, (int, float)):
                return "color: #1D9E75" if val >= 0 else "color: #E24B4A"
            return ""

        st.dataframe(
            closed.style.map(_colour_pnl, subset=["pnl_usd", "pnl_pct"]),
            use_container_width=True,
        )
    else:
        st.info("No closed trades yet.")
