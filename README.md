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
- Applies pre-trade risk checks for stop loss, maximum stop width, minimum reward/risk, max order notional, max open notional, equity-percent position size, leverage, slippage, allowed exchanges, blocked symbols, daily loss, and consecutive losing exits
- Supports fixed-fraction paper sizing from `risk_pct` or `risk_amount` plus stop distance, with an optional max risk-percent cap
- Supports approval-required mode with persisted pending approvals
- Records paper orders, paper positions, realized PnL, active bracket lots, and audit events
- Rehydrates paper positions, bracket lots, and exposure risk state from SQLite after restart
- Triggers paper long and short stop-loss, single or staged take-profit, activation-gated trailing-stop, and break-even exits from `POST /market/price`
- Accepts bracket exits either as top-level signal fields or nested under `bracket`, `bracket_order`, or `exit_plan`
- Rejects staged take-profit brackets when any absolute target is on the wrong side of entry for the order direction
- Rejects standalone break-even triggers unless there is a stop-loss or trailing-stop leg for the break-even rule to move
- Links paper bracket exit legs with OCA-style groups and records which sibling stop, take-profit, or trailing legs are canceled when a final paper exit closes the lot
- Marks activation-gated trailing stops as `pending_activation` until price movement arms them, then records triggered paper exits as `filled` reduce-only close orders
- Lists all active synthetic paper brackets and supports protective stop amendments that only tighten risk
- Previews one active bracket by signal ID at a hypothetical mark, including trigger distance and trailing activation context
- Cancels active synthetic paper bracket exits by signal ID while leaving the underlying paper position open for separate manual management
- Previews hypothetical market-price marks and bracket/trailing exits without mutating paper orders or positions
- Previews server-side risk decisions from the operator UI without placing orders
- Backtests one signal against a supplied paper mark-price path and returns active exit snapshots after each mark without mutating the live in-memory engine
- Shows persisted signal history with one-click reload into the Trading Desk
- Supports quote-notional and base-quantity ticket sizing, paper position close controls, bracket lot context and trigger tests, and local unrealized P&L marks in the operator UI
- Captures inline halt and approval rejection reasons in operator workflows
- Shows timestamped audit events, exports filtered audit CSVs, and copies JSON payloads from operator panels
- Exposes CCXT venue discovery and capability inspection without enabling live execution
- Tracks a curated Bitcoin platform registry for Coinbase, Kraken, Gemini, Bitstamp, Binance.US, Alpaca, Robinhood, Crypto.com, OKX, Bybit, KuCoin, Bitget, Gate.io, MEXC, Phemex, BitMEX, Deribit, and Bitunix
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
- Opens the Auto-Crypto operator UI in your browser
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

The production operator UI is served by the backend at:

```text
http://127.0.0.1:8004/ui
```

FastAPI docs remain available at:

```text
http://127.0.0.1:8004/docs
```

## UI Prototype Gallery

The original five selectable UI iterations remain available in `ui-prototypes/` as design references.

```powershell
cd ui-prototypes
python -m http.server 8064 --bind 127.0.0.1
```

Open `http://127.0.0.1:8064` and use the left navigation to switch between Command Center, Trading Desk, Strategy Marketplace, Portfolio Sentinel, and Signal Forge.

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

## Operator UI Smoke Test

Run the full browser-driven operator workflow after UI changes:

```powershell
python -m pip install -e ".[dev]"
python scripts/operator_ui_smoke.py
```

The smoke test starts a temporary approval-mode API with an isolated SQLite database, opens the production UI in a real browser, exercises every tab and core control path, exports JSON/CSV files, and fails on page errors, JavaScript console errors, or blank required canvases.

If Playwright's bundled browser is not installed, point the smoke test at an existing Chrome or Edge executable:

```powershell
$env:AUTO_CRYPTO_BROWSER_PATH="C:\Program Files\Google\Chrome\Application\chrome.exe"
python scripts/operator_ui_smoke.py
```

## Core Crypto Workflow

1. Send a JSON alert from TradingView or another alert source.
2. Auto-Crypto normalizes the crypto symbol, side, size, price, exchange, and strategy metadata.
3. Risk checks approve, reject, queue, or halt the signal.
4. In paper mode, the bot records an accepted order and updates the paper portfolio.
5. Price updates can trigger paper stop-loss, take-profit, or trailing-stop exits. Long bracket exits sell; short bracket exits buy back paper quantity.
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
  "stop_loss_price": "49000",
  "take_profit_pct": "3",
  "take_profit_price": "51500",
  "take_profit_targets": [
    {"pct": "3", "close_pct": "50"},
    {"trigger_price": "53000", "close_pct": "50"}
  ],
  "trailing_stop_pct": "2.5",
  "trailing_activation_pct": "1.5",
  "breakeven_trigger_pct": "2",
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

For buy brackets, `stop_loss_pct` creates a fixed protective sell below entry, `take_profit_pct` creates a fixed profit-taking sell above entry, and `trailing_stop_pct` creates a sell stop that starts below entry and ratchets upward when `POST /market/price` marks a new high-water price. Use `stop_loss_price` and `take_profit_price` when the alert source already calculated exact trigger prices. The trailing stop never moves lower. Add `trailing_activation_pct` to keep the trailing leg dormant until price has moved favorably by that percent, and add `breakeven_trigger_pct` to move protective stop exits up to the entry price after a favorable move.

For short brackets, send `side: "sell"` or `side: "short"` with at least one exit field such as `stop_loss_pct`, `stop_loss_price`, `take_profit_pct`, `take_profit_price`, or `trailing_stop_pct`. Paper stop-loss and trailing-stop triggers sit above entry, paper take-profit triggers sit below entry, and exit orders buy back paper quantity. Short trailing stops track a low-water price and ratchet downward only after favorable price movement; `trailing_activation_pct` delays arming until price falls by that percent. A plain `SELL` without bracket fields remains a manual long close. Send `side: "close_short"` or `reduce_only: true` with `side: "buy"` to buy back paper short quantity without opening a new paper long.

Every paper bracket leg now carries an `oca_group` and `status` in order JSON and active-exit snapshots. Activation-gated trailing stops start as `pending_activation`, move to `open` when the favorable activation mark is reached, and are recorded as `filled` on the synthetic paper exit order that closes quantity. When a stop-loss, trailing-stop, or final take-profit closes the remaining paper lot, the synthetic exit order is marked `reduce_only: true` and includes `exit_kind`, the filled exit leg, plus `canceled_exit_orders` so tests and operators can see which sibling legs would have been canceled in one-cancels-other behavior. This is still paper accounting only; no live OCO or exchange-native bracket order is submitted.

Operators can inspect active paper brackets, tighten protective stops, and cancel active paper brackets by signal ID:

```powershell
Invoke-RestMethod http://127.0.0.1:8004/brackets

Invoke-RestMethod http://127.0.0.1:8004/brackets/btc-breakout-001

Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8004/brackets/btc-breakout-001/preview -ContentType "application/json" -Body '{
  "price": "50600"
}'

Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8004/brackets/btc-breakout-001/stop -ContentType "application/json" -Body '{
  "trigger_price": "50250",
  "reason": "manual support moved higher"
}'

Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8004/brackets/btc-breakout-001/cancel -ContentType "application/json" -Body '{
  "reason": "operator replaced exit plan manually"
}'
```

`POST /brackets/{signal_id}/preview` is signal-specific and paper-only. It runs the hypothetical mark against a deep copy of the selected paper bracket, returns only exits that would trigger for that bracket, and includes `distance_to_trigger`, `distance_to_trigger_pct`, and `trailing_activation_price` in the active-exit snapshot. It does not create orders, update P&L, record audit events, or mutate trailing stops on the active engine.

Stop amendments are paper-only bracket maintenance events. A long bracket stop can only move upward, and a short bracket stop can only move downward. Attempts to loosen the stop return `409` and leave the bracket unchanged. Successful amendments record a synthetic `bracket_stop_amend` paper order plus a `bracket.stop_amended` audit event when SQLite persistence is configured, and those amendments replay after restart.

Bracket cancellation records a synthetic `bracket_cancel` paper order plus a `bracket.canceled` audit event when SQLite persistence is configured. It removes the active stop-loss, take-profit, and trailing-stop legs for that paper lot, but it does not close the open paper exposure. The operator must submit a separate reduce-only or manual close order when the position itself should be closed.

Use `take_profit_targets` for staged exits. Each target accepts either `pct` or `trigger_price` plus `close_pct`, and the total `close_pct` cannot exceed `100`. For example, `[{ "pct": "3", "close_pct": "50" }, { "trigger_price": "53000", "close_pct": "50" }]` sells half of the original paper lot at 3% profit and the remaining half at the exact trigger price. Long absolute targets must sit above entry and short absolute targets must sit below entry; risk checks reject the whole signal if any staged target is inverted. If the first target fills and price later falls to the stop, the remaining paper quantity exits through the stop-loss or trailing-stop leg. If `take_profit_targets` is omitted, `take_profit_pct` or `take_profit_price` still creates one full-size take-profit target.

Bracket fields may be sent at the top level or grouped under `bracket`, `bracket_order`, or `exit_plan`. Top-level values win if both are present:

```json
{
  "symbol": "ETHUSDT",
  "side": "short",
  "quote_amount": "100",
  "price": "100",
  "bracket": {
    "stop_loss_pct": "5",
    "take_profit_pct": "10",
    "trailing_stop_pct": "3",
    "trailing_activation_pct": "2",
    "breakeven_trigger_pct": "1.5"
  }
}
```

## Text Crypto Alerts

The text parser is intentionally strict so Discord-style alerts are explicit and auditable.

Supported examples:

```text
BUY BTCUSDT $125 @ 50000 SL 2.5% TP 5% TRAIL 3% ACT 2% BE 2%
BUY BTCUSDT $125 @ 50000 SL @ 49000 TP @ 51500 TRAIL 3%
BUY BTCUSDT $125 @ 50000 SL 2% TP1 3% 50% TP2 @ 53000 50%
BUY SOLUSDT $50 @ 150 SL 3% TP 8% TS 4% BE 3%
SELL ETH/USDT 0.25 @ 3000
SHORT ETHUSDT $75 @ 3000 SL 2% TP 4% TRAIL 3% ACT 1%
```

Text alerts support percentage brackets (`SL 2%`, `TP 5%`), absolute brackets (`SL @ 49000`, `TP @ 51500`), and staged take-profit targets such as `TP1 3% 50% TP2 @ 53000 50%`. Staged text targets map to the same `take_profit_targets` structure as JSON alerts, and the parser rejects ambiguous prose rather than guessing.

Validate text without placing an order:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8004/signals/parse-text -ContentType "application/json" -Body '{
  "message": "BUY SOLUSDT $50 @ 150 SL 3% TP 8% TRAIL 4% BE 3%"
}'
```

Run parsed text through the normal duplicate, risk, approval, and paper execution path:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8004/webhooks/text-alert -ContentType "application/json" -Body '{
  "message": "BUY SOLUSDT $50 @ 150 SL 3% TP 8% TRAIL 4% BE 3%"
}'
```

## Signal Schema

Required fields:

- `symbol`, `ticker`, or `pair`
- `side` or `action`
- `quote_amount`, `notional`, `base_amount`, `quantity`, `qty`, `risk_amount`, or `risk_pct`

Recommended fields:

- `signal_id`
- `price`, `entry_price`, or `limit_price`
- `risk_amount` to size the paper notional from a fixed quote-currency risk budget and stop distance
- `risk_pct` to size the paper notional from account equity percent and stop distance
- `stop_loss_pct`
- `stop_loss_price` or `stop_price`
- `take_profit_pct`
- `take_profit_price` or `target_price`
- `take_profit_targets` as a list of `{ "pct": "...", "close_pct": "..." }` or `{ "trigger_price": "...", "close_pct": "..." }` objects for staged exits
- `trailing_stop_pct`
- `trailing_activation_pct` or `trail_activation_pct`
- `breakeven_trigger_pct`
- `bracket`, `bracket_order`, or `exit_plan` object containing the same stop-loss, take-profit, trailing-stop, and break-even fields
- `max_slippage_bps`
- `reduce_only`
- `strategy_id` or `strategy`
- `exchange` or `venue`
- `market_type`

Forbidden actions include `withdraw`, `transfer`, `internal_transfer`, and `deposit`.

## Risk Controls

Risk checks run before paper execution:

- `stop_loss_required`
- `max_order_notional_exceeded`
- `max_open_notional_exceeded`
- `max_position_equity_pct_exceeded`
- `max_risk_per_trade_pct_exceeded`
- `max_leverage_exceeded`
- `max_slippage_exceeded`
- `consecutive_loss_limit_exceeded`
- `max_stop_loss_pct_exceeded`
- `max_trailing_stop_pct_exceeded`
- `trailing_stop_required_for_activation`
- `breakeven_requires_protective_exit`
- `min_reward_risk_ratio_not_met`
- `invalid_stop_loss_price`
- `invalid_take_profit_price`
- `price_required_for_stop_loss_price`
- `price_required_for_take_profit_price`
- `exchange_not_allowed`
- `symbol_not_allowed`
- `symbol_blocked`
- `daily_loss_limit_exceeded`
- `price_required_for_base_amount`
- `risk_sizing_requires_stop_loss`

Set `AUTO_CRYPTO_MAX_OPEN_NOTIONAL` above `0` to cap cumulative open long plus short paper exposure. Set `AUTO_CRYPTO_MAX_POSITION_EQUITY_PCT` above `0` to limit a single ticket to a percentage of account equity. Set `AUTO_CRYPTO_MAX_RISK_PER_TRADE_PCT` above `0` to cap `risk_pct` sizing requests. Set `AUTO_CRYPTO_MAX_STOP_LOSS_PCT`, `AUTO_CRYPTO_MAX_TRAILING_STOP_PCT`, and `AUTO_CRYPTO_MIN_REWARD_RISK_RATIO` above `0` to reject alerts whose fixed stop or trailing stop is too wide or whose take-profit does not justify the stop risk. Absolute `stop_loss_price` and `take_profit_price` values are converted to entry-relative percentages for those same checks, and every staged absolute take-profit target is checked for the correct side of entry. Set `AUTO_CRYPTO_MAX_CONSECUTIVE_LOSSES` above `0` to pause new entries after repeated losing bracket exits. SQLite-backed paper state restores open exposure after restart, and triggered paper exits release exposure for later risk checks.

When `quote_amount` and `base_amount` are omitted, JSON signals may set `risk_amount` or `risk_pct` with a stop loss. Auto-Crypto computes the paper order notional as `risk budget / stop distance`. For example, `risk_pct: "1"` with `equity: 10000` and `stop_loss_pct: "5"` sizes a `2000` paper notional so the stop represents about `100` quote currency of paper risk before slippage.

## Research Notes

Current bot work is guided by paper-first risk controls and exchange order behavior:

- Binance documents spot trailing stops as dynamic contingent orders that track favorable price movement and trigger after a configured reversal delta; it also allows an optional stop price before tracking begins, which maps to Auto-Crypto's paper `trailing_activation_pct`: <https://developers.binance.com/docs/binance-spot-api-docs/faqs/trailing-stop-faq>
- Binance.US currently describes trailing stops as stops whose trigger price follows favorable market movement and fires when the market moves against the position, matching Auto-Crypto's high-water and low-water paper trailing logic: <https://support.binance.us/en/articles/9842886-trailing-stop-orders-what-they-are-and-how-to-use-them>
- Binance order payloads expose trailing-stop fields such as `trailingDelta` and `trailingTime`, which is useful when mapping paper behavior to future live adapters: <https://developers.binance.com/docs/binance-spot-api-docs/rest-api/trading-endpoints>
- Coinbase describes bracket and TP/SL orders as linked exits where only the triggered side executes and the other side is turned off, which is the behavior Auto-Crypto mirrors in paper bracket lots: <https://help.coinbase.com/en/coinbase/trading-and-funding/advanced-trade/order-types>
- Coinbase Advanced Trade API attached TP/SL order examples use explicit stop and limit trigger prices, so Auto-Crypto accepts `stop_loss_price`, `take_profit_price`, and staged `trigger_price` values in addition to percentage offsets: <https://docs.cdp.coinbase.com/coinbase-app/advanced-trade-apis/guides/orders>
- Kraken Pro documents TP/SL bracket orders for spot markets but notes they are not available with trailing stop order types, reinforcing why Auto-Crypto keeps bracket/trailing combinations as explicit paper simulation until venue-specific live capabilities are mapped: <https://support.kraken.com/articles/bracket-orders-on-kraken-pro>
- Interactive Brokers describes bracket orders as an entry plus opposite-side profit-taking and stop-loss children where the unfilled child is canceled after one side triggers; Auto-Crypto's synthetic OCA metadata follows that paper-first model: <https://www.interactivebrokers.com/campus/trading-lessons/bracket-orders-for-tws-mosaic-2/>
- Binance documents that OCO orders can include a trailing-stop contingent leg and that triggering it cancels the paired limit leg, which is why Auto-Crypto now stores OCA grouping and canceled sibling metadata in paper exit orders before any live adapter work: <https://developers.binance.com/docs/binance-spot-api-docs/faqs/trailing-stop-faq>
- CCXT notes that trailing orders and stop/take-profit parameters vary by exchange, so Auto-Crypto keeps exchange-specific live execution disabled and paper-first until adapter capability checks are explicit: <https://docs.ccxt.com/docs/faq>
- CCXT documents take-profit and stop-loss orders as closing orders for an existing position, including trigger prices and an inverted side when closing a sell/short position; Auto-Crypto mirrors that by using paper buy exits for short bracket lots: <https://github.com/ccxt/ccxt/wiki/manual>
- Coinbase describes derivatives TP/SL bracket exits as reduce-only orders, so Auto-Crypto marks synthetic paper bracket exits `reduce_only` even though no live exchange order is sent: <https://help.coinbase.com/coinbase/derivatives/bracket-orders>
- CCXT's trailing-order FAQ calls out `reduceOnly` as an exchange-dependent way to close rather than open exposure; Auto-Crypto supports paper `reduce_only` and `close_short` intents while keeping live execution disabled: <https://docs.ccxt.com/docs/faq>
- CCXT's order FAQ also recommends checking exchange feature flags for native take-profit and stop-loss support; this is why staged TP/SL simulation is recorded as paper behavior instead of assuming a portable live bracket implementation: <https://github.com/ccxt/ccxt/wiki/FAQ/9e4963a7b3438ba4fee47be1ec6922f4baf6684e>
- Bot setting guidance consistently emphasizes stop loss, take profit, demo/paper testing, backtesting, and position sizing before live automation: <https://bitsgap.com/blog/how-to-choose-crypto-trading-bot-settings-in-2026-range-investment-stop-loss-and-take-profit>
- Recent crypto-bot risk guidance highlights fixed-fraction sizing, commonly around 1-2% per trade, plus stop-loss and drawdown limits before live automation; Auto-Crypto's `risk_pct` sizing stays paper-only and can be capped with `AUTO_CRYPTO_MAX_RISK_PER_TRADE_PCT`: <https://cryptorobot.ai/blog/essential-tips-managing-risks-crypto-trading-bots>
- Current crypto backtesting guidance emphasizes testing strategies on historical or simulated price paths before launch; Auto-Crypto's `/backtest/signal` endpoint applies that idea to bracket and trailing-stop paper logic without mutating active state: <https://bitsgap.com/blog/crypto-backtesting-guide-2025-tools-tips-and-how-bitsgap-helps>

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
AUTO_CRYPTO_MAX_POSITION_EQUITY_PCT=0
AUTO_CRYPTO_MAX_RISK_PER_TRADE_PCT=0
AUTO_CRYPTO_MAX_LEVERAGE=1
AUTO_CRYPTO_MAX_DAILY_LOSS=500
AUTO_CRYPTO_MAX_CONSECUTIVE_LOSSES=0
AUTO_CRYPTO_MAX_SLIPPAGE_BPS=100
AUTO_CRYPTO_REQUIRE_STOP_LOSS=true
AUTO_CRYPTO_MAX_STOP_LOSS_PCT=0
AUTO_CRYPTO_MAX_TRAILING_STOP_PCT=0
AUTO_CRYPTO_MIN_REWARD_RISK_RATIO=0

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
- `POST /signals/preview-text`
- `POST /signals/preview`
- `POST /signals/submit-text`
- `POST /signals/submit`
- `POST /backtest/signal`

Paper market updates:

- `POST /market/price`
- `POST /market/price/preview`
- `GET /brackets`
- `GET /brackets/{signal_id}`
- `POST /brackets/{signal_id}/preview`
- `POST /brackets/{signal_id}/stop`
- `POST /brackets/{signal_id}/cancel`

`POST /signals/preview` and `POST /signals/preview-text` return a `bracket_plan` object with the synthetic entry side, exit side, OCA group, trailing arming state, trailing activation price, stop/take-profit/trailing triggers, estimated notional and quantity, worst-case stop loss, equity risk percent, and first-target reward/risk estimate that would apply if the signal were submitted.

`POST /backtest/signal` accepts a `signal` object plus a `prices` list, runs the signal through an isolated paper engine, marks each supplied price, and returns triggered exits, active exit snapshots after each mark, final paper P&L, final open notional, and final positions. It does not save orders, write audit events, or mutate the active engine.

`POST /market/price/preview` returns the paper exits that would trigger at a hypothetical mark without mutating orders, positions, audit history, daily P&L, or exposure. Use it before applying a mark when testing bracket and trailing-stop behavior.

`POST /market/price` applies the mark, returns any triggered exits, refreshes account open notional through the trading engine, and returns the current `active_exits` snapshot, including ratcheted trailing-stop trigger prices, activation state, activation price, trigger distance, and water marks.

`GET /brackets` returns active synthetic paper brackets grouped by signal. `GET /brackets/{signal_id}` returns active synthetic paper exit legs for one signal. `POST /brackets/{signal_id}/preview` previews only that bracket against a mark without mutating active state. `POST /brackets/{signal_id}/stop` tightens a paper stop without loosening risk, persists an amendment order, and records audit context. `POST /brackets/{signal_id}/cancel` removes those synthetic exits, persists a cancellation order, and records audit context without closing the position.

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
- `GET /exchanges/platforms`
- `GET /exchanges/paper/capabilities`
- `GET /exchanges/{exchange_id}/capabilities`
- `GET /exchanges/{exchange_id}/integration`

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

## Supported Platform Registry

`GET /exchanges/platforms` returns the curated integration backlog and readiness state for all high-priority Bitcoin venues. Each row includes driver type, CCXT mapping when available, market types, API coverage, credential-field status, documentation URL, and live-execution gate state.

Current platforms:

| Platform | Exchange ID | Adapter path | Markets |
| --- | --- | --- | --- |
| Coinbase Advanced Trade | `coinbase` | CCXT now, native planned | spot, derivatives |
| Kraken | `kraken` | CCXT | spot, margin, futures |
| Gemini | `gemini` | CCXT | spot |
| Bitstamp | `bitstamp` | CCXT | spot |
| Binance.US | `binanceus` | CCXT | spot, OTC |
| Alpaca Crypto | `alpaca` | CCXT, native broker adapter planned | spot |
| Robinhood Crypto | `robinhood` | native broker adapter planned | spot |
| Crypto.com Exchange | `cryptocom` | CCXT | spot, margin, derivatives |
| OKX | `okx` | CCXT | spot, margin, swaps, futures, options |
| Bybit | `bybit` | CCXT | spot, swaps, futures, options |
| KuCoin | `kucoin` | CCXT | spot, margin, futures |
| Bitget | `bitget` | CCXT | spot, margin, swaps, futures |
| Gate.io | `gateio` | CCXT | spot, margin, swaps, futures, options |
| MEXC | `mexc` | CCXT | spot, swaps, futures |
| Phemex | `phemex` | CCXT | spot, contracts |
| BitMEX | `bitmex` | CCXT | swaps, futures |
| Deribit | `deribit` | CCXT | options, futures, swaps |
| Bitunix | `bitunix` | native REST adapter | spot, swaps, futures |

Example checks:

```powershell
Invoke-RestMethod http://127.0.0.1:8004/exchanges/platforms
Invoke-RestMethod http://127.0.0.1:8004/exchanges/deribit/integration
Invoke-RestMethod http://127.0.0.1:8004/exchanges/bitmex/integration
Invoke-RestMethod http://127.0.0.1:8004/exchanges/coinbase/integration
```

Use `.[exchange]` dependencies to let CCXT-backed platforms report `adapter_ready`. A native Robinhood adapter and richer native Alpaca broker flows require separate request-signing implementations before live execution can be considered.

## Bitunix Integration

Auto-Crypto includes a native Bitunix adapter for futures market data, credential validation, and capability reporting. Live Bitunix execution remains disabled by default.

Configure credentials in a local `.env` file or environment variables. Do not commit real keys.

```powershell
AUTO_CRYPTO_BITUNIX_API_KEY=replace-with-your-key
AUTO_CRYPTO_BITUNIX_SECRET_KEY=replace-with-your-secret
AUTO_CRYPTO_BITUNIX_LIVE_ENABLED=false
```

Useful checks:

```powershell
Invoke-RestMethod http://127.0.0.1:8004/exchanges
Invoke-RestMethod http://127.0.0.1:8004/exchanges/bitunix/capabilities
Invoke-RestMethod "http://127.0.0.1:8004/exchanges/bitunix/futures/tickers?symbols=BTCUSDT,ETHUSDT"
Invoke-RestMethod "http://127.0.0.1:8004/exchanges/bitunix/futures/account?margin_coin=USDT"
```

The private account check signs the request using Bitunix's required `api-key`, `nonce`, `timestamp`, and `sign` headers. The bot never returns or logs the secret key.

## Persistence

When SQLite is enabled:

- Signal IDs are claimed with insert-only idempotency writes.
- Duplicate signal IDs after restart return `status=duplicate`.
- Pending approvals persist across restarts.
- Paper orders are replayed at startup to restore positions, active bracket lots, and open exposure.
- Paper price exits are saved as synthetic sell orders.
- Paper bracket cancellation orders are replayed at startup so removed synthetic exits do not reappear after restart.

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
