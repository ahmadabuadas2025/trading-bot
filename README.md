# SolanaMemBot

A personal, config-driven Solana meme-coin trading bot with four
independent fund buckets, math-driven fast scoring, and a twice-daily
LLM social-intelligence layer.

## Philosophy

- **Math makes fast decisions** (every few minutes).
- **LLM makes slow smart decisions** (twice per day at 08:00 and
  20:00 UTC).
- **Four independent strategies** with separate fund buckets.
- **Every decision is logged and explainable.**
- **Paper trade 30 days** before going live.

## Architecture at a glance

```
core/         Infrastructure: db, config, logging, http, scoring, regime,
              safety, slippage, dedup, blacklist, ATR, social collector,
              LLM client + scanner, executor, orchestrator.
clients/      External API clients: dexscreener, birdeye, helius,
              coingecko, jupiter.
services/     Bucket services: hot_trader, copy_trading, gem_detector,
              new_listing.
utils/        Shared helpers: honeypot checker, time utils.
tests/        Pytest unit tests (math and safety modules).
```

Every module is a class. Every function has a docstring. All I/O is
async (asyncio + aiohttp + aiosqlite). All external HTTP calls
go through core/http.py, which provides exponential backoff and a
circuit breaker.

## Quickstart

### Requirements

- Python 3.12
- On first run the bot will create data/bot.db (SQLite, WAL mode)
  and logs/bot.log.

### Install

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -e .[dev]
cp .env.example .env   # edit as needed (all keys optional in paper mode)
```

### Run the bot (paper mode, default)

```bash
python main.py --mode paper
```

### Run the dashboard (separate terminal)

```bash
streamlit run dashboard.py
```

### Live mode

```bash
# DANGER: real funds. Set WALLET_PRIVATE_KEY in .env first.
python main.py --mode live
```

## Configuration

- Secrets live in `.env` (see `.env.example`).
- Parameters live in `config.yaml`.

## Testing

```bash
pytest -q
```

## Safety

The bot will refuse to start live mode without a wallet. It also
enforces a daily 15% loss emergency stop, per-bucket cooldowns after
consecutive losses, a honeypot check before every buy, and a
persistent blacklist.

This software is provided as-is for personal research. Trading crypto
is risky and you can lose all of your capital.
