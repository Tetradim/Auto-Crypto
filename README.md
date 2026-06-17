# Auto-Crypto

Auto-Crypto is a paper-first crypto trading automation service for Discord and webhook-driven alerts. The first implementation slice focuses on safe signal intake, risk checks, idempotency, and paper execution with bracket-style exits.

## Current Capabilities

- TradingView/custom webhook endpoint: `POST /webhooks/tradingview`
- Text alert webhook endpoint: `POST /webhooks/text-alert`
- Strict crypto signal normalization for pairs such as `BTCUSDT` and `BTC/USDT`
- Duplicate signal suppression with idempotency keys, including SQLite-backed restart safety
- Pre-trade risk checks for stop-loss requirement, max notional, leverage, slippage, allowed venues, blocked symbols, and daily loss
- Paper exchange that records accepted orders, planned stop-loss/take-profit exits, and triggered paper exits per filled lot
- Minimal Discord slash-command client using `/health` and `/signal_test`
- Optional CCXT adapter boundary for future live exchange integrations
- Exchange discovery and capability reporting for paper mode and installed CCXT venues
- Optional HMAC-signed webhook verification through `AUTO_CRYPTO_WEBHOOK_SECRET`
- SQLite repository for signal, paper-order, and audit history
- Operator halt/resume controls that block new orders and record audit events
- Paper portfolio accounting with weighted average entry and realized PnL
- Paper market price updates that trigger stop-loss/take-profit exits and audit events
- Approval-required mode for human review before order execution, with SQLite-backed pending approvals
- Conservative text alert parser for Discord-style messages

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
`AUTO_CRYPTO_ALLOWED_EXCHANGES` defaults to `paper`; add venue IDs such as `binance` or `kraken` only after API keys and live execution controls are ready.

Install the optional exchange extras to inspect CCXT-supported venues:

```powershell
python -m pip install -e ".[exchange]"
```

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

When `AUTO_CRYPTO_DB_PATH` is enabled, signal IDs are claimed with an insert-only write before approval or execution. Replayed signal IDs after a service restart return `status=duplicate` and record a `signal.duplicate` audit event.

## Exchange Discovery

- `GET /exchanges`: returns paper mode plus installed CCXT exchange IDs
- `GET /exchanges/paper/capabilities`: returns paper execution capabilities
- `GET /exchanges/{exchange_id}/capabilities`: returns CCXT-reported venue capabilities when `auto-crypto[exchange]` is installed

Exchange discovery does not enable live trading by itself. Live order placement still requires an explicit adapter path and exchange API keys without withdrawal permissions.
Signals whose `exchange` value is not in `AUTO_CRYPTO_ALLOWED_EXCHANGES` are rejected by risk checks.

## Paper Price Updates

Use `POST /market/price` to feed paper-mode market prices into the bracket engine. When the new price crosses an active stop loss or take profit, Auto-Crypto records a synthetic sell order, closes the paper position, updates realized PnL, and records an `exit.triggered` audit event.
Multiple entries on the same symbol keep independent paper lots, so one take-profit or stop-loss trigger closes only the matching lot instead of flattening the whole symbol.

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/market/price -ContentType "application/json" -Body '{
  "symbol": "BTCUSDT",
  "price": "51500"
}'
```

## Operator Controls

- `GET /control/status`: halt status
- `POST /control/halt` with `{"reason": "..."}`: blocks new order execution
- `POST /control/resume`: resumes new order execution

## Approval Mode

Set `AUTO_CRYPTO_REQUIRE_APPROVAL=true` or call `create_app(require_approval=True)` to queue incoming signals instead of executing immediately.
When `AUTO_CRYPTO_DB_PATH` is enabled, pending approvals survive service restarts and can be approved or rejected by any later app instance using the same database.

- `GET /approvals`: pending signals
- `POST /approvals/{signal_id}/approve`: executes the queued signal through the normal risk and execution path
- `POST /approvals/{signal_id}/reject` with `{"reason": "..."}`: removes the queued signal and records an audit event

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
The default allowed exchange is `paper`; alert-supplied live venue IDs must be configured before risk approval.

## Text Alert Format

The text parser is intentionally strict. Supported examples:

```text
BUY BTCUSDT $125 @ 50000 SL 2.5% TP 5%
SELL ETH/USDT 0.25 @ 3000
```

Use `POST /signals/parse-text` with `{"message": "..."}` to validate a message without placing an order.
Use `POST /webhooks/text-alert` with the same payload shape to run the parsed alert through duplicate checks, risk, approval mode, and paper execution.

## Roadmap

1. Wire Discord buttons to approval/halt endpoints.
2. Add official sandbox/live adapters behind explicit config gates.
3. Add portfolio reconciliation and exchange user-stream workers.
4. Add PostgreSQL deployment option for multi-user hosting.
5. Add deployment manifests and CI.
