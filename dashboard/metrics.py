"""KPI cards, equity curve, and P&L-by-bucket chart."""

from __future__ import annotations

import sqlite3

import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from dashboard.db import df, scalar
from dashboard.wallet import fetch_live_sol_balance, fetch_sol_price_usd


def render_kpis(conn: sqlite3.Connection) -> None:
    """Render top KPI metric cards."""
    current_mode = scalar(
        conn, "SELECT trading_mode FROM safety_state WHERE id = 1", default="paper",
    )

    if current_mode == "live":
        sol_balance = fetch_live_sol_balance()
        sol_price = fetch_sol_price_usd() if sol_balance is not None else None
        if sol_balance is not None and sol_price is not None:
            total_balance = sol_balance * sol_price
            balance_label = f"Wallet ({sol_balance:.4f} SOL)"
        elif sol_balance is not None:
            total_balance = scalar(
                conn, "SELECT SUM(balance) FROM fund_buckets", default=0.0,
            )
            balance_label = "Portfolio (price unavailable)"
        else:
            total_balance = scalar(
                conn, "SELECT SUM(balance) FROM fund_buckets", default=0.0,
            )
            balance_label = "Portfolio (no wallet key)"
    else:
        total_balance = scalar(conn, "SELECT SUM(balance) FROM fund_buckets", default=0.0)
        balance_label = "Portfolio (Paper)"

    daily_pnl = scalar(
        conn,
        "SELECT COALESCE(SUM(pnl_usd), 0) FROM positions "
        "WHERE status = 'CLOSED' AND DATE(closed_at) = DATE('now')",
        default=0.0,
    )
    total_closed = scalar(conn, "SELECT COUNT(*) FROM positions WHERE status = 'CLOSED'", default=0)
    winning = scalar(
        conn, "SELECT COUNT(*) FROM positions WHERE status = 'CLOSED' AND pnl_usd > 0", default=0
    )
    win_rate = (winning / total_closed * 100) if total_closed else 0.0
    open_count = scalar(conn, "SELECT COUNT(*) FROM positions WHERE status = 'OPEN'", default=0)
    best_pnl = scalar(
        conn, "SELECT MAX(pnl_usd) FROM positions WHERE status = 'CLOSED'", default=0.0
    )
    worst_pnl = scalar(
        conn, "SELECT MIN(pnl_usd) FROM positions WHERE status = 'CLOSED'", default=0.0
    )
    avg_hold = scalar(
        conn,
        "SELECT AVG((JULIANDAY(closed_at) - JULIANDAY(opened_at)) * 1440) "
        "FROM positions WHERE status = 'CLOSED' AND closed_at IS NOT NULL",
        default=0.0,
    )

    cols = st.columns(7)
    metrics = [
        (balance_label, f"${total_balance:,.2f}", None),
        ("Daily P&L", f"${daily_pnl:+,.2f}", daily_pnl),
        ("Win rate", f"{win_rate:.1f}%", None),
        ("Open positions", str(open_count), None),
        ("Best trade", f"${best_pnl:+,.2f}", best_pnl),
        ("Worst trade", f"${worst_pnl:+,.2f}", worst_pnl),
        ("Avg hold", f"{avg_hold:.1f}m", None),
    ]
    for col, (label, value, delta) in zip(cols, metrics, strict=False):
        with col:
            if delta is not None:
                st.metric(label, value, delta=f"{'up' if delta >= 0 else 'down'}")
            else:
                st.metric(label, value)


def render_equity_curve(conn: sqlite3.Connection) -> None:
    """7-day equity curve built from closed positions."""
    st.subheader("Equity curve - last 7 days")
    data = df(
        conn,
        """
        SELECT DATE(closed_at) AS day, SUM(pnl_usd) AS daily_pnl
        FROM positions
        WHERE status = 'CLOSED'
          AND closed_at >= DATETIME('now', '-7 days')
        GROUP BY day ORDER BY day
        """,
    )
    if data.empty:
        st.info("No closed trades in the last 7 days.")
        return

    current_mode = scalar(
        conn, "SELECT trading_mode FROM safety_state WHERE id = 1", default="paper",
    )
    if current_mode == "live":
        sol_balance = fetch_live_sol_balance()
        sol_price = fetch_sol_price_usd() if sol_balance is not None else None
        if sol_balance is not None and sol_price is not None:
            start_balance = sol_balance * sol_price
        else:
            start_balance = scalar(conn, "SELECT SUM(balance) FROM fund_buckets", default=0.0)
    else:
        start_balance = scalar(conn, "SELECT SUM(balance) FROM fund_buckets", default=0.0)

    window_pnl_total = data["daily_pnl"].sum()
    start_balance_window = start_balance - window_pnl_total

    data["cumulative_pnl"] = data["daily_pnl"].cumsum()
    data["balance"] = start_balance_window + data["cumulative_pnl"]

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=data["day"],
            y=data["balance"].round(2),
            mode="lines+markers",
            fill="tozeroy",
            line={"color": "#378ADD", "width": 2},
            fillcolor="rgba(55,138,221,0.08)",
            name="Balance",
        )
    )
    fig.update_layout(
        margin={"l": 0, "r": 0, "t": 0, "b": 0},
        height=220,
        xaxis_title=None,
        yaxis_title="USD",
        showlegend=False,
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig, use_container_width=True)


def render_pnl_by_bucket(conn: sqlite3.Connection) -> None:
    """Bar chart of total P&L per bucket."""
    st.subheader("P&L by bucket")
    data = df(
        conn,
        "SELECT bucket_name, ROUND(SUM(pnl_usd), 2) AS total_pnl "
        "FROM positions WHERE status = 'CLOSED' GROUP BY bucket_name ORDER BY total_pnl DESC",
    )
    if data.empty:
        st.info("No closed trades yet.")
        return
    data["colour"] = data["total_pnl"].apply(lambda v: "#1D9E75" if v >= 0 else "#E24B4A")
    fig = px.bar(
        data,
        x="bucket_name",
        y="total_pnl",
        color="colour",
        color_discrete_map="identity",
        text="total_pnl",
    )
    fig.update_traces(texttemplate="$%{text:,.2f}", textposition="outside")
    fig.update_layout(
        margin={"l": 0, "r": 0, "t": 0, "b": 0},
        height=260,
        showlegend=False,
        xaxis_title=None,
        yaxis_title="USD P&L",
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig, use_container_width=True)
