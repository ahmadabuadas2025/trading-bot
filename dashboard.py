"""Streamlit dashboard.

Read-only view over ``data/bot.db`` covering bucket balances, open
positions, closed trades, recent LLM scan results, blacklist entries,
and the safety state. All queries are synchronous reads on a copy of
the SQLite connection so the main bot's writer lock is never held.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

DB_PATH: Path = Path("data/bot.db")
st.set_page_config(page_title="SolanaMemBot", page_icon=":chart:", layout="wide")


def _open_readonly() -> sqlite3.Connection | None:
    """Open a read-only SQLite connection.

    Returns:
        A read-only connection, or ``None`` if the DB is missing.
    """
    if not DB_PATH.exists():
        return None
    uri = f"file:{DB_PATH.as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=5)


def _df(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> pd.DataFrame:
    """Run a query and return a DataFrame.

    Args:
        conn: Open SQLite connection.
        sql: SQL statement.
        params: Parameter tuple.

    Returns:
        A DataFrame (possibly empty).
    """
    with closing(conn.cursor()) as cur:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchall()
    return pd.DataFrame(rows, columns=cols)


def _render_header() -> None:
    """Render the page header."""
    st.title("SolanaMemBot dashboard")
    st.caption(
        "Read-only view of paper and live trading state. "
        "Every row comes straight from SQLite (WAL mode)."
    )


def _render_safety(conn: sqlite3.Connection) -> None:
    """Render the safety state banner.

    Args:
        conn: Open SQLite connection.
    """
    row = _df(conn, "SELECT * FROM safety_state WHERE id = 1")
    if row.empty:
        st.info("No safety state yet.")
        return
    state = row.iloc[0]
    if int(state.get("emergency_stop", 0)):
        st.error(
            f"EMERGENCY STOP ACTIVE — {state.get('stop_reason')}. "
            f"Daily loss: {float(state.get('daily_loss_pct') or 0.0):.2%}"
        )
    else:
        st.success("No emergency stop active.")


def _render_buckets(conn: sqlite3.Connection) -> None:
    """Render per-bucket balances and open-position counts.

    Args:
        conn: Open SQLite connection.
    """
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
    st.dataframe(merged, width="stretch")
    fig = px.bar(merged, x="bucket_name", y="balance", color="bucket_name", text="balance")
    st.plotly_chart(fig, width="stretch")


def _render_positions(conn: sqlite3.Connection) -> None:
    """Render the open and closed positions tables.

    Args:
        conn: Open SQLite connection.
    """
    st.subheader("Open positions")
    st.dataframe(
        _df(
            conn,
            "SELECT id, bucket_name, coin_symbol, entry_price, size_usd, "
            "stop_loss_pct, take_profit_pct, opened_at FROM positions "
            "WHERE status = 'OPEN' ORDER BY opened_at DESC",
        ),
        width="stretch",
    )
    st.subheader("Recent closed positions")
    st.dataframe(
        _df(
            conn,
            "SELECT id, bucket_name, coin_symbol, entry_price, exit_price, "
            "pnl_usd, pnl_pct, close_reason, closed_at FROM positions "
            "WHERE status = 'CLOSED' ORDER BY closed_at DESC LIMIT 50",
        ),
        width="stretch",
    )


def _render_llm(conn: sqlite3.Connection) -> None:
    """Render the most recent LLM scan.

    Args:
        conn: Open SQLite connection.
    """
    st.subheader("Latest LLM verdicts")
    df = _df(
        conn,
        "SELECT scan_time, coin_symbol, bucket, llm_score, verdict, confidence, "
        "social_buzz, kol_mentioned, red_flags, reason, expires_at "
        "FROM llm_scan_results ORDER BY scan_time DESC LIMIT 50",
    )
    if df.empty:
        st.info("No LLM scans yet.")
    else:
        st.dataframe(df, width="stretch")


def _render_blacklist(conn: sqlite3.Connection) -> None:
    """Render the active blacklist.

    Args:
        conn: Open SQLite connection.
    """
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
        st.dataframe(df, width="stretch")


def main() -> None:
    """Render the full dashboard."""
    _render_header()
    conn = _open_readonly()
    if conn is None:
        st.warning(f"Database not found at {DB_PATH}. Start the bot first.")
        return
    with closing(conn):
        _render_safety(conn)
        _render_buckets(conn)
        _render_positions(conn)
        _render_llm(conn)
        _render_blacklist(conn)


if __name__ == "__main__":
    main()
