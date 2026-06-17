# Auto-Crypto

Auto-Crypto is a paper-first crypto trading automation service for Discord and webhook-driven alerts. The first implementation slice focuses on safe signal intake, risk checks, idempotency, and paper execution with bracket-style exits.

## Current Capabilities

- TradingView/custom webhook endpoint: `POST /webhooks/tradingview`
- Strict crypto signal normalization for pairs such as `BTCUSDT` and `BTC/USDT`
- Duplicate signal suppression with idempotency keys
- Pre-trade risk checks for stop-loss requirement, max notional, leverage, slippage, blocked symbols, and daily loss
- Paper exchange that records accepted orders and planned stop-loss/take-profit exits
- Minimal Discord slash-command client using `/health` and `/signal_test`
- Optional CCXT adapter boundary for future live exchange integrations
- Optional HMAC-signed webhook verification through `AUTO_CRYPTO_WEBHOOK_SECRET`
- SQLite repository for signal, paper-order, and audit history
- Operator halt/resume controls that block new orders and record audit events
- Paper portfolio accounting with weighted average entry and realized PnL

Live trading is intentionally not enabled by default. Do not grant withdrawal permissions to exchange API keys.

## Quick Start

```powershell
python -m venv .venv
.\\.venv\\Scripts\\Activate.ps1
python -m pip install -e ".[dev]"
python -m pytest
python -m uvicorn autocrypto.app:app --reload
```

For environment-backed settings, import and run `autocrypto.app:create_app_from_env()` from your ASGI launcher. `AUTO_CRYPTO_DB_PATH` enables SQLite persistence, and `AUTO_CRYPTO_WEBHOOK_SECRET` enables signed webhook enforcement.

Send a test signal:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/webhooks/tradingview -ContentType "application/json" -Body '{
  "symbol": "BTCUSDT",
  "side": "buy",
  "quote_amount": "25",
  "price": "50000",
  "stop_loss_pct": "2",
  "take_profit_pct": "3"
}'
```

## Signed Webhooks

Set `AUTO_CRYPTO_WEBHOOK_SECRET` to require signed webhook alerts. Signed requests must include:

- `x-auto-crypto-timestamp`: timestamp string chosen by the sender
- `x-auto-crypto-signature`: `sha256=<hex digest>`

The digest is `HMAC_SHA256(secret, timestamp + "." + raw_request_body)`.

When a signed request is accepted, the same timestamp/body pair is rejected on repeat as a replay. Apps that pass a webhook tolerance reject stale timestamps.

## History Endpoints

- `GET /signals`: accepted normalized signals
- `GET /orders`: accepted paper orders
- `GET /positions`: current paper portfolio positions
- `GET /audit`: signal and order lifecycle audit events

## Operator Controls

- `GET /control/status`: halt status
- `POST /control/halt` with `{"reason": "..."}`: blocks new order execution
- `POST /control/resume`: resumes new order execution

## Signal Schema

Required:

- `symbol` or `ticker`
- `side` or `action`
- `quote_amount`, `notional`, `base_amount`, `quantity`, or `qty`

Recommended:

- `price`
- `stop_loss_pct`
- `take_profit_pct`
- `max_slippage_bps`
- `strategy_id`
- `exchange`

Forbidden signal actions include withdrawal and transfer actions.

## Roadmap

1. Expand Discord controls with approve/reject/halt flows.
2. Add official sandbox/live adapters behind explicit config gates.
3. Add portfolio reconciliation and exchange user-stream workers.
4. Add PostgreSQL deployment option for multi-user hosting.
5. Add deployment manifests and CI.
