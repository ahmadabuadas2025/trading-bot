"""Streamlit dashboard — SolanaMemBot.

Read-only metrics view + live control sidebar that writes back to
``data/bot.db``.  All reads use a read-only URI connection; all writes
use a separate read-write connection so the WAL file is never corrupted.

Sidebar controls
----------------
* Per-bucket allocation sliders (auto-normalised to 100 %)
* Per-bucket enable / disable toggles
* Global risk parameters: stop-loss %, take-profit %, max position USD,
  daily-loss circuit-breaker %, max open positions
* Emergency stop toggle
* Add-to-blacklist form

Dashboard sections
------------------
* Portfolio KPI cards  (total value, daily P&L, win rate, open positions,
  best trade, worst trade, average hold time)
* Equity curve (7-day running balance sampled from closed positions)
* P&L by bucket bar chart
* Open positions table
* Closed positions table (last 50)
* Latest LLM verdicts table
* Active blacklist table
* Safety state banner
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# ── constants ────────────────────────────────────────────────────────────────
DB_PATH: Path = Path("data/bot.db")

st.set_page_config(
    page_title="SolanaMemBot",
    page_icon=":chart_with_upwards_trend:",
    layout="wide",
)

# ── helpers ──────────────────────────────────────────────────────────────────

def _open_ro() -> sqlite3.Connection | None:
    """Return a read-only SQLite connection, or None if DB is missing."""
    if not DB_PATH.exists():
        return None
    uri = f"file:{DB_PATH.as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=5)


def _open_rw() -> sqlite3.Connection:
    """Return a read-write SQLite connection."""
    return sqlite3.connect(str(DB_PATH), timeout=10)


def _df(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> pd.DataFrame:
    with closing(conn.cursor()) as cur:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchall()
    return pd.DataFrame(rows, columns=cols)


def _scalar(conn: sqlite3.Connection, sql: str, params: tuple = (), default=None):
    with closing(conn.cursor()) as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    return row[0] if row and row[0] is not None else default


# ── sidebar ──────────────────────────────────────────────────────────────────

def _sidebar(conn_ro: sqlite3.Connection) -> None:
    """Render full sidebar and flush any changes to the DB."""

    st.sidebar.title("⚙️ Bot controls")
    st.sidebar.caption("Changes are written immediately to SQLite.")

    # ── 1. emergency stop ────────────────────────────────────────────────────
    st.sidebar.subheader("🚨 Emergency stop")
    safety = _df(conn_ro, "SELECT * FROM safety_state WHERE id = 1")
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
            with _open_rw() as rw:
                rw.execute(
                    "UPDATE safety_state SET emergency_stop = ?, stop_reason = ? WHERE id = 1",
                    (int(new_stop), reason if new_stop else None),
                )
                rw.commit()
            st.sidebar.success("Saved.")
            st.rerun()

    st.sidebar.divider()

    # ── 2. bucket allocations ────────────────────────────────────────────────
    st.sidebar.subheader("💰 Budget allocation")
    buckets_df = _df(
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
        colour = "normal" if total_alloc == 100 else "inverse"
        st.sidebar.metric("Total allocation", f"{total_alloc}%", delta=f"{total_alloc - 100:+d}% vs 100%")
        if total_alloc != 100:
            st.sidebar.warning("Allocations must sum to 100 % before saving.")

        if st.sidebar.button("💾 Save bucket settings", disabled=(total_alloc != 100)):
            with _open_rw() as rw:
                for name, pct in new_allocs.items():
                    rw.execute(
                        "UPDATE fund_buckets SET allocation_pct = ?, enabled = ? WHERE bucket_name = ?",
                        (pct, int(new_enabled[name]), name),
                    )
                rw.commit()
            st.sidebar.success("Bucket settings saved.")
            st.rerun()

    st.sidebar.divider()

    # ── 3. risk parameters ───────────────────────────────────────────────────
    st.sidebar.subheader("🛡️ Risk parameters")

    # read current values from safety_state or use defaults
    sl_default = float(safety.iloc[0].get("default_stop_loss_pct", 5.0)) if not safety.empty else 5.0
    tp_default = float(safety.iloc[0].get("default_take_profit_pct", 15.0)) if not safety.empty else 15.0
    max_pos_default = float(safety.iloc[0].get("max_position_usd", 500.0)) if not safety.empty else 500.0
    daily_loss_limit = float(safety.iloc[0].get("daily_loss_limit_pct", 10.0)) if not safety.empty else 10.0
    max_open = int(safety.iloc[0].get("max_open_positions", 10)) if not safety.empty else 10

    new_sl = st.sidebar.slider("Stop-loss %", 1, 30, int(sl_default), 1, key="sl")
    new_tp = st.sidebar.slider("Take-profit %", 5, 100, int(tp_default), 1, key="tp")
    new_max_pos = st.sidebar.number_input(
        "Max position size (USD)", min_value=10.0, max_value=10_000.0,
        value=max_pos_default, step=10.0, key="max_pos",
    )
    new_daily_loss = st.sidebar.slider(
        "Daily loss circuit-breaker %", 1, 50, int(daily_loss_limit), 1,
        help="Bot halts automatically when daily loss exceeds this %.",
        key="daily_loss",
    )
    new_max_open = st.sidebar.slider("Max open positions", 1, 50, max_open, 1, key="max_open")

    if st.sidebar.button("💾 Save risk parameters"):
        with _open_rw() as rw:
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

    # ── 4. trading mode ──────────────────────────────────────────────────────
    st.sidebar.subheader("🔄 Trading mode")
    current_mode = _scalar(conn_ro, "SELECT trading_mode FROM safety_state WHERE id = 1", default="paper")
    
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
            st.sidebar.warning("⚠️ Switching to LIVE mode will use real funds.")
        if st.sidebar.button("Confirm mode switch", type="primary"):
            with _open_rw() as rw:
                rw.execute("UPDATE safety_state SET trading_mode = ? WHERE id = 1", (new_mode,))
                rw.commit()
            st.sidebar.success(f"Mode set to {new_mode}.")
            st.rerun()

    st.sidebar.divider()

    # ── 5. scan interval ─────────────────────────────────────────────────────
    st.sidebar.subheader("⏱️ Scan interval")
    current_interval = int(_scalar(conn_ro, "SELECT scan_interval_seconds FROM safety_state WHERE id = 1", default=60))
    
    new_interval = st.sidebar.slider(
        "Seconds between LLM scans", 10, 300, current_interval, 10,
        help="Lower = more API calls. Higher = slower reaction.",
        key="scan_interval",
    )
    if new_interval != current_interval:
        if st.sidebar.button("Save interval"):
            with _open_rw() as rw:
                rw.execute(
                    "UPDATE safety_state SET scan_interval_seconds = ? WHERE id = 1",
                    (new_interval,),
                )
                rw.commit()
            st.sidebar.success("Interval saved.")
            st.rerun()

    st.sidebar.divider()

    # ── 6. quick blacklist ────────────────────────────────────────────────────
    st.sidebar.subheader("🚫 Add to blacklist")
    bl_symbol = st.sidebar.text_input("Coin symbol (e.g. BONK)", key="bl_sym").upper().strip()
    bl_address = st.sidebar.text_input("Coin address (optional)", key="bl_addr").strip()
    bl_reason = st.sidebar.text_input("Reason", key="bl_reason").strip()
    bl_permanent = st.sidebar.checkbox("Permanent ban", value=False, key="bl_perm")
    bl_hours = st.sidebar.number_input(
        "Temporary ban duration (hours)", min_value=1, max_value=720,
        value=24, step=1, key="bl_hours",
        disabled=bl_permanent,
    )

    if st.sidebar.button("Add to blacklist", disabled=(not bl_symbol or not bl_reason)):
        expires = None if bl_permanent else (
            datetime.utcnow() + timedelta(hours=int(bl_hours))
        ).strftime("%Y-%m-%d %H:%M:%S")
        with _open_rw() as rw:
            rw.execute(
                """INSERT OR REPLACE INTO blacklist
                   (coin_symbol, coin_address, reason, source, permanent, expires_at, blacklisted_at)
                   VALUES (?, ?, ?, 'dashboard', ?, ?, CURRENT_TIMESTAMP)""",
                (bl_symbol, bl_address or None, bl_reason, int(bl_permanent), expires),
            )
            rw.commit()
        st.sidebar.success(f"{bl_symbol} blacklisted.")
        st.rerun()


# ── portfolio metrics ─────────────────────────────────────────────────────────

def _render_safety_banner(conn: sqlite3.Connection) -> None:
    row = _df(conn, "SELECT * FROM safety_state WHERE id = 1")
    if row.empty:
        st.info("No safety state found.")
        return
    state = row.iloc[0]
    mode = state.get("trading_mode", "paper")
    mode_badge = "🟢 PAPER" if mode == "paper" else "🔴 LIVE"
    if int(state.get("emergency_stop", 0)):
        st.error(
            f"🚨 EMERGENCY STOP ACTIVE — {state.get('stop_reason')} | "
            f"Daily loss: {float(state.get('daily_loss_pct') or 0):.2%} | Mode: {mode_badge}"
        )
    else:
        st.success(f"✅ Bot running normally | Mode: {mode_badge}")


def _render_kpis(conn: sqlite3.Connection) -> None:
    """Render top KPI metric cards."""

    # total balance across all buckets
    total_balance = _scalar(conn, "SELECT SUM(balance) FROM fund_buckets", default=0.0)

    # daily P&L from positions closed today
    daily_pnl = _scalar(
        conn,
        "SELECT COALESCE(SUM(pnl_usd), 0) FROM positions "
        "WHERE status = 'CLOSED' AND DATE(closed_at) = DATE('now')",
        default=0.0,
    )

    # all-time win rate
    total_closed = _scalar(conn, "SELECT COUNT(*) FROM positions WHERE status = 'CLOSED'", default=0)
    winning = _scalar(
        conn, "SELECT COUNT(*) FROM positions WHERE status = 'CLOSED' AND pnl_usd > 0", default=0
    )
    win_rate = (winning / total_closed * 100) if total_closed else 0.0

    # open count
    open_count = _scalar(conn, "SELECT COUNT(*) FROM positions WHERE status = 'OPEN'", default=0)

    # best / worst single trade
    best_pnl = _scalar(
        conn, "SELECT MAX(pnl_usd) FROM positions WHERE status = 'CLOSED'", default=0.0
    )
    worst_pnl = _scalar(
        conn, "SELECT MIN(pnl_usd) FROM positions WHERE status = 'CLOSED'", default=0.0
    )

    # average hold time (minutes)
    avg_hold = _scalar(
        conn,
        "SELECT AVG((JULIANDAY(closed_at) - JULIANDAY(opened_at)) * 1440) "
        "FROM positions WHERE status = 'CLOSED' AND closed_at IS NOT NULL",
        default=0.0,
    )

    cols = st.columns(7)
    metrics = [
        ("💼 Portfolio", f"${total_balance:,.2f}", None),
        ("📈 Daily P&L", f"${daily_pnl:+,.2f}", daily_pnl),
        ("🎯 Win rate", f"{win_rate:.1f}%", None),
        ("📂 Open positions", str(open_count), None),
        ("🏆 Best trade", f"${best_pnl:+,.2f}", best_pnl),
        ("💀 Worst trade", f"${worst_pnl:+,.2f}", worst_pnl),
        ("⏱️ Avg hold", f"{avg_hold:.1f}m", None),
    ]
    for col, (label, value, delta) in zip(cols, metrics):
        with col:
            if delta is not None:
                colour = "normal" if delta >= 0 else "inverse"
                st.metric(label, value, delta=f"{'↑' if delta >= 0 else '↓'}")
            else:
                st.metric(label, value)


def _render_equity_curve(conn: sqlite3.Connection) -> None:
    """7-day equity curve built from closed positions."""
    st.subheader("Equity curve — last 7 days")
    df = _df(
        conn,
        """
        SELECT DATE(closed_at) AS day, SUM(pnl_usd) AS daily_pnl
        FROM positions
        WHERE status = 'CLOSED'
          AND closed_at >= DATETIME('now', '-7 days')
        GROUP BY day ORDER BY day
        """,
    )
    if df.empty:
        st.info("No closed trades in the last 7 days.")
        return

    # starting balance for the window
    start_balance = _scalar(conn, "SELECT SUM(balance) FROM fund_buckets", default=0.0)
    # back-calculate the balance at start of the 7d window
    window_pnl_total = df["daily_pnl"].sum()
    start_balance_window = start_balance - window_pnl_total

    df["cumulative_pnl"] = df["daily_pnl"].cumsum()
    df["balance"] = start_balance_window + df["cumulative_pnl"]

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=df["day"],
            y=df["balance"].round(2),
            mode="lines+markers",
            fill="tozeroy",
            line=dict(color="#378ADD", width=2),
            fillcolor="rgba(55,138,221,0.08)",
            name="Balance",
        )
    )
    fig.update_layout(
        margin=dict(l=0, r=0, t=0, b=0),
        height=220,
        xaxis_title=None,
        yaxis_title="USD",
        showlegend=False,
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig, use_container_width=True)


def _render_pnl_by_bucket(conn: sqlite3.Connection) -> None:
    """Bar chart of total P&L per bucket."""
    st.subheader("P&L by bucket")
    df = _df(
        conn,
        "SELECT bucket_name, ROUND(SUM(pnl_usd), 2) AS total_pnl "
        "FROM positions WHERE status = 'CLOSED' GROUP BY bucket_name ORDER BY total_pnl DESC",
    )
    if df.empty:
        st.info("No closed trades yet.")
        return
    df["colour"] = df["total_pnl"].apply(lambda v: "#1D9E75" if v >= 0 else "#E24B4A")
    fig = px.bar(
        df,
        x="bucket_name",
        y="total_pnl",
        color="colour",
        color_discrete_map="identity",
        text="total_pnl",
    )
    fig.update_traces(texttemplate="$%{text:,.2f}", textposition="outside")
    fig.update_layout(
        margin=dict(l=0, r=0, t=0, b=0),
        height=260,
        showlegend=False,
        xaxis_title=None,
        yaxis_title="USD P&L",
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig, use_container_width=True)


def _render_buckets(conn: sqlite3.Connection) -> None:
    st.subheader("Fund buckets")
    buckets = _df(
        conn,
        "SELECT bucket_name, allocation_pct, balance, enabled, description, updated_at "
        "FROM fund_buckets ORDER BY bucket_name",
    )
    positions = _df(
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
        margin=dict(l=0, r=0, t=30, b=0),
        height=280,
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig, use_container_width=True)


def _render_positions(conn: sqlite3.Connection) -> None:
    st.subheader("Open positions")
    st.dataframe(
        _df(
            conn,
            "SELECT id, bucket_name, coin_symbol, entry_price, size_usd, "
            "stop_loss_pct, take_profit_pct, opened_at FROM positions "
            "WHERE status = 'OPEN' ORDER BY opened_at DESC",
        ),
        use_container_width=True,
    )
    st.subheader("Recent closed positions (last 50)")
    closed = _df(
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


def _render_llm(conn: sqlite3.Connection) -> None:
    st.subheader("Latest LLM verdicts (last 50)")
    df = _df(
        conn,
        "SELECT scan_time, coin_symbol, bucket, llm_score, verdict, confidence, "
        "social_buzz, kol_mentioned, red_flags, reason, expires_at "
        "FROM llm_scan_results ORDER BY scan_time DESC LIMIT 50",
    )
    if df.empty:
        st.info("No LLM scans yet.")
    else:
        st.dataframe(df, use_container_width=True)


def _render_blacklist(conn: sqlite3.Connection) -> None:
    st.subheader("Active blacklist")
    df = _df(
        conn,
        "SELECT coin_symbol, coin_address, reason, source, permanent, expires_at, "
        "blacklisted_at FROM blacklist "
        "WHERE permanent = 1 OR expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP "
        "ORDER BY blacklisted_at DESC LIMIT 100",
    )
    if df.empty:
        st.info("Blacklist is empty.")
    else:
        st.dataframe(df, use_container_width=True)


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    st.title("SolanaMemBot dashboard")
    st.caption("Live view of trading state · Sidebar controls write directly to SQLite.")

    conn = _open_ro()
    if conn is None:
        st.warning(f"Database not found at {DB_PATH}. Start the bot first.")
        return

    with closing(conn):
        _sidebar(conn)
        _render_safety_banner(conn)

        st.divider()
        _render_kpis(conn)

        st.divider()
        col_left, col_right = st.columns([2, 1])
        with col_left:
            _render_equity_curve(conn)
        with col_right:
            _render_pnl_by_bucket(conn)

        st.divider()
        _render_buckets(conn)

        st.divider()
        _render_positions(conn)

        st.divider()
        _render_llm(conn)

        st.divider()
        _render_blacklist(conn)


if __name__ == "__main__":
    main()