"""Shared helpers for fetching live Solana wallet data.

Used by both :mod:`dashboard.metrics` and :mod:`dashboard.components`
to avoid code duplication and provide consistent error handling.
"""

from __future__ import annotations

import json
import os
from urllib.request import Request, urlopen

from dotenv import load_dotenv


def fetch_live_sol_balance() -> float | None:
    """Fetch real SOL balance from wallet via Solana RPC.

    Returns ``None`` when the public key is missing, the RPC returns a
    JSON-RPC error, or any network/parsing failure occurs.
    """
    load_dotenv(override=False)
    pub_key = os.getenv("WALLET_PUBLIC_KEY")
    rpc_url = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
    if not pub_key:
        return None
    body = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getBalance",
        "params": [pub_key],
    }).encode()
    req = Request(rpc_url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        if "error" in data:
            return None
        result = data.get("result")
        if result is None:
            return None
        lamports = result.get("value")
        if lamports is None:
            return None
        return lamports / 1e9
    except Exception:  # noqa: BLE001
        return None


def fetch_sol_price_usd() -> float | None:
    """Fetch current SOL/USD price from CoinGecko.

    Returns ``None`` when the API call fails so callers can decide how
    to handle the missing price (e.g. fall back to DB balance).
    """
    try:
        url = "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd"
        req = Request(url, headers={"Accept": "application/json"})
        with urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        return float(data["solana"]["usd"])
    except Exception:  # noqa: BLE001
        return None
