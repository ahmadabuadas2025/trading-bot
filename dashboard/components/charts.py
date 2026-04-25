"""Reusable Plotly chart components for the dashboard."""

from __future__ import annotations

import plotly.graph_objects as go


def equity_curve(timestamps: list[str], values: list[float], title: str = "Equity Curve") -> go.Figure:
    """Create an equity curve line chart.

    Args:
        timestamps: List of timestamp strings for the x-axis.
        values: List of equity values for the y-axis.
        title: Chart title.

    Returns:
        Plotly Figure object.
    """
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=timestamps,
            y=values,
            mode="lines",
            name="Equity",
            line={"color": "#00d4aa", "width": 2},
            fill="tozeroy",
            fillcolor="rgba(0, 212, 170, 0.1)",
        )
    )
    fig.update_layout(
        title=title,
        xaxis_title="Time",
        yaxis_title="USD",
        template="plotly_dark",
        height=400,
        margin={"l": 40, "r": 20, "t": 40, "b": 40},
    )
    return fig


def pnl_bar_chart(
    labels: list[str],
    values: list[float],
    title: str = "PnL by Strategy",
) -> go.Figure:
    """Create a PnL bar chart with color-coded positive/negative bars.

    Args:
        labels: Strategy or category labels.
        values: PnL values per label.
        title: Chart title.

    Returns:
        Plotly Figure object.
    """
    colors = ["#00d4aa" if v >= 0 else "#ff4444" for v in values]
    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=labels,
            y=values,
            marker_color=colors,
            name="PnL",
        )
    )
    fig.update_layout(
        title=title,
        xaxis_title="Strategy",
        yaxis_title="PnL (USD)",
        template="plotly_dark",
        height=350,
        margin={"l": 40, "r": 20, "t": 40, "b": 40},
    )
    return fig


def win_rate_pie(wins: int, losses: int, title: str = "Win/Loss Ratio") -> go.Figure:
    """Create a win/loss pie chart.

    Args:
        wins: Number of winning trades.
        losses: Number of losing trades.
        title: Chart title.

    Returns:
        Plotly Figure object.
    """
    fig = go.Figure()
    fig.add_trace(
        go.Pie(
            labels=["Wins", "Losses"],
            values=[wins, losses],
            marker={"colors": ["#00d4aa", "#ff4444"]},
            hole=0.4,
            textinfo="label+percent",
        )
    )
    fig.update_layout(
        title=title,
        template="plotly_dark",
        height=300,
        margin={"l": 20, "r": 20, "t": 40, "b": 20},
    )
    return fig


def cumulative_profit_chart(
    timestamps: list[str],
    profits: list[float],
    title: str = "Cumulative Profit",
) -> go.Figure:
    """Create a cumulative profit line chart.

    Args:
        timestamps: List of timestamp strings.
        profits: List of cumulative profit values.
        title: Chart title.

    Returns:
        Plotly Figure object.
    """
    cumulative: list[float] = []
    total = 0.0
    for p in profits:
        total += p
        cumulative.append(total)

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=timestamps,
            y=cumulative,
            mode="lines+markers",
            name="Cumulative Profit",
            line={"color": "#ffa726", "width": 2},
            marker={"size": 4},
        )
    )
    fig.update_layout(
        title=title,
        xaxis_title="Time",
        yaxis_title="Profit (USD)",
        template="plotly_dark",
        height=400,
        margin={"l": 40, "r": 20, "t": 40, "b": 40},
    )
    return fig
