"""Reusable Streamlit metric card components."""

from __future__ import annotations

import streamlit as st


def metric_card(label: str, value: str, delta: str | None = None, delta_color: str = "normal") -> None:
    """Render a styled metric card.

    Args:
        label: Metric label text.
        value: Main value to display.
        delta: Optional delta/change indicator.
        delta_color: Color for the delta ("normal", "inverse", or "off").
    """
    st.metric(label=label, value=value, delta=delta, delta_color=delta_color)


def metric_row(metrics: list[dict[str, str]]) -> None:
    """Render a horizontal row of metric cards.

    Args:
        metrics: List of dicts with keys 'label', 'value', and optionally 'delta'.
    """
    cols = st.columns(len(metrics))
    for col, m in zip(cols, metrics):
        with col:
            metric_card(
                label=m["label"],
                value=m["value"],
                delta=m.get("delta"),
                delta_color=m.get("delta_color", "normal"),
            )


def status_indicator(label: str, is_active: bool) -> None:
    """Render a colored status indicator.

    Args:
        label: Status label.
        is_active: Whether the status is active/green.
    """
    color = "🟢" if is_active else "🔴"
    st.markdown(f"{color} **{label}**")
