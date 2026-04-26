"""Bridge between the Streamlit dashboard and the running bot."""

from __future__ import annotations

import json
from pathlib import Path

from core.logger import LoggerFactory

log = LoggerFactory.get_logger("dashboard_bridge")

STATE_FILE = Path("data/dashboard_state.json")


class DashboardBridge:
    """Reads dashboard_state.json to pick up UI control changes."""

    def __init__(self) -> None:
        self._last_state: dict = {}

    def read_state(self) -> dict:
        """Read the current dashboard state. Returns empty dict on failure."""
        try:
            if STATE_FILE.exists():
                data = json.loads(STATE_FILE.read_text())
                if data != self._last_state:
                    log.info("Dashboard state updated: {}", data)
                    self._last_state = data
                return data
        except Exception:
            pass
        return self._last_state

    def is_emergency_stop(self) -> bool:
        state = self.read_state()
        return state.get("emergency_stop", False)

    def is_strategy_enabled(self, strategy_name: str) -> bool:
        """Check if a strategy is enabled from dashboard controls."""
        state = self.read_state()
        key = f"{strategy_name}_enabled"
        return state.get(key, True)

    def get_slippage_bps(self, default: int = 100) -> int:
        state = self.read_state()
        return int(state.get("slippage_bps", default))

    def get_max_risk_pct(self, default: float = 2.0) -> float:
        state = self.read_state()
        return float(state.get("max_risk_pct", default)) / 100.0  # UI shows %, convert to decimal

    def get_max_drawdown_pct(self, default: float = 5.0) -> float:
        state = self.read_state()
        return float(state.get("max_drawdown_pct", default)) / 100.0

    def get_max_open_trades(self, default: int = 3) -> int:
        state = self.read_state()
        return int(state.get("max_open_trades", default))
