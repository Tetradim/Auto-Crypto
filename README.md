# Auto-Crypto

Auto-Crypto is a paper-first crypto trading automation bot for Discord-style alerts, TradingView webhooks, and operator-controlled execution workflows. It is built to normalize crypto alerts, apply risk checks, queue approvals when required, simulate fills, track bracket exits, and persist audit history before any live exchange adapter is enabled.

Live trading is intentionally disabled by default. Use exchange API keys with trade-only permissions, no withdrawals, and only after sandbox validation.

## What It Does Now

- Runs a local FastAPI bot/API on Windows with `Launch-Auto-Crypto.bat`
- Accepts TradingView/custom JSON alerts at `POST /webhooks/tradingview`
- Accepts strict text crypto alerts at `POST /webhooks/text-alert`
- Parses text alerts without ordering at `POST /signals/parse-text`
- Normalizes crypto pairs such as `BTCUSDT`, `BTC/USDT`, `ETH-USDC`, and `SOL_USDT`
- Blocks duplicate signal IDs across restarts with SQLite-backed idempotency
- Applies pre-trade risk checks for stop loss, max order notional, max open notional, leverage, slippage, allowed exchanges, blocked symbols, and daily loss
- Supports approval-required mode with persisted pending approvals
- Records paper orders, paper positions, realized PnL, active bracket lots, and audit events
- Rehydrates paper positions, bracket lots, and exposure risk state from SQLite after restart
- Triggers paper stop-loss/take-profit exits from `POST /market/price`
- Exposes CCXT venue discovery and capability inspection without enabling live execution
- Provides a minimal Discord slash-command client for `/health` and `/signal_test`

## Windows Launcher

Double-click:

```text
Launch-Auto-Crypto.bat
```

The launcher:

- Creates `.venv` when missing
- Installs Auto-Crypto dependencies when needed
- Starts the API from `autocrypto.app:create_app_from_env`
- Uses persistent SQLite at `data/auto_crypto.sqlite3`
- Opens the FastAPI docs in your browser
- Stops processes it started when the launcher window closes

Useful switches:

```powershell
.\Launch-Auto-Crypto.bat -Port 8004 -InstallDeps
.\Launch-Auto-Crypto.bat -ExchangeDeps
.\Launch-Auto-Crypto.bat -StartDiscord
.\Launch-Auto-Crypto.bat -NoBrowser
```

`-StartDiscord` requires `DISCORD_BOT_TOKEN` in the environment. Without it, the launcher starts the webhook/API bot only.

The workstation suite reserves frontend port `3004` for Auto-Crypto. This checkout currently exposes API docs from the backend on port `8004`.

## Manual Start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m pytest
python -m uvicorn autocrypto.app:create_app_from_env --factory --host 127.0.0.1 --port 8004
```

Install optional CCXT support for venue discovery:

```powershell
python -m pip install -e ".[exchange]"
```

## Core Crypto Workflow

1. Send a JSON alert from TradingView or another alert source.
2. Auto-Crypto normalizes the crypto symbol, side, size, price, exchange, and strategy metadata.
3. Risk checks approve, reject, queue, or halt the signal.
4. In paper mode, the bot records an accepted order and updates the paper portfolio.
5. Price updates can trigger paper stop-loss or take-profit exits.
6. Signals, orders, positions, approvals, and audit events are stored in SQLite when `AUTO_CRYPTO_DB_PATH` is set.

## Send A Test Crypto Alert

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8004/webhooks/tradingview -ContentType "application/json" -Body '{
  "signal_id": "btc-breakout-001",
  "symbol": "BTCUSDT",
  "side": "buy",
  "quote_amount": "25",
  "price": "50000",
  "stop_loss_pct": "2",
  "take_profit_pct": "3",
  "strategy_id": "breakout"
}'
```

Check positions:

```powershell
Invoke-RestMethod http://127.0.0.1:8004/positions
```

Trigger a paper take-profit:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8004/market/price -ContentType "application/json" -Body '{
  "symbol": "BTCUSDT",
  "price": "51500"
}'
```

## Text Crypto Alerts

The text parser is intentionally strict so Discord-style alerts are explicit and auditable.

Supported examples:

```text
BUY BTCUSDT $125 @ 50000 SL 2.5% TP 5%
SELL ETH/USDT 0.25 @ 3000
```

Validate text without placing an order:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8004/signals/parse-text -ContentType "application/json" -Body '{
  "message": "BUY SOLUSDT $50 @ 150 SL 3% TP 8%"
}'
```

Run parsed text through the normal duplicate, risk, approval, and paper execution path:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8004/webhooks/text-alert -ContentType "application/json" -Body '{
  "message": "BUY SOLUSDT $50 @ 150 SL 3% TP 8%"
}'
```

## Signal Schema

Required fields:

- `symbol`, `ticker`, or `pair`
- `side` or `action`
- `quote_amount`, `notional`, `base_amount`, `quantity`, or `qty`

Recommended fields:

- `signal_id`
- `price`, `entry_price`, or `limit_price`
- `stop_loss_pct`
- `take_profit_pct`
- `max_slippage_bps`
- `strategy_id` or `strategy`
- `exchange` or `venue`
- `market_type`

Forbidden actions include `withdraw`, `transfer`, `internal_transfer`, and `deposit`.

## Risk Controls

Risk checks run before paper execution:

- `stop_loss_required`
- `max_order_notional_exceeded`
- `max_open_notional_exceeded`
- `max_leverage_exceeded`
- `max_slippage_exceeded`
- `exchange_not_allowed`
- `symbol_not_allowed`
- `symbol_blocked`
- `daily_loss_limit_exceeded`
- `price_required_for_base_amount`

Set `AUTO_CRYPTO_MAX_OPEN_NOTIONAL` above `0` to cap cumulative open buy exposure. SQLite-backed paper state restores open exposure after restart, and triggered paper exits release exposure for later risk checks.

## Environment Variables

```env
AUTO_CRYPTO_HOST=127.0.0.1
AUTO_CRYPTO_PORT=8004
AUTO_CRYPTO_REQUIRE_APPROVAL=false
AUTO_CRYPTO_DB_PATH=./data/auto_crypto.sqlite3

AUTO_CRYPTO_WEBHOOK_SECRET=
AUTO_CRYPTO_WEBHOOK_TOLERANCE_SECONDS=300

AUTO_CRYPTO_MAX_ORDER_NOTIONAL=1000
AUTO_CRYPTO_MAX_OPEN_NOTIONAL=0
AUTO_CRYPTO_MAX_LEVERAGE=1
AUTO_CRYPTO_MAX_DAILY_LOSS=500
AUTO_CRYPTO_MAX_SLIPPAGE_BPS=100
AUTO_CRYPTO_REQUIRE_STOP_LOSS=true

AUTO_CRYPTO_DEFAULT_EXCHANGE=paper
AUTO_CRYPTO_ALLOWED_EXCHANGES=paper

DISCORD_BOT_TOKEN=
```

## Signed Webhooks

Set `AUTO_CRYPTO_WEBHOOK_SECRET` to require HMAC-signed alert requests. Signed requests must include:

- `x-auto-crypto-timestamp`
- `x-auto-crypto-signature`

Signature format:

```text
sha256=<hex digest>
```

Digest payload:

```text
timestamp + "." + raw_request_body
```

Accepted signed payloads are replay-protected. Stale timestamps are rejected when `AUTO_CRYPTO_WEBHOOK_TOLERANCE_SECONDS` is set.

## Main Endpoints

Health and history:

- `GET /health`
- `GET /signals`
- `GET /orders`
- `GET /positions`
- `GET /audit`

Signal intake:

- `POST /webhooks/tradingview`
- `POST /webhooks/text-alert`
- `POST /signals/parse-text`

Paper market updates:

- `POST /market/price`

Operator controls:

- `GET /control/status`
- `POST /control/halt`
- `POST /control/resume`

Approval mode:

- `GET /approvals`
- `POST /approvals/{signal_id}/approve`
- `POST /approvals/{signal_id}/reject`

Exchange inspection:

- `GET /exchanges`
- `GET /exchanges/paper/capabilities`
- `GET /exchanges/{exchange_id}/capabilities`

## Approval Mode

Set `AUTO_CRYPTO_REQUIRE_APPROVAL=true` or call `create_app(require_approval=True)` to queue incoming signals instead of executing immediately.

When `AUTO_CRYPTO_DB_PATH` is enabled, pending approvals survive service restarts and can be approved or rejected by any later app instance using the same database.

## Exchange Discovery

`GET /exchanges` returns paper mode plus installed CCXT exchange IDs with separate flags:

- `driver_available`
- `credentials_configured`
- `live_execution_enabled`

CCXT discovery does not enable live trading. It means the adapter driver can be inspected. Live order placement still needs explicit implementation, credentials, configuration gates, and exchange API keys without withdrawal permissions.

Signals whose `exchange` value is not in `AUTO_CRYPTO_ALLOWED_EXCHANGES` are rejected by risk checks. The default allowed exchange is `paper`.

## Persistence

When SQLite is enabled:

- Signal IDs are claimed with insert-only idempotency writes.
- Duplicate signal IDs after restart return `status=duplicate`.
- Pending approvals persist across restarts.
- Paper orders are replayed at startup to restore positions, active bracket lots, and open exposure.
- Paper price exits are saved as synthetic sell orders.

## Safety Notes

- Paper mode is the default and only execution path currently enabled.
- Do not use exchange API keys with withdrawal permissions.
- Do not allow non-paper exchange IDs until live execution controls are explicitly implemented and tested.
- Use signed webhooks for any alert source exposed beyond localhost.
- Treat alert text as commands. Keep formats strict and auditable.

## Roadmap

1. Wire Discord buttons to approval, reject, halt, and resume endpoints.
2. Add sandbox/live exchange adapters behind explicit configuration gates.
3. Add portfolio reconciliation and exchange user-stream workers.
4. Add PostgreSQL deployment support for hosted or multi-user operation.
5. Add CI, deployment manifests, and packaged installer support.
