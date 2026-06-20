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
- Applies pre-trade risk checks for stop loss, maximum stop width, first-target and total staged reward/risk, staged target count, max order notional, max open notional, equity-percent position size, leverage, slippage, allowed exchanges, blocked symbols, daily loss, and consecutive losing exits
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
- Supports paper trailing stops by percentage callback or fixed quote-distance amount, plus either percentage or absolute-price activation gates
- Supports paper trailing-step controls so trailing stops only ratchet after a minimum trigger improvement instead of every favorable tick
- Supports optional paper breakeven-after-take-profit brackets so the remaining stop/trailing legs lock at entry after a staged target fills
- Supports paper time-stop exits with `max_hold_marks`/`time_stop_marks` so a bracket can close after a fixed number of market-price marks when no price exit fired
- Lists all active synthetic paper brackets with remaining-notional, worst-case stop-loss, and first-target reward summaries
- Supports protective stop, trailing-stop, take-profit, and manual breakeven amendments; protective exits only tighten risk and take-profit targets only move farther into profit
- Supports paper-only bracket close-by-signal controls that flatten or partially reduce the selected simulated bracket at an operator-supplied mark or at the current nearest protective trigger
- Previews one active bracket by signal ID at a hypothetical mark, including trigger distance and trailing activation context
- Cancels active synthetic paper bracket exits by signal ID while leaving the underlying paper position open for separate manual management
- Previews hypothetical market-price marks and bracket/trailing exits without mutating paper orders or positions
- Previews server-side risk decisions from the operator UI without placing orders
- Backtests one signal against a supplied paper mark-price path and returns active exit snapshots after each mark without mutating the live in-memory engine
- Backtests one signal against OHLC candles with conservative adverse-first intrabar sequencing, plus MFE/MAE excursion metrics, without mutating active state
- Backtests can opt into paper fee and slippage assumptions so bracket/trailing-stop results are not limited to clean mark-price fills
- Shows persisted signal history with one-click reload into the Trading Desk
- Supports quote-notional and base-quantity ticket sizing, paper position close controls, bracket lot context, bracket previews, stop tightening, breakeven locks, bracket cancellation, trigger tests, and local unrealized P&L marks in the operator UI
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
  "trailing_stop_price": "48750",
  "trailing_activation_price": "50750",
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

For buy brackets, `stop_loss_pct` creates a fixed protective sell below entry, `take_profit_pct` creates a fixed profit-taking sell above entry, and `trailing_stop_pct` creates a sell stop that starts below entry and ratchets upward when `POST /market/price` marks a new high-water price. Use `trailing_stop_amount` or `trail_amount` when the trail should stay a fixed quote-currency distance behind the high-water mark instead of a percentage. Use `stop_loss_price`, `take_profit_price`, and `trailing_stop_price` when the alert source already calculated exact initial trigger prices. `trailing_stop_price` sets only the starting paper trigger; `trailing_stop_pct` or `trailing_stop_amount` still controls the ratchet distance after favorable marks. The trailing stop never moves lower. Add `trailing_step_pct`, `trail_step_pct`, `trailing_step_amount`, or `trail_step_amount` to require the next synthetic trailing trigger to improve by at least that percentage or quote-currency distance before the paper stop ratchets. Add `trailing_activation_pct` to keep the trailing leg dormant until price has moved favorably by that percent, or `trailing_activation_price`, `trail_activation_price`, or `activation_price` to arm it at an exact favorable mark. Add `breakeven_trigger_pct` to move protective stop exits up to the entry price after a favorable move. Add `breakeven_after_take_profit: true` when staged take-profit fills should automatically move remaining protective stop-loss and trailing-stop legs to the paper entry price. Add `max_hold_marks` or `time_stop_marks` to close the paper bracket at the current mark after that many `POST /market/price` updates if stop-loss, take-profit, and trailing-stop exits have not already closed it.

For short brackets, send `side: "sell"` or `side: "short"` with at least one exit field such as `stop_loss_pct`, `stop_loss_price`, `take_profit_pct`, `take_profit_price`, `trailing_stop_pct`, `trailing_stop_amount`, or `max_hold_marks`. Paper stop-loss and trailing-stop triggers sit above entry, paper take-profit triggers sit below entry, time stops close at the current paper mark, and exit orders buy back paper quantity. Short trailing stops track a low-water price and ratchet downward only after favorable price movement; `trailing_activation_pct` delays arming until price falls by that percent, while `trailing_activation_price` arms at an exact lower mark. A plain `SELL` without bracket fields remains a manual long close. Send `side: "close_short"` or `reduce_only: true` with `side: "buy"` to buy back paper short quantity without opening a new paper long.

Every paper bracket leg now carries an `oca_group` and `status` in order JSON and active-exit snapshots. Activation-gated trailing stops start as `pending_activation`, move to `open` when the favorable activation mark is reached, and are recorded as `filled` on the synthetic paper exit order that closes quantity. When a stop-loss, trailing-stop, or final take-profit closes the remaining paper lot, the synthetic exit order is marked `reduce_only: true` and includes `exit_kind`, the filled exit leg, plus `canceled_exit_orders` so tests and operators can see which sibling legs would have been canceled in one-cancels-other behavior. This is still paper accounting only; no live OCO or exchange-native bracket order is submitted.

Paper time stops appear as `time_exit` legs with `status: "waiting"` in previews, bracket listings, and backtest snapshots. Active snapshots include `max_hold_marks`, `marks_seen`, and `marks_remaining`. When the count reaches zero, Auto-Crypto records a reduce-only `time_exit` close order at the supplied mark and cancels sibling synthetic exits in the same paper OCA group. This is intended for backtesting and operator review of stale-entry rules; it does not schedule a wall-clock order or submit live exchange instructions.

Operators can inspect active paper brackets, tighten protective stops or trailing-stop triggers, move take-profit targets farther into profit, move protective exits to breakeven, close a bracket at an operator-supplied paper mark or at its current nearest protective trigger, and cancel active paper brackets by signal ID:

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

Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8004/brackets/btc-breakout-001/trailing-stop -ContentType "application/json" -Body '{
  "trigger_price": "52100",
  "reason": "operator tightened trailing trigger after new support formed"
}'

Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8004/brackets/btc-breakout-001/take-profit -ContentType "application/json" -Body '{
  "trigger_price": "54200",
  "reason": "operator raised paper target after breakout confirmed"
}'

Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8004/brackets/btc-breakout-001/breakeven -ContentType "application/json" -Body '{
  "reason": "operator locked remaining paper risk at entry"
}'

Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8004/brackets/btc-breakout-001/close -ContentType "application/json" -Body '{
  "price": "52400",
  "close_pct": "50",
  "reason": "operator reduced paper bracket before event risk"
}'

Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8004/brackets/btc-breakout-001/close-protective -ContentType "application/json" -Body '{
  "reason": "operator accepted the current paper protective trigger"
}'

Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8004/brackets/btc-breakout-001/cancel -ContentType "application/json" -Body '{
  "reason": "operator replaced exit plan manually"
}'
```

`POST /brackets/{signal_id}/preview` is signal-specific and paper-only. It runs the hypothetical mark against a deep copy of the selected paper bracket, returns only exits that would trigger for that bracket, and includes `distance_to_trigger`, `distance_to_trigger_pct`, and `trailing_activation_price` in the active-exit snapshot. It does not create orders, update P&L, record audit events, or mutate trailing stops on the active engine.

Stop amendments are paper-only bracket maintenance events. A long bracket stop can only move upward, and a short bracket stop can only move downward. Attempts to loosen the stop return `409` and leave the bracket unchanged. Successful amendments record a synthetic `bracket_stop_amend` paper order plus a `bracket.stop_amended` audit event when SQLite persistence is configured, and those amendments replay after restart.

Trailing-stop amendments are also paper-only bracket maintenance events. A long trailing trigger can only move upward, and a short trailing trigger can only move downward. A successful `POST /brackets/{signal_id}/trailing-stop` amendment records a synthetic `bracket_trailing_stop_amend` paper order, marks the trailing leg `open`, syncs the paper water mark so later trailing updates do not loosen it, records a `bracket.trailing_stop_amended` audit event when SQLite persistence is configured, and replays after restart. This is an operator override for simulated trailing exits only; it does not submit or modify any live exchange order.

Take-profit amendments are paper-only bracket maintenance events. A long target can only move upward, and a short target can only move downward, so amendments cannot reduce the projected reward after approval. `POST /brackets/{signal_id}/take-profit` defaults to the first still-open target and accepts optional `target_index` for staged brackets. Successful amendments record a synthetic `bracket_take_profit_amend` paper order plus `bracket.take_profit_amended` audit event when SQLite persistence is configured, and replay after restart.

Breakeven amendments are paper-only bracket maintenance events. `POST /brackets/{signal_id}/breakeven` moves open stop-loss and trailing-stop legs to the entry price when doing so tightens risk, records a synthetic `bracket_breakeven` order plus `bracket.breakeven_amended` audit event when SQLite persistence is configured, and replays after restart. If the protective exits are already at or beyond breakeven, the API returns `409` and leaves the bracket unchanged.

Bracket cancellation records a synthetic `bracket_cancel` paper order plus a `bracket.canceled` audit event when SQLite persistence is configured. It removes the active stop-loss, take-profit, and trailing-stop legs for that paper lot, including trailing stops still waiting in `pending_activation`, but it does not close the open paper exposure. The operator must submit a separate reduce-only or manual close order when the position itself should be closed.

Bracket close records a synthetic `bracket_manual_close` or `bracket_manual_reduce` paper order plus a `bracket.closed` audit event when SQLite persistence is configured. `POST /brackets/{signal_id}/close` requires `price` or `mark_price`; send optional `close_pct` or `base_amount` to reduce only part of the remaining bracket quantity. Full closes cancel remaining synthetic exits, while partial reductions keep the remaining stop-loss, take-profit, and trailing-stop legs active for the reduced paper lot. The endpoint uses the existing reduce-only long/short lot accounting, updates daily paper P&L and exposure, and replays after restart. This is still a simulated close at an operator-supplied mark; it does not submit a live market order.

`POST /brackets/{signal_id}/close-protective` uses the selected bracket's current nearest protective stop-loss or trailing-stop trigger as the paper close mark, then follows the same reduce-only accounting, optional `close_pct`/`base_amount` handling, and restart replay behavior as manual bracket close. It records `bracket.protective_closed` in the audit log when SQLite persistence is configured. This is useful when an operator accepts the current protective exit plan without typing a separate mark; it still does not send a live order.

The Portfolio Sentinel `Bracket Ledger` exposes those same paper-only controls in the UI. `Preview` calls `POST /brackets/{signal_id}/preview` at the selected exit trigger, `Tighten Stop` prompts for a new protective stop and calls `POST /brackets/{signal_id}/stop`, `Tighten Trail` calls `POST /brackets/{signal_id}/trailing-stop`, `Raise TP` calls `POST /brackets/{signal_id}/take-profit`, `Breakeven` calls `POST /brackets/{signal_id}/breakeven`, `Close at Protective` calls `POST /brackets/{signal_id}/close-protective`, `Cancel Bracket` removes synthetic exits without closing the paper position, and `Trigger` still applies the mark through `POST /market/price`. These controls are operator maintenance tools for simulated brackets; they do not place live exchange orders.

Use `take_profit_targets` for staged exits. Each target accepts either `pct` or `trigger_price` plus `close_pct`, and the total `close_pct` cannot exceed `100`. For example, `[{ "pct": "3", "close_pct": "50" }, { "trigger_price": "53000", "close_pct": "50" }]` sells half of the original paper lot at 3% profit and the remaining half at the exact trigger price. Long absolute targets must sit above entry and short absolute targets must sit below entry; risk checks reject the whole signal if any staged target is inverted. If configured, `AUTO_CRYPTO_MAX_TAKE_PROFIT_TARGETS` rejects target lists that are too long, and `AUTO_CRYPTO_MIN_TOTAL_REWARD_RISK_RATIO` evaluates the weighted staged reward/risk across all targets. If the first target fills and price later falls to the stop, the remaining paper quantity exits through the stop-loss or trailing-stop leg. If `breakeven_after_take_profit` is true, a partial take-profit fill also tightens remaining stop-loss and trailing-stop legs to entry when that reduces paper risk. If `take_profit_targets` is omitted, `take_profit_pct` or `take_profit_price` still creates one full-size take-profit target.

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
    "trailing_stop_amount": "4",
    "trailing_activation_price": "96",
    "breakeven_trigger_pct": "1.5",
    "breakeven_after_take_profit": true
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
BUY BTCUSDT $100 @ 50000 SL 2% TP1 4% 50% TP2 8% 50% TRAIL 4% BEAFTERTP
BUY SOLUSDT $50 @ 150 SL 3% TP 8% TS 4% BE 3%
SELL ETH/USDT 0.25 @ 3000
SHORT ETHUSDT $75 @ 3000 SL 2% TP 4% TRAIL 3% ACT 1%
```

Text alerts support percentage brackets (`SL 2%`, `TP 5%`), absolute brackets (`SL @ 49000`, `TP @ 51500`), trailing steps (`TRAIL 4% STEP 1%`), breakeven triggers (`BE 2%`), breakeven-after-target locks (`BEAFTERTP`), and staged take-profit targets such as `TP1 3% 50% TP2 @ 53000 50%`. Staged text targets map to the same `take_profit_targets` structure as JSON alerts, and the parser rejects ambiguous prose rather than guessing.

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
- `trailing_stop_amount` or `trail_amount` to trail by a fixed quote-currency distance instead of a percent
- `trailing_stop_price` or `trail_price` to set the exact initial paper trailing trigger while `trailing_stop_pct` or `trailing_stop_amount` controls later ratchets
- `trailing_step_pct`, `trail_step_pct`, `trailing_step_amount`, or `trail_step_amount` to reduce trailing-stop churn by requiring a minimum trigger improvement before ratcheting
- `trailing_activation_pct` or `trail_activation_pct`
- `trailing_activation_price`, `trail_activation_price`, or `activation_price`
- `breakeven_trigger_pct`
- `breakeven_after_take_profit` or `move_stop_to_breakeven_after_tp`
- `max_hold_marks` or `time_stop_marks` to close a paper bracket after a fixed count of market-price marks
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
- `trailing_stop_required_for_step`
- `trailing_stop_pct_required_for_price`
- `invalid_trailing_stop_price`
- `price_required_for_trailing_stop_price`
- `invalid_trailing_activation_price`
- `price_required_for_trailing_activation_price`
- `duplicate_trailing_activation`
- `price_required_for_trailing_stop_amount`
- `breakeven_requires_protective_exit`
- `breakeven_after_take_profit_requires_take_profit`
- `min_reward_risk_ratio_not_met`
- `min_total_reward_risk_ratio_not_met`
- `max_take_profit_targets_exceeded`
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

Set `AUTO_CRYPTO_MAX_OPEN_NOTIONAL` above `0` to cap cumulative open long plus short paper exposure. Set `AUTO_CRYPTO_MAX_POSITION_EQUITY_PCT` above `0` to limit a single ticket to a percentage of account equity. Set `AUTO_CRYPTO_MAX_RISK_PER_TRADE_PCT` above `0` to cap `risk_pct` sizing requests. Set `AUTO_CRYPTO_MAX_STOP_LOSS_PCT`, `AUTO_CRYPTO_MAX_TRAILING_STOP_PCT`, and `AUTO_CRYPTO_MIN_REWARD_RISK_RATIO` above `0` to reject alerts whose fixed stop or trailing stop is too wide or whose first take-profit does not justify the stop risk. Set `AUTO_CRYPTO_MIN_TOTAL_REWARD_RISK_RATIO` above `0` to evaluate the weighted reward/risk of every staged take-profit target by `close_pct`, and set `AUTO_CRYPTO_MAX_TAKE_PROFIT_TARGETS` above `0` to cap overly complex staged brackets. Absolute `stop_loss_price`, `take_profit_price`, and fixed `trailing_stop_amount` values are converted to entry-relative percentages for those same checks, and every staged absolute take-profit target is checked for the correct side of entry. Set `AUTO_CRYPTO_MAX_CONSECUTIVE_LOSSES` above `0` to pause new entries after repeated losing bracket exits. SQLite-backed paper state restores open exposure after restart, and triggered paper exits release exposure for later risk checks.

When `quote_amount` and `base_amount` are omitted, JSON signals may set `risk_amount` or `risk_pct` with a stop loss. Auto-Crypto computes the paper order notional as `risk budget / stop distance`. For example, `risk_pct: "1"` with `equity: 10000` and `stop_loss_pct: "5"` sizes a `2000` paper notional so the stop represents about `100` quote currency of paper risk before slippage.

## Research Notes

Current bot work is guided by paper-first risk controls and exchange order behavior:

- Binance documents spot trailing stops as dynamic contingent orders that track favorable price movement and trigger after a configured reversal delta; it also allows an optional stop price before tracking begins, which maps to Auto-Crypto's paper `trailing_activation_pct` and `trailing_activation_price`. Binance also documents per-symbol trailing-delta filters, which is why Auto-Crypto now supports paper `trailing_step_pct` and `trailing_step_amount` to avoid unrealistic tick-by-tick ratchets in simulations: <https://developers.binance.com/docs/binance-spot-api-docs/faqs/trailing-stop-faq>
- Binance Futures describes trailing stop orders as requiring both an activation condition and a callback-rate reversal condition before a market order is issued, matching Auto-Crypto's paper activation-plus-ratchet model: <https://www.binance.com/en/support/faq/detail/360042299292>
- Binance.US currently describes trailing stops as stops whose trigger price follows favorable market movement and fires when the market moves against the position, matching Auto-Crypto's high-water and low-water paper trailing logic: <https://support.binance.us/en/articles/9842886-trailing-stop-orders-what-they-are-and-how-to-use-them>
- Binance order payloads expose trailing-stop fields such as `trailingDelta` and `trailingTime`, which is useful when mapping paper behavior to future live adapters: <https://developers.binance.com/docs/binance-spot-api-docs/rest-api/trading-endpoints>
- Coinbase describes bracket and TP/SL orders as linked exits where only the triggered side executes and the other side is turned off, which is the behavior Auto-Crypto mirrors in paper bracket lots: <https://help.coinbase.com/en/coinbase/trading-and-funding/advanced-trade/order-types>
- Coinbase Advanced Trade API attached TP/SL order examples use explicit stop and limit trigger prices, so Auto-Crypto accepts `stop_loss_price`, `take_profit_price`, and staged `trigger_price` values in addition to percentage offsets: <https://docs.cdp.coinbase.com/coinbase-app/advanced-trade-apis/guides/orders>
- Freqtrade documents keeping a static stop until a favorable offset is reached and then trailing the stop, while ignoring stop changes that would loosen risk; Auto-Crypto mirrors that with `trailing_activation_pct`, `trailing_activation_price`, and protective-only amendments: <https://www.freqtrade.io/en/stable/stoploss/>
- Kraken Pro documents TP/SL bracket orders for spot markets but notes they are not available with trailing stop order types, reinforcing why Auto-Crypto keeps bracket/trailing combinations as explicit paper simulation until venue-specific live capabilities are mapped: <https://support.kraken.com/articles/bracket-orders-on-kraken-pro>
- Interactive Brokers describes bracket orders as an entry plus opposite-side profit-taking and stop-loss children where the unfilled child is canceled after one side triggers; Auto-Crypto's synthetic OCA metadata follows that paper-first model: <https://www.interactivebrokers.com/campus/trading-lessons/bracket-orders-for-tws-mosaic-2/>
- Binance Academy's current OCO explainer describes paired limit and stop-limit exits where only one side executes and the other is canceled, which supports keeping Auto-Crypto's staged TP plus protective-stop behavior explicit and auditable in paper mode: <https://www.binance.com/en/academy/glossary/oco-order>
- Binance documents that OCO orders can include a trailing-stop contingent leg and that triggering it cancels the paired limit leg, which is why Auto-Crypto now stores OCA grouping and canceled sibling metadata in paper exit orders before any live adapter work: <https://developers.binance.com/docs/binance-spot-api-docs/faqs/trailing-stop-faq>
- CCXT notes that trailing orders and stop/take-profit parameters vary by exchange, so Auto-Crypto keeps exchange-specific live execution disabled and paper-first until adapter capability checks are explicit: <https://docs.ccxt.com/docs/faq>
- CCXT documents take-profit and stop-loss orders as closing orders for an existing position, including trigger prices and an inverted side when closing a sell/short position; Auto-Crypto mirrors that by using paper buy exits for short bracket lots: <https://github.com/ccxt/ccxt/wiki/manual>
- Coinbase describes derivatives TP/SL bracket exits as reduce-only orders, so Auto-Crypto marks synthetic paper bracket exits `reduce_only` even though no live exchange order is sent: <https://help.coinbase.com/coinbase/derivatives/bracket-orders>
- CCXT's trailing-order FAQ calls out `reduceOnly` as an exchange-dependent way to close rather than open exposure; Auto-Crypto supports paper `reduce_only` and `close_short` intents while keeping live execution disabled: <https://docs.ccxt.com/docs/faq>
- CCXT's order FAQ also recommends checking exchange feature flags for native take-profit and stop-loss support; this is why staged TP/SL simulation is recorded as paper behavior instead of assuming a portable live bracket implementation: <https://github.com/ccxt/ccxt/wiki/FAQ/9e4963a7b3438ba4fee47be1ec6922f4baf6684e>
- CCXT describes trailing orders as exchange-dependent, sometimes usable with `reduceOnly`, and able to trail by percentage or quote amount, so Auto-Crypto accepts `trailing_stop_pct`, fixed `trailing_stop_amount`, and exact paper trail starts while keeping live execution gated until adapter support is explicit: <https://docs.ccxt.com/docs/faq>
- Coinbase documents bracket and TP/SL behavior as paired exits where the triggered side executes and the other side cancels; exact `trailing_stop_price` remains paper-only because this venue behavior differs from portable trailing-order semantics: <https://help.coinbase.com/en/coinbase/trading-and-funding/advanced-trade/order-types>
- Bot setting guidance consistently emphasizes stop loss, take profit, demo/paper testing, backtesting, and position sizing before live automation: <https://bitsgap.com/blog/how-to-choose-crypto-trading-bot-settings-in-2026-range-investment-stop-loss-and-take-profit>
- Current bot-setting guidance treats take-profit and stop-loss selection as part of the strategy's risk/reward profile, so Auto-Crypto now reports and can gate both first-target and weighted staged-target reward/risk before paper execution: <https://bitsgap.com/blog/how-to-choose-crypto-trading-bot-settings-in-2026-range-investment-stop-loss-and-take-profit>
- Recent crypto-bot risk guidance highlights fixed-fraction sizing, commonly around 1-2% per trade, plus stop-loss and drawdown limits before live automation; Auto-Crypto's `risk_pct` sizing stays paper-only and can be capped with `AUTO_CRYPTO_MAX_RISK_PER_TRADE_PCT`: <https://cryptorobot.ai/blog/essential-tips-managing-risks-crypto-trading-bots>
- Current crypto backtesting guidance emphasizes testing strategies on historical or simulated price paths before launch; Auto-Crypto's `/backtest/signal` endpoint applies that idea to bracket and trailing-stop paper logic without mutating active state: <https://bitsgap.com/blog/crypto-backtesting-guide-2025-tools-tips-and-how-bitsgap-helps>
- Current crypto-bot backtesting guidance warns that clean historical fills can hide slippage, fees, latency, and stressed-market liquidity; Auto-Crypto's backtest-only `fee_bps` and `slippage_bps` inputs make those assumptions explicit while keeping live execution disabled: <https://bitsgap.com/blog/crypto-bot-backtesting-in-2026-what-it-shows-and-what-it-cannot-predict>
- Recent backtesting guidance emphasizes replaying rules with realistic fees, slippage, position sizing, drawdown review, and forward testing before risking capital; Auto-Crypto's breakeven-after-TP option is therefore modeled first in paper exits and backtest snapshots: <https://coinbureau.com/guides/how-to-backtest-your-crypto-trading-strategy>
- Backtrader's slippage documentation notes that real-market conditions can miss requested prices and exposes configurable percentage/fixed slippage in simulation; Auto-Crypto uses the same idea in isolated paper backtest and paper-exchange cost knobs: <https://www.backtrader.com/docu/slippage/slippage/>
- Interactive Brokers' current walk-forward analysis guidance describes rolling in-sample/out-of-sample testing as a closer simulation of real trading conditions than one fixed historical backtest, which is why Auto-Crypto now supports labeled candle batches suitable for chunked walk-forward checks: <https://www.interactivebrokers.com/campus/ibkr-quant-news/the-future-of-backtesting-a-deep-dive-into-walk-forward-analysis/>
- Recent crypto backtesting guidance warns that out-of-sample and walk-forward checks help expose curve fitting; Auto-Crypto's conservative candle mode keeps same-bar stop/target ambiguity from overstating bracket performance: <https://stoic.ai/blog/backtesting-trading-strategies/>
- Current bot-launch guidance recommends out-of-sample validation, walk-forward testing, demo exchange testing, and gradual rollout before live exchange API use; Auto-Crypto keeps these additions in isolated paper backtests and active bracket snapshots only: <https://skyrexio.com/blog/no-code-crypto-trading-bot-how-to-build-an-algorithmic-strategy-backtest-it-and-launch-via-exchange-api/>
- Current crypto bot settings guidance continues to emphasize stop loss, take profit, demo testing, and backtesting as core setup steps; Auto-Crypto keeps time-stop exits paper-only so stale-trade rules can be tested beside price exits before any live rollout: <https://bitsgap.com/blog/how-to-choose-crypto-trading-bot-settings-in-2026-range-investment-stop-loss-and-take-profit>
- Recent crypto bot risk guidance recommends combining trailing stops with a fixed initial stop so risk is defined before the trailing logic activates; Auto-Crypto's time-stop addition keeps that same paper-first bracket lifecycle explicit instead of replacing protective exits: <https://cryptorobot.ai/blog/essential-tips-managing-risks-crypto-trading-bots>
- Current order-type guidance describes OCO/bracket orders as paired conditional exits where one fill cancels the other, reinforcing why Auto-Crypto records `time_exit` sibling cancellation metadata in paper mode before considering exchange-native behavior: <https://bitsgap.com/blog/the-only-guide-to-order-types-youll-ever-need>
- Backtesting guidance for entry and exit rules emphasizes objective, structured exits; `max_hold_marks` adds a deterministic paper time-stop rule that can be replayed through `/backtest/signal` alongside stop-loss, take-profit, and trailing-stop paths: <https://www.fortraders.com/blog/backtesting-entry-and-exit-rules-best-practices>

## Environment Variables

```env
AUTO_CRYPTO_HOST=127.0.0.1
AUTO_CRYPTO_PORT=8004
AUTO_CRYPTO_REQUIRE_APPROVAL=false
AUTO_CRYPTO_DB_PATH=./data/auto_crypto.sqlite3

AUTO_CRYPTO_WEBHOOK_SECRET=
AUTO_CRYPTO_WEBHOOK_TOLERANCE_SECONDS=0

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
AUTO_CRYPTO_MIN_TOTAL_REWARD_RISK_RATIO=0
AUTO_CRYPTO_MAX_TAKE_PROFIT_TARGETS=0

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

Accepted signed payloads are replay-protected. Stale timestamps are rejected when `AUTO_CRYPTO_WEBHOOK_TOLERANCE_SECONDS` is set above `0`; keep it at `0` to disable timestamp staleness checks.

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
- `POST /brackets/{signal_id}/trailing-stop`
- `POST /brackets/{signal_id}/breakeven`
- `POST /brackets/{signal_id}/close`
- `POST /brackets/{signal_id}/cancel`

`POST /signals/preview` and `POST /signals/preview-text` return a `bracket_plan` object with the synthetic entry side, exit side, OCA group, trailing arming state, trailing activation price, stop/take-profit/trailing triggers, estimated notional and quantity, worst-case stop loss, equity risk percent, first-target reward/risk, and weighted total staged target reward/risk that would apply if the signal were submitted. The preview echoes both percentage and fixed-amount trail fields in the normalized signal payload.

`POST /backtest/signal` accepts a `signal` object plus a `prices` list, runs the signal through an isolated paper engine, marks each supplied price, and returns triggered exits, active exit snapshots after each mark, final paper P&L, final open notional, final positions, and the applied cost assumptions. It does not save orders, write audit events, or mutate the active engine.

`POST /backtest/signal` also accepts a `candles` list instead of `prices`. Each candle requires `high`, `low`, and `close`, plus an optional `label`, `time`, or `timestamp`. Candle backtests use a conservative adverse-first path: long signals mark low, high, then close; short signals mark high, low, then close. If a candle could have hit both a stop and a target, this favors the protective stop outcome rather than assuming the profitable target filled first. Each returned mark includes cumulative `mfe` and `mae` percentages from entry.

Example candle backtest:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8004/backtest/signal -ContentType "application/json" -Body '{
  "signal": {
    "symbol": "BTCUSDT",
    "side": "buy",
    "quote_amount": "100",
    "price": "100",
    "stop_loss_pct": "5",
    "take_profit_pct": "10"
  },
  "candles": [
    {"label": "bar-1", "high": "112", "low": "94", "close": "108"}
  ]
}'
```

Backtests may include `costs`, or top-level `fee_bps` and `slippage_bps`, to model paper fill friction. Fees are charged on entry and exit fills, and slippage moves buys above the mark and sells below the mark. These knobs are isolated to the backtest sandbox unless a caller explicitly creates a separate paper exchange with costs in code:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8004/backtest/signal -ContentType "application/json" -Body '{
  "signal": {
    "symbol": "BTCUSDT",
    "side": "buy",
    "quote_amount": "100",
    "price": "100",
    "stop_loss_pct": "5",
    "take_profit_pct": "10"
  },
  "prices": ["104", "110"],
  "costs": {
    "fee_bps": "10",
    "slippage_bps": "20"
  }
}'
```

`POST /market/price/preview` returns the paper exits that would trigger at a hypothetical mark without mutating orders, positions, audit history, daily P&L, or exposure. Use it before applying a mark when testing bracket and trailing-stop behavior.

`POST /market/price` applies the mark, returns any triggered exits, refreshes account open notional through the trading engine, and returns the current `active_exits` snapshot, including ratcheted trailing-stop trigger prices, percentage or amount trail distance, activation state, activation price, trigger distance, and water marks.

`GET /brackets` returns active synthetic paper brackets grouped by signal, including a summary of remaining notional, nearest protective trigger, worst-case stop loss, and first-target reward/risk when available. `GET /brackets/{signal_id}` returns the same summary plus active synthetic paper exit legs for one signal. `POST /brackets/{signal_id}/preview` previews only that bracket against a mark without mutating active state. `POST /brackets/{signal_id}/stop` tightens a paper stop without loosening risk, persists an amendment order, and records audit context. `POST /brackets/{signal_id}/trailing-stop` tightens a paper trailing trigger without loosening risk, persists an amendment order, and records audit context. `POST /brackets/{signal_id}/breakeven` moves open protective exits to entry when it tightens risk, persists an amendment order, and records audit context. `POST /brackets/{signal_id}/close` closes or partially reduces the selected synthetic paper bracket at the supplied mark, persists a reduce-only close or reduce order, records realized paper P&L, and cancels remaining exits only when the bracket is fully closed. `POST /brackets/{signal_id}/cancel` removes those synthetic exits, persists a cancellation order, and records audit context without closing the position.

Bracket summaries now include `protective_distance_pct`, `protective_locked_pnl`, `total_target_reward`, and `total_target_reward_risk_ratio`. A negative protective distance means the stop/trailing trigger has moved beyond entry and the paper bracket has locked in profit if that protective exit fires at the trigger. `worst_case_loss` floors at zero once the protective exit is at or beyond breakeven. For staged exits, total target reward is weighted by each target's `close_pct`, so operators can compare the whole bracket plan instead of only the nearest target.

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
- Paper breakeven amendment orders are replayed at startup so protective exits remain locked at entry after restart.

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
