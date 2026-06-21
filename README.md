# Auto-Crypto

Auto-Crypto is a paper-first crypto trading automation bot for Discord-style alerts, TradingView webhooks, and operator-controlled execution workflows. It is built to normalize crypto alerts, apply risk checks, queue approvals when required, simulate fills, track bracket exits, and persist audit history before any live exchange adapter is enabled.

Live trading is intentionally disabled by default. Use exchange API keys with trade-only permissions, no withdrawals, and only after sandbox validation.

## What It Does Now

- Runs a local FastAPI bot/API on Windows with `Launch-Auto-Crypto.bat`
- Accepts TradingView/custom JSON alerts at `POST /webhooks/tradingview`
- Accepts strict text crypto alerts at `POST /webhooks/text-alert`
- Parses text alerts without ordering at `POST /signals/parse-text`
- Parses text-alert trailing stops as percentages, fixed quote-distance amounts, exact starting trigger prices, exact activation prices, partial trailing close sizes, and post-target profit locks
- Accepts object-shaped webhook bracket legs such as `bracket.stop_loss`, `bracket.take_profit`, and `bracket.trailing_stop`, including common percentage, price, quantity, activation, and trailing aliases
- Normalizes crypto pairs such as `BTCUSDT`, `BTC/USDT`, `ETH-USDC`, and `SOL_USDT`
- Blocks duplicate signal IDs across restarts with SQLite-backed idempotency
- Applies pre-trade risk checks for stop loss, maximum stop width, first-target and total staged reward/risk, staged target count, max order notional, max open notional, aggregate open bracket risk, per-symbol concentration, per-trade stop-risk amount, volatility regime, equity-percent position size, leverage, slippage, allowed exchanges, blocked symbols, daily loss, and consecutive losing exits
- Supports fixed-fraction paper sizing from `risk_pct` or `risk_amount` plus stop distance, with optional max risk-percent and max stop-risk amount caps
- Supports approval-required mode with persisted pending approvals, including risk-sized tickets and paper time-stop fields
- Records paper orders, paper positions, realized PnL, active bracket lots, and audit events
- Rehydrates paper positions, bracket lots, and exposure risk state from SQLite after restart
- Nets bracketed reversal signals against existing opposite paper lots before opening a residual long or short bracket, with `netted_quantity` and `opened_quantity` persisted on the paper order
- Triggers paper long and short stop-loss, single or staged take-profit, activation-gated trailing-stop, and break-even exits from `POST /market/price`
- Accepts bracket exits either as top-level signal fields or nested under `bracket`, `bracket_order`, or `exit_plan`
- Provides paper-only bracket templates (`fixed_bracket`, `activation_trailer`, and `staged_runner`) that can be previewed or submitted through the existing signal intake path without enabling live exchange execution
- Rejects staged take-profit brackets when any absolute target is on the wrong side of entry for the order direction
- Rejects absolute stop-loss, take-profit, trailing-start, and trailing-activation prices when they are on the wrong side of the entry for buy or short brackets
- Rejects standalone break-even triggers unless there is a stop-loss or trailing-stop leg for the break-even rule to move
- Links paper bracket exit legs with OCA-style groups and records which sibling stop, take-profit, or trailing legs are canceled when a final paper exit closes the lot
- Accepts optional custom synthetic `oca_group`/`oco_group` identifiers for paper bracket legs so external alerts can preserve their own bracket grouping labels
- Can include `oca_group` and `canceled_exit_orders` in final paper trigger responses when `POST /market/price` is called with `include_order_metadata: true`
- Marks activation-gated trailing stops as `pending_activation` until price movement arms them, then records triggered paper exits as `filled` reduce-only close orders; dormant trailing legs are not counted as current protective exits in bracket risk summaries
- Supports paper trailing stops by percentage callback or fixed quote-distance amount, plus either percentage or absolute-price activation gates
- Supports partial paper trailing-stop reductions with `trailing_stop_close_pct`, leaving the remaining simulated lot under its other bracket exits
- Supports paper trailing-step controls so trailing stops only ratchet after a minimum trigger improvement instead of every favorable tick
- Supports optional paper trailing-after-take-profit controls so a trailing stop can stay dormant until the first staged or full take-profit target fills
- Requires a fixed stop-loss by default when a trailing stop starts pending activation or pending take-profit, so initial paper risk is defined before the trail can arm
- Supports optional paper breakeven-after-take-profit and profit-lock-after-take-profit brackets so the remaining stop/trailing legs lock at entry or a configured profit percentage after a staged target fills
- Supports paper time-stop exits with `max_hold_marks`/`time_stop_marks` so a bracket can close after a fixed number of market-price marks when no price exit fired
- Lists all active synthetic paper brackets with remaining-notional, worst-case stop-loss, first-target reward, reward/risk ratios, and aggregate bracket-risk summaries
- Supports protective stop, trailing-stop, take-profit, and manual breakeven amendments; protective exits only tighten risk and take-profit targets only move farther into profit
- Supports paper-only trailing-stop tightening from a supplied favorable mark, computing the next synthetic trail trigger from the configured percentage or fixed trail distance while rejecting marks that would loosen risk
- Supports paper-only bracket close-by-signal controls that flatten or partially reduce the selected simulated bracket at an operator-supplied mark or at the current nearest protective trigger
- Previews one active bracket by signal ID at a hypothetical mark, through a multi-mark path, or through an OHLC candle path with conservative adverse-first or favorable-first sequencing, optional open mark, ambiguous stop/target range flags, policy-comparison outcome ranges, trigger distance, trailing activation context, and simulated post-mark trailing ratchets without mutating live paper state
- Previews one bracket's trailing-stop path across several hypothetical marks with before/after activation and ratchet flags, without mutating active paper orders, positions, P&L, or water marks
- Shows a paper bracket exit ladder by signal ID with trigger order, estimated close quantity, estimated notional, estimated P&L, and optional mark-distance math for each stop, trailing, take-profit, or time-stop leg
- Shows read-only bracket decision support by signal ID with paper exit sequencing, reward/risk health flags, and next-trailing-ratchet telemetry for a supplied mark
- Shows read-only bracket coverage diagnostics so operators can spot partial take-profit residual quantity/notional, partial trailing reductions, missing full-profit exits, and paper-only time exits before applying marks
- Shows read-only OCA group diagnostics and bracket health flags for reused external OCA/OCO labels across active paper brackets
- Cancels active synthetic paper bracket exits by signal ID while leaving the underlying paper position open for separate manual management
- Previews hypothetical market-price marks and bracket/trailing exits without mutating paper orders or positions, including simulated post-mark trailing-stop ratchets and per-bracket impact summaries for trigger, close, quantity, and trailing-ratchet outcomes
- Reports applied or previewed trailing-stop ratchet impacts on market price marks, plus optional trigger-price and trigger-gap metadata for paper exits that crossed through their stop or target
- Previews server-side risk decisions from the operator UI without placing orders
- Backtests one signal against a supplied paper mark-price path and returns active exit snapshots after each mark without mutating the live in-memory engine
- Backtests one signal against OHLC candles with conservative adverse-first intrabar sequencing, plus MFE/MAE excursion metrics, without mutating active state
- Backtests can opt into paper fee and slippage assumptions and report closed-P&L drawdown/runup so bracket/trailing-stop results are not limited to clean mark-price fills
- Backtests can report final mark-to-market unrealized P&L for open paper lots, or optionally force-close remaining simulated bracket quantity at the final mark
- Fetches Bitunix futures klines through the native market-data adapter and can run isolated paper backtests directly from those candles
- Shows persisted signal history with one-click reload into the Trading Desk
- Supports quote-notional and base-quantity ticket sizing, paper position close controls, bracket lot context, bracket previews, stop tightening, breakeven locks, bracket cancellation, trigger tests, and local unrealized P&L marks in the operator UI
- Adds operator-facing snapshot cards, a trading-desk preflight checklist, and bracket status/distance columns so paper queues, exposure, positions, and synthetic exits are easier to triage from the UI
- Captures inline halt and approval rejection reasons in operator workflows
- Shows timestamped audit events, exports filtered audit CSVs, and copies JSON payloads from operator panels
- Exposes CCXT venue discovery and capability inspection without enabling live execution
- Exposes a non-executing bracket/trailing exchange-plan preview that classifies a signal as paper synthetic, attached TP/SL, entry-then-OCO, entry-then-trailing, or paper-required based on venue capability flags, staged/partial exit shape, and time-stop use, plus leg-level exchange-family, buy/sell close-action sequencing, activation, partial-close, ignored reduce-only bracket fields, and plan-summary metadata
- Exchange-plan previews now include an explicit paper ticket flow for bracket buy/sell logic, leg trigger comparators such as `mark_price <= stop`, reduce-only close actions, and next trailing-ratchet estimates so operators can inspect the intended order path before any approval or paper submission
- Accepts explicit close-side aliases such as `sell_to_close`, `buy_to_cover`, `cover_short`, `reduce_long`, and `reduce_short` as reduce-only paper close intents
- Tracks a curated BTC/ETH/SOL platform registry for Coinbase, Kraken, Binance.US, Binance, Gemini, Bitstamp, Alpaca, Robinhood, Crypto.com, OKX, Bybit, KuCoin, Bitget, Gate.io, MEXC, Phemex, BitMEX, Deribit, and Bitunix
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

## macOS Beta Installer

MacBook beta testers can install the local source build with the bundled macOS installer script. It creates the Python virtual environment, installs the package in editable mode, uses persistent SQLite storage, and adds a double-click launcher to the Desktop.

Prerequisites:

- macOS with Python 3.11+ on `PATH`

From the repository root:

```bash
chmod +x install-macos.sh
./install-macos.sh
```

After installation, double-click `Auto-Crypto.command` on the Desktop. The launcher starts the API on `8004` and opens `http://127.0.0.1:8004/ui`. Logs are written to `~/Desktop/Auto-Crypto.log`.

Manual launch options:

```bash
./install-macos.sh --launch
./install-macos.sh --launch --install-deps
./install-macos.sh --launch --exchange-deps
./install-macos.sh --launch --start-discord
./install-macos.sh --launch --port 8004 --no-browser
```

`--start-discord` requires `DISCORD_BOT_TOKEN` in the environment.

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
  "trailing_stop_close_pct": "40",
  "trailing_activation_price": "50750",
  "breakeven_trigger_pct": "2",
  "profit_lock_after_take_profit_pct": "1",
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

Webhook brackets may be sent as flat fields or as object-shaped legs under `bracket`, `bracket_order`, or `exit_plan`. For example, `bracket.stop_loss.percent`, `bracket.take_profit.target_pct`, `bracket.take_profit.close_percent`, `bracket.trailing_stop.callback_pct`, `bracket.trailing_stop.qty_pct`, `bracket.trailing_stop.activation_pct`, and `bracket.trailing_stop.after_take_profit` normalize into the same paper bracket model as the flat fields above. Staged `take_profit_targets` also accept `percent`, `percentage`, `target_pct`, `target_price`, `limit_price`, `qty_pct`, `quantity_pct`, `size_percent`, and `close_percent`. Trailing close sizes above `100` are rejected by risk checks so a trailing leg cannot claim to close more than the simulated paper lot.

For buy brackets, `stop_loss_pct` creates a fixed protective sell below entry, `take_profit_pct` creates a fixed profit-taking sell above entry, and `trailing_stop_pct` creates a sell stop that starts below entry and ratchets upward when `POST /market/price` marks a new high-water price. If a buy bracket arrives while paper short lots are open for the same symbol, Auto-Crypto first buys back the short lots, realizes paper PnL, and attaches the new bracket only to any remaining buy quantity. The order JSON records `netted_quantity` and `opened_quantity` so restart replay keeps the same residual bracket lot. Use `trailing_stop_amount` or `trail_amount` when the trail should stay a fixed quote-currency distance behind the high-water mark instead of a percentage. Use `stop_loss_price`, `take_profit_price`, and `trailing_stop_price` when the alert source already calculated exact initial trigger prices. Absolute buy-bracket stop-loss and trailing-start prices must be below entry, while absolute take-profit and trailing-activation prices must be above entry. `trailing_stop_price` sets only the starting paper trigger; `trailing_stop_pct` or `trailing_stop_amount` still controls the ratchet distance after favorable marks. The trailing stop never moves lower. Add `trailing_stop_close_pct` or `trail_close_pct` below `100` when the trailing leg should reduce only part of the original paper lot; after that simulated partial fill, the remaining quantity keeps its other open stop-loss and take-profit exits. Add `trailing_step_pct`, `trail_step_pct`, `trailing_step_amount`, or `trail_step_amount` to require the next synthetic trailing trigger to improve by at least that percentage or quote-currency distance before the paper stop ratchets. Add `trailing_activation_pct` to keep the trailing leg dormant until price has moved favorably by that percent, or `trailing_activation_price`, `trail_activation_price`, or `activation_price` to arm it at an exact favorable mark. Add `trail_after_take_profit`, `trailing_after_take_profit`, or `trail_after_tp` when the trailing leg should start as `pending_take_profit` and only arm after the first paper take-profit fill; if an activation gate is also configured, the trailing leg still waits for that favorable activation mark after the target fill. Add `breakeven_trigger_pct` to move protective stop exits up to the entry price after a favorable move. Add `breakeven_after_take_profit: true` when staged take-profit fills should automatically move remaining protective stop-loss and trailing-stop legs to the paper entry price. Add `profit_lock_after_take_profit_pct` (also accepted as `lock_profit_after_take_profit_pct` or `profit_lock_after_tp_pct`) when staged take-profit fills should instead move remaining protective exits to an entry-relative profit lock, such as `102.00` for a long entry at `100` with `2%`. Text alerts can use `LOCKAFTERTP 2%`. Add `max_hold_marks` or `time_stop_marks` to close the paper bracket at the current mark after that many `POST /market/price` updates if stop-loss, take-profit, and trailing-stop exits have not already closed it.

For short brackets, send `side: "sell"` or `side: "short"` with at least one exit field such as `stop_loss_pct`, `stop_loss_price`, `take_profit_pct`, `take_profit_price`, `trailing_stop_pct`, `trailing_stop_amount`, or `max_hold_marks`. Paper stop-loss and trailing-stop triggers sit above entry, paper take-profit triggers sit below entry, time stops close at the current paper mark, and exit orders buy back paper quantity. If a sell bracket arrives while paper long lots are open for the same symbol, Auto-Crypto sells down the long lots first, realizes paper PnL, and opens a short bracket only for the residual sell quantity. Absolute short-bracket stop-loss and trailing-start prices must be above entry, while absolute take-profit and trailing-activation prices must be below entry. Short trailing stops track a low-water price and ratchet downward only after favorable price movement; `trailing_stop_close_pct` partially buys back the simulated short lot, `trailing_activation_pct` delays arming until price falls by that percent, and `trailing_activation_price` arms at an exact lower mark. `profit_lock_after_take_profit_pct` moves protective exits downward after the first staged target, such as `98.00` for a short entry at `100` with `2%`. A plain `SELL` without bracket fields remains a manual long close. Send `side: "close_short"` or `reduce_only: true` with `side: "buy"` to buy back paper short quantity without opening a new paper long.

Every paper bracket leg now carries an `oca_group` and `status` in order JSON and active-exit snapshots. By default the group is derived from `signal_id`; send `oca_group`, `oco_group`, or `order_group` at the top level or inside `bracket`/`exit_plan` to preserve an external alert's grouping label in all synthetic exit legs and cancellation metadata. Activation-gated trailing stops start as `pending_activation`, move to `open` when the favorable activation mark is reached, and are recorded as `filled` on the synthetic paper exit order that closes quantity. Take-profit-gated trailing stops start as `pending_take_profit`, move to `open` after the first paper target fill when activation rules allow it, and are classified as paper/custom-native mapping in exchange-plan previews. A partial trailing stop records its configured `close_pct`, removes that triggered trailing leg from the remaining lot, and leaves sibling synthetic exits active. When a stop-loss, full trailing-stop, final take-profit, or time-stop closes the remaining paper lot, the synthetic exit order is marked `reduce_only: true` and includes `exit_kind`, the filled exit leg, plus `canceled_exit_orders` so tests and operators can see which sibling legs would have been canceled in one-cancels-other behavior. Send `include_order_metadata: true` to `POST /market/price` to include `oca_group`, `trigger_price`, `trigger_gap`, and serialized `canceled_exit_orders` in the trigger response and audit payload for final exits. `trigger_gap` is the absolute paper distance between the configured trigger and the supplied mark/fill price, which helps spot gaps through stops or targets during paper review. This is still paper accounting only; no live OCO or exchange-native bracket order is submitted.

`POST /market/price` and `POST /market/price/preview` both include a `trailing_ratchets` array when the supplied mark would tighten a synthetic trailing stop without closing the bracket. Each row reports the `signal_id`, before/after trigger prices, trigger improvement, and status transition. This makes normal price-mark responses as transparent as the bracket-specific preview endpoints without enabling live trailing-order submission.

`POST /signals/exchange-plan` is a non-executing order-plan view for buy/sell bracket logic. It now returns `summary.entry_ticket`, `summary.exit_ticket`, and `summary.bracket_order_flow` so a long bracket is shown as buy-to-open followed by sell-to-close stop, target, or trailing legs, while a short bracket is shown as sell-to-open followed by buy-to-cover exits. Each planned exit includes `trigger_relation` and `trigger_condition` fields, and trailing legs include `params.trailing.nextRatchet` with the next trigger and minimum favorable mark when the trailing stop is already open, or the activation/take-profit gate that blocks ratcheting when it is pending. This mirrors current exchange-planning practice: CCXT documents that attached TP/SL support is venue-dependent and distinct from standalone trigger orders (<https://docs.ccxt.com/docs/faq>, <https://github.com/ccxt/ccxt/wiki/manual>), while Binance describes trailing stops in terms of activation price plus callback/rebound conditions (<https://www.binance.com/en/support/faq/detail/360042299292>). Auto-Crypto keeps this as an inspection-only planner; `live_order_safe` remains `false`.

Paper time stops appear as `time_exit` legs with `status: "waiting"` in previews, bracket listings, and backtest snapshots. Active snapshots include `max_hold_marks`, `marks_seen`, and `marks_remaining`. When the count reaches zero, Auto-Crypto records a reduce-only `time_exit` close order at the supplied mark and cancels sibling synthetic exits in the same paper OCA group. This is intended for backtesting and operator review of stale-entry rules; it does not schedule a wall-clock order or submit live exchange instructions.

Operators can inspect active paper brackets, review bracket health flags, tighten protective stops or trailing-stop triggers, move take-profit targets farther into profit, move protective exits to breakeven, lock a configured amount of paper profit into protective exits, close a bracket at an operator-supplied paper mark or at its current nearest protective trigger, and cancel active paper brackets by signal ID:

```powershell
Invoke-RestMethod http://127.0.0.1:8004/bracket-templates

Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8004/signals/preview-template -ContentType "application/json" -Body '{
  "template": "activation_trailer",
  "signal": {
    "symbol": "BTCUSDT",
    "side": "buy",
    "quote_amount": "100",
    "price": "50000"
  }
}'

Invoke-RestMethod http://127.0.0.1:8004/brackets

Invoke-RestMethod http://127.0.0.1:8004/brackets/health

Invoke-RestMethod http://127.0.0.1:8004/brackets/coverage

Invoke-RestMethod http://127.0.0.1:8004/brackets/btc-breakout-001

Invoke-RestMethod "http://127.0.0.1:8004/brackets/btc-breakout-001/exit-ladder?mark_price=50600"

Invoke-RestMethod "http://127.0.0.1:8004/brackets/btc-breakout-001/decision-support?mark_price=50600"

Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8004/brackets/btc-breakout-001/preview -ContentType "application/json" -Body '{
  "price": "50600"
}'

Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8004/brackets/btc-breakout-001/preview-path -ContentType "application/json" -Body '{
  "prices": ["50600", "52400", "51100"]
}'

Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8004/brackets/btc-breakout-001/preview-candle -ContentType "application/json" -Body '{
  "open": "50600",
  "high": "52400",
  "low": "49750",
  "close": "51100",
  "intrabar_policy": "conservative_adverse_first"
}'

Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8004/brackets/btc-breakout-001/stop -ContentType "application/json" -Body '{
  "trigger_price": "50250",
  "reason": "manual support moved higher"
}'

Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8004/brackets/btc-breakout-001/trailing-stop -ContentType "application/json" -Body '{
  "trigger_price": "52100",
  "reason": "operator tightened trailing trigger after new support formed"
}'

Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8004/brackets/btc-breakout-001/trailing-stop/mark -ContentType "application/json" -Body '{
  "mark_price": "54800",
  "reason": "operator derived the next paper trailing trigger from a favorable mark"
}'

Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8004/brackets/btc-breakout-001/trailing-stop/preview-path -ContentType "application/json" -Body '{
  "prices": ["50600", "52100", "54800"]
}'

Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8004/brackets/btc-breakout-001/take-profit -ContentType "application/json" -Body '{
  "trigger_price": "54200",
  "reason": "operator raised paper target after breakout confirmed"
}'

Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8004/brackets/btc-breakout-001/breakeven -ContentType "application/json" -Body '{
  "reason": "operator locked remaining paper risk at entry"
}'

Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8004/brackets/btc-breakout-001/lock-profit -ContentType "application/json" -Body '{
  "lock_profit_pct": "2",
  "reason": "operator locked a paper profit floor after breakout"
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

`POST /brackets/{signal_id}/preview` is signal-specific and paper-only. It runs the hypothetical mark against a deep copy of the selected paper bracket, returns only exits that would trigger for that bracket, and includes `distance_to_trigger`, `distance_to_trigger_pct`, and `trailing_activation_price` in the active-exit snapshot. The `impact` block reports `will_trigger`, `triggered_kinds`, `will_close_bracket`, remaining quantity before/after, quantity delta, and any simulated trailing-stop ratchet from the mark. It does not create orders, update P&L, record audit events, or mutate trailing stops on the active engine.

`POST /brackets/{signal_id}/preview-path` is also signal-specific and paper-only. Send `prices` or `marks` as a non-empty list to replay several hypothetical marks through one deep-copied bracket state. The response includes each mark's `would_trigger` exits, `preview_active_exits` after that mark, simulated preview positions, and `mutates_state: false`; the live paper bracket, positions, audit log, and order history are unchanged. This is useful for reviewing whether an activation-gated or stepped trailing stop would ratchet before it would trigger.

`POST /brackets/{signal_id}/preview-candle` is the OHLC version of bracket path preview. Send `high`, `low`, and `close`, plus optional `open`/`open_price` and optional `intrabar_policy`. The default `conservative_adverse_first` policy replays the copied bracket as open, adverse extreme, favorable extreme, close; for long brackets that is `open`, `low`, `high`, `close`, while short brackets use `open`, `high`, `low`, `close`. Send `intrabar_policy: "favorable_first"` to inspect the optimistic opposite sequence. Every response includes an `outcome` block with trigger count, first trigger phase/kind, close status, remaining bracket quantity, final paper position quantity, and final realized P&L for the copied bracket path. Add `compare_policies: true` or `compare_intrabar_policies: true` to receive `policy_comparison`, which runs both supported policies, reports whether outcomes diverged, and returns the low/high/spread P&L range. Responses include `ambiguous_intrabar: true` and an `ambiguity` block when both protective and profit-taking exits are inside the same candle range, because OHLC data alone cannot prove which exit happened first. The active paper bracket, trailing water marks, positions, audit log, and order history are unchanged.

`POST /brackets/{signal_id}/trailing-stop/preview-path` is a focused paper-only replay for trailing stops. Send `prices` or `marks` as a non-empty list to see each mark's trailing state before and after the copied bracket processes it, including `activated`, `ratcheted`, `next_trailing_trigger`, step-threshold telemetry, water marks, any simulated trigger, and `mutates_state: false`. Use it when the operator wants to understand whether an activation-gated or stepped trail would move before applying real paper marks or submitting a manual `/trailing-stop/mark` amendment.

`GET /bracket-templates` lists paper-only bracket presets that operators can use as starter exit plans. `fixed_bracket` adds a fixed stop-loss and take-profit, `activation_trailer` adds a fixed initial stop plus activation-gated stepped trailing logic, and `staged_runner` adds staged targets, a take-profit-gated partial trailing leg, and breakeven-after-target behavior. `POST /signals/preview-template` accepts `{ "template": "...", "signal": {...}, "overrides": {...} }`, merges the template into the draft signal, then returns the same risk and bracket preview as `/signals/preview` without creating paper orders. `POST /signals/submit-template` uses the same merge path but hands the normalized result to the existing intake service, so the result is still paper or approval-gated according to the running app configuration.

This preview behavior follows current trading-bot references. CCXT documents attached `stopLoss`/`takeProfit` and trailing order support as exchange-dependent, so Auto-Crypto keeps these previews synthetic and paper-only until venue-specific mappings are reviewed (<https://docs.ccxt.com/docs/faq>, <https://github.com/ccxt/ccxt/wiki/manual>). Binance documents OCO as a linked order list where one executed leg cancels the other, and also notes that trailing stops can be used as contingent OCO legs; Auto-Crypto mirrors that link-and-cancel shape in paper metadata while leaving live OCO disabled (<https://developers.binance.com/docs/binance-spot-api-docs/rest-api/trading-endpoints>, <https://developers.binance.com/docs/binance-spot-api-docs/faqs/trailing-stop-faq>). Coinbase describes take-profit/stop-loss attachments and bracket orders as predefined exit levels for risk management, and Kraken documents spot take-profit/stop-loss brackets while noting exclusions around trailing-stop combinations, so Auto-Crypto surfaces coverage and exchange-plan diagnostics instead of assuming every venue can carry the same native bracket shape (<https://help.coinbase.com/en/coinbase/trading-and-funding/advanced-trade/order-types>, <https://support.kraken.com/articles/bracket-orders-on-kraken-pro>). Freqtrade documents keeping a static stop until an offset is reached and ignoring stop changes that would loosen risk; those practices match Auto-Crypto's activation, step, and no-loosening trail previews (<https://www.freqtrade.io/en/stable/stoploss/>). Backtrader's bracket-order docs model linked stop-loss and take-profit children, with opposite-side exits for short positions, which matches the long-sell and short-buy paper exit accounting here (<https://www.backtrader.com/docu/order-creation-execution/bracket/bracket/>). Backtrader's trailing-stop docs describe long trails following price upward while short trails do the opposite; QuantConnect warns that synthetic OCO helpers can overstate certainty when both stop and take-profit orders fill inside the same bar; and the 2026 TradeZella backtesting guide warns that slippage, commissions, and execution assumptions matter. Auto-Crypto therefore exposes policy comparison, P&L range, and trigger-gap telemetry instead of pretending one OHLC ordering or mark fill is certain (<https://www.backtrader.com/docu/order-creation-execution/trail/stoptrail/>, <https://www.quantconnect.com/docs/v2/writing-algorithms/trading-and-orders/order-types/other-order-types>, <https://www.tradezella.com/blog/backtesting-trading-strategies>).

`GET /brackets/{signal_id}/exit-ladder` is also signal-specific and paper-only. It lists the bracket's synthetic exits in the direction they would be encountered by price, including each leg's intent, status, `close_pct`, estimated close quantity, estimated trigger notional, estimated P&L, and whether that leg would close the remaining paper lot. Add `?mark_price=...` to include current distance-to-trigger values without mutating trailing stops or recording any order.

`GET /brackets/{signal_id}/decision-support` is a read-only operator view over the same synthetic paper bracket. It returns the bracket summary, health row, next open trigger, full trigger sequence, and a `trailing` block for each trailing leg. Add `?mark_price=...` to see whether that mark would activate a pending trailing stop, whether a stepped trailing stop would ratchet, the `next_trailing_trigger`, `next_trailing_trigger_change`, and the configured `trailing_step_required`. The endpoint does not mutate paper orders, P&L, active exits, or water marks.

Stop amendments are paper-only bracket maintenance events. A long bracket stop can only move upward, and a short bracket stop can only move downward. Attempts to loosen the stop return `409` and leave the bracket unchanged. Successful amendments record a synthetic `bracket_stop_amend` paper order plus a `bracket.stop_amended` audit event when SQLite persistence is configured, and those amendments replay after restart.

Trailing-stop amendments are also paper-only bracket maintenance events. A long trailing trigger can only move upward, and a short trailing trigger can only move downward. A successful `POST /brackets/{signal_id}/trailing-stop` amendment records a synthetic `bracket_trailing_stop_amend` paper order, marks the trailing leg `open`, syncs the paper water mark so later trailing updates do not loosen it, records a `bracket.trailing_stop_amended` audit event when SQLite persistence is configured, and replays after restart. `POST /brackets/{signal_id}/trailing-stop/mark` accepts `mark_price` or `price`, derives the next protective trigger from the bracket's configured `trailing_stop_pct` or `trailing_stop_amount`, rejects marks that are not favorable enough to tighten the trail or satisfy the configured trailing step, records `bracket_trailing_stop_mark_amend`, and replays after restart. These are operator overrides for simulated trailing exits only; they do not submit or modify any live exchange order.

Take-profit amendments are paper-only bracket maintenance events. A long target can only move upward, and a short target can only move downward, so amendments cannot reduce the projected reward after approval. `POST /brackets/{signal_id}/take-profit` defaults to the first still-open target and accepts optional `target_index` for staged brackets. Successful amendments record a synthetic `bracket_take_profit_amend` paper order plus `bracket.take_profit_amended` audit event when SQLite persistence is configured, and replay after restart.

Breakeven amendments are paper-only bracket maintenance events. `POST /brackets/{signal_id}/breakeven` moves open stop-loss and trailing-stop legs to the entry price when doing so tightens risk, records a synthetic `bracket_breakeven` order plus `bracket.breakeven_amended` audit event when SQLite persistence is configured, and replays after restart. If the protective exits are already at or beyond breakeven, the API returns `409` and leaves the bracket unchanged.

Profit-lock amendments are paper-only bracket maintenance events. `POST /brackets/{signal_id}/lock-profit` accepts `lock_profit_pct` or `profit_lock_pct`, computes the entry-relative lock price, and moves open stop-loss and trailing-stop legs there only when the move tightens risk. For a long entry at `100`, `lock_profit_pct: "2"` moves protective exits to `102.00`; for a short entry at `100`, it moves them to `98.00`. Successful amendments record a synthetic `bracket_profit_lock` paper order plus `bracket.profit_locked` audit event when SQLite persistence is configured, and replay after restart. If the requested lock would loosen the current protective trigger, the API returns `409`.

`GET /brackets/health` summarizes active synthetic paper brackets for operator triage. It reports counts plus per-bracket issue codes such as `no_open_protective_exit`, `protective_exit_still_at_risk`, `trailing_stop_pending`, and `no_open_take_profit_exit`. A bracket can still be intentional with an issue code; the endpoint is a review aid, not an automated live-trading gate.

`GET /brackets/coverage` is a read-only companion to health. It reports each active bracket's open take-profit close percentage, open trailing-stop close percentage, strongest protective close percentage, residual percentage after all open take-profit targets, full-close and partial-close exit counts, time-exit presence, and coverage notes such as `take_profit_plan_leaves_residual`, `contains_partial_exit`, `contains_paper_time_exit`, or `no_open_protective_exit`. Use it before pushing marks through `/market/price` when staged targets and partial trailing reductions make the remaining paper quantity hard to see at a glance.

`GET /brackets/oca-groups` inventories active synthetic OCA/OCO group labels across paper brackets. It reports each group, member signal IDs, symbols, directions, exit count, and notes such as `oca_group_reused_across_brackets`, `oca_group_spans_symbols`, and `oca_group_spans_directions`. `GET /brackets/health` also flags `oca_group_reused_across_brackets` per affected bracket so operators can catch accidental external group-label reuse before trying to map a paper bracket plan to a venue-native OCO/bracket workflow.

Bracket cancellation records a synthetic `bracket_cancel` paper order plus a `bracket.canceled` audit event when SQLite persistence is configured. It removes the active stop-loss, take-profit, and trailing-stop legs for that paper lot, including trailing stops still waiting in `pending_activation`, but it does not close the open paper exposure. The operator must submit a separate reduce-only or manual close order when the position itself should be closed.

Bracket close records a synthetic `bracket_manual_close` or `bracket_manual_reduce` paper order plus a `bracket.closed` audit event when SQLite persistence is configured. `POST /brackets/{signal_id}/close` requires `price` or `mark_price`; send optional `close_pct` or `base_amount` to reduce only part of the remaining bracket quantity. Full closes cancel remaining synthetic exits, while partial reductions keep the remaining stop-loss, take-profit, and trailing-stop legs active for the reduced paper lot. The endpoint uses the existing reduce-only long/short lot accounting, updates daily paper P&L and exposure, and replays after restart. This is still a simulated close at an operator-supplied mark; it does not submit a live market order.

`POST /brackets/{signal_id}/close-protective` uses the selected bracket's current nearest protective stop-loss or trailing-stop trigger as the paper close mark, then follows the same reduce-only accounting, optional `close_pct`/`base_amount` handling, and restart replay behavior as manual bracket close. It records `bracket.protective_closed` in the audit log when SQLite persistence is configured. This is useful when an operator accepts the current protective exit plan without typing a separate mark; it still does not send a live order.

The Portfolio Sentinel `Bracket Ledger` exposes those same paper-only controls in the UI. It shows the originating signal, synthetic exit status, trigger, and trigger distance from the current mark before the action buttons. `Preview` calls `POST /brackets/{signal_id}/preview` at the selected exit trigger, `Tighten Stop` prompts for a new protective stop and calls `POST /brackets/{signal_id}/stop`, `Tighten Trail` calls `POST /brackets/{signal_id}/trailing-stop`, `Raise TP` calls `POST /brackets/{signal_id}/take-profit`, `Breakeven` calls `POST /brackets/{signal_id}/breakeven`, `Close at Protective` calls `POST /brackets/{signal_id}/close-protective`, `Cancel Bracket` removes synthetic exits without closing the paper position, and `Trigger` still applies the mark through `POST /market/price`. These controls are operator maintenance tools for simulated brackets; they do not place live exchange orders.

The Command Center includes operator snapshot cards for attention flags, exposure usage, open paper positions, and active synthetic exits. The Trading Desk includes an operator preflight checklist for live-trading lock state, execution intent, global halt state, approval queue state, synthetic-exit visibility, and audit visibility. These panels read the existing `/ui/state` payload and do not introduce live execution.

Use `take_profit_targets` for staged exits. Each target accepts either `pct` or `trigger_price` plus `close_pct`, and the total `close_pct` cannot exceed `100`. For example, `[{ "pct": "3", "close_pct": "50" }, { "trigger_price": "53000", "close_pct": "50" }]` sells half of the original paper lot at 3% profit and the remaining half at the exact trigger price. Long absolute targets must sit above entry and short absolute targets must sit below entry; risk checks reject the whole signal if any staged target is inverted. If configured, `AUTO_CRYPTO_MAX_TAKE_PROFIT_TARGETS` rejects target lists that are too long, and `AUTO_CRYPTO_MIN_TOTAL_REWARD_RISK_RATIO` evaluates the weighted staged reward/risk across all targets. If the first target fills and price later falls to the stop, the remaining paper quantity exits through the stop-loss or trailing-stop leg. If `trail_after_take_profit` is true, the trailing leg does not ratchet or trigger until that first target has filled. If `breakeven_after_take_profit` is true, a partial take-profit fill also tightens remaining stop-loss and trailing-stop legs to entry when that reduces paper risk. If `take_profit_targets` is omitted, `take_profit_pct` or `take_profit_price` still creates one full-size take-profit target.

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
    "trailing_stop_close_pct": "50",
    "trailing_activation_price": "96",
    "trail_after_take_profit": true,
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
BUY BTCUSDT $125 @ 50000 SL @ 49000 TP @ 51500 TRAIL @ 48750 ACT @ 50750
BUY BTCUSDT $125 @ 50000 SL 2.5% TP 5% TRAILAMT 250 TRAILCLOSE 40%
BUY BTCUSDT $125 @ 50000 SL 2% TP1 3% 50% TP2 @ 53000 50%
BUY BTCUSDT $100 @ 50000 SL 2% TP1 4% 50% TP2 8% 50% TRAIL 4% BEAFTERTP TRAILAFTERTP
BUY SOLUSDT $50 @ 150 SL 3% TP 8% TS 4% BE 3%
SELL ETH/USDT 0.25 @ 3000
SHORT ETHUSDT $75 @ 3000 SL 2% TP 4% TRAIL 3% ACT 1%
```

Text alerts support percentage brackets (`SL 2%`, `TP 5%`), absolute brackets (`SL @ 49000`, `TP @ 51500`), percentage trailing stops (`TRAIL 4%` or `TS 4%`), fixed-distance trailing stops (`TRAILAMT 250` or `TRAIL 250 USDT`), exact starting trailing triggers (`TRAIL @ 48750`), exact trailing activation marks (`ACT @ 50750`), partial trailing close sizes (`TRAILCLOSE 40%`), trailing steps (`TRAIL 4% STEP 1%`), breakeven triggers (`BE 2%`), breakeven-after-target locks (`BEAFTERTP`), take-profit-gated trailing stops (`TRAILAFTERTP`), and staged take-profit targets such as `TP1 3% 50% TP2 @ 53000 50%`. Staged text targets map to the same `take_profit_targets` structure as JSON alerts, and the parser rejects ambiguous prose rather than guessing.

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

Preview how a normalized bracket would map to exchange capabilities without placing any order:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8004/signals/exchange-plan -ContentType "application/json" -Body '{
  "symbol": "BTCUSDT",
  "side": "buy",
  "quote_amount": "100",
  "price": "50000",
  "stop_loss_pct": "2",
  "take_profit_pct": "4",
  "trailing_stop_pct": "3"
}'
```

The exchange-plan route is intentionally non-executing. For `exchange: "paper"` it returns `paper_synthetic_bracket`; for future CCXT/native venues it returns a conservative strategy label such as `attached_stop_loss_take_profit`, `entry_then_oco_after_fill`, `entry_then_trailing_stop`, `paper_required_for_mixed_bracket_trailing`, or `paper_required_for_staged_or_partial_bracket`. `live_order_safe` remains `false` because Auto-Crypto still does not submit live orders. Each planned exit leg includes `intent` (`protective_exit`, `profit_exit`, or `staleness_exit`), `position_effect`, `close_action` (`sell_to_close_long` or `buy_to_cover_short`), `activation_status`, `partial_close`, `exchange_order_family`, and trailing parameters such as callback, step, and activation fields when present. The plan `summary` counts protective, take-profit, trailing, pending-trailing, partial-trailing, staged take-profit, partial take-profit, and time-stop legs, and `execution_sequence` spells out the paper-safe order of operations: submit entry, wait for fill, then attach or track exits with the intended buy/sell close side. Reduce-only close signals that include bracket fields stay `single_order`, report `ignored_bracket_fields: true`, and emit `reduce_only_signal_ignores_bracket_exit_fields` so operators do not mistake a close ticket for a fresh bracket. Staged take-profits, partial take-profits, partial trailing exits, and time stops are classified as paper/custom-mapping requirements for non-paper venues even when the venue advertises basic bracket or trailing support.

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
- `volatility_pct`, `realized_volatility_pct`, `atr_pct`, or `atr_percent` so risk checks can reject entries during high-volatility regimes
- `stop_loss_pct`
- `stop_loss_price` or `stop_price`
- `take_profit_pct`
- `take_profit_price` or `target_price`
- `take_profit_targets` as a list of `{ "pct": "...", "close_pct": "..." }` or `{ "trigger_price": "...", "close_pct": "..." }` objects for staged exits
- `trailing_stop_pct`
- `trailing_stop_amount` or `trail_amount` to trail by a fixed quote-currency distance instead of a percent
- `trailing_stop_price` or `trail_price` to set the exact initial paper trailing trigger while `trailing_stop_pct` or `trailing_stop_amount` controls later ratchets
- `trailing_stop_close_pct` or `trail_close_pct` to make the paper trailing leg reduce only part of the original bracket quantity
- `trailing_step_pct`, `trail_step_pct`, `trailing_step_amount`, or `trail_step_amount` to reduce trailing-stop churn by requiring a minimum trigger improvement before ratcheting
- `trailing_activation_pct` or `trail_activation_pct`
- `trailing_activation_price`, `trail_activation_price`, or `activation_price`
- `trail_after_take_profit`, `trailing_after_take_profit`, or `trail_after_tp`
- `breakeven_trigger_pct`
- `breakeven_after_take_profit` or `move_stop_to_breakeven_after_tp`
- `max_hold_marks` or `time_stop_marks` to close a paper bracket after a fixed count of market-price marks
- `bracket`, `bracket_order`, or `exit_plan` object containing the same stop-loss, take-profit, trailing-stop, and break-even fields
- `max_slippage_bps`
- `volatility_pct`, `realized_volatility_pct`, `atr_pct`, or `atr_percent`
- `reduce_only`
- `strategy_id` or `strategy`
- `exchange` or `venue`
- `market_type`

Forbidden actions include `withdraw`, `transfer`, `internal_transfer`, and `deposit`.

Opening-side aliases include `buy`, `long`, `open_long`, `buy_to_open`, `sell`, `short`, `open_short`, `sell_short`, and `sell_to_open`. Reduce-only close aliases include `close`, `close_long`, `sell_to_close`, `reduce_long`, `close_short`, `buy_to_cover`, `cover_short`, and `reduce_short`. Close aliases intentionally ignore bracket fields for execution planning, so a close ticket cannot accidentally create fresh synthetic bracket exits.

## Risk Controls

Risk checks run before paper execution:

- `stop_loss_required`
- `max_order_notional_exceeded`
- `max_open_notional_exceeded`
- `max_symbol_open_notional_exceeded`
- `max_position_equity_pct_exceeded`
- `max_risk_per_trade_pct_exceeded`
- `max_entry_volatility_pct_exceeded`
- `max_leverage_exceeded`
- `max_slippage_exceeded`
- `consecutive_loss_limit_exceeded`
- `max_stop_loss_pct_exceeded`
- `max_trailing_stop_pct_exceeded`
- `trailing_stop_required_for_activation`
- `trailing_stop_required_for_step`
- `trailing_stop_required_for_close_pct`
- `trailing_stop_required_for_take_profit_delay`
- `trail_after_take_profit_requires_take_profit`
- `trailing_stop_pct_required_for_price`
- `invalid_trailing_stop_close_pct`
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
- `max_risk_amount_exceeded`
- `max_open_risk_amount_exceeded`
- `max_open_risk_equity_pct_exceeded`
- `pending_trailing_requires_fixed_stop`

Set `AUTO_CRYPTO_MAX_OPEN_NOTIONAL` above `0` to cap cumulative open long plus short paper exposure, and set `AUTO_CRYPTO_MAX_SYMBOL_OPEN_NOTIONAL` above `0` to cap concentration in one symbol before accepting another paper entry. Set `AUTO_CRYPTO_MAX_OPEN_RISK_AMOUNT` above `0` to cap aggregate active paper bracket loss at the nearest open protective stop/trailing trigger, and set `AUTO_CRYPTO_MAX_OPEN_RISK_EQUITY_PCT` above `0` to cap that same aggregate open bracket risk as a percentage of account equity. Set `AUTO_CRYPTO_MAX_POSITION_EQUITY_PCT` above `0` to limit a single ticket to a percentage of account equity. Set `AUTO_CRYPTO_MAX_RISK_AMOUNT` above `0` to cap the estimated quote-currency loss at the fixed stop for any bracket entry, whether the signal was sized by notional, base amount, `risk_amount`, or `risk_pct`. Set `AUTO_CRYPTO_MAX_RISK_PER_TRADE_PCT` above `0` to cap `risk_pct` sizing requests. Signals may include `volatility_pct`, `realized_volatility_pct`, or `atr_pct`; set `AUTO_CRYPTO_MAX_ENTRY_VOLATILITY_PCT` above `0` to reject entries when that supplied volatility regime is too high. Set `AUTO_CRYPTO_MAX_STOP_LOSS_PCT`, `AUTO_CRYPTO_MAX_TRAILING_STOP_PCT`, and `AUTO_CRYPTO_MIN_REWARD_RISK_RATIO` above `0` to reject alerts whose fixed stop or trailing stop is too wide or whose first take-profit does not justify the stop risk. Set `AUTO_CRYPTO_MIN_TOTAL_REWARD_RISK_RATIO` above `0` to evaluate the weighted reward/risk of every staged take-profit target by `close_pct`, and set `AUTO_CRYPTO_MAX_TAKE_PROFIT_TARGETS` above `0` to cap overly complex staged brackets. Absolute `stop_loss_price`, `take_profit_price`, and fixed `trailing_stop_amount` values are converted to entry-relative percentages for those same checks, and every staged absolute take-profit target is checked for the correct side of entry. Leave `AUTO_CRYPTO_REQUIRE_FIXED_STOP_FOR_PENDING_TRAILING=true` to reject activation-gated or take-profit-gated trailing stops unless a fixed stop-loss also protects the position before the trail arms; this follows current risk guidance to combine trailing stops with an initial stop. Set `AUTO_CRYPTO_MAX_CONSECUTIVE_LOSSES` above `0` to pause new entries after repeated losing bracket exits. SQLite-backed paper state restores open exposure and open bracket risk after restart, and triggered paper exits release exposure for later risk checks.

When `quote_amount` and `base_amount` are omitted, JSON signals may set `risk_amount` or `risk_pct` with a stop loss. Auto-Crypto computes the paper order notional as `risk budget / stop distance`. For example, `risk_pct: "1"` with `equity: 10000` and `stop_loss_pct: "5"` sizes a `2000` paper notional so the stop represents about `100` quote currency of paper risk before slippage.

## Research Notes

Current bot work is guided by paper-first risk controls and exchange order behavior:

- Bitsgap's 2026 bot-settings guide emphasizes stop loss, take profit, demo testing, and backtesting as core bot configuration checks, which is why Auto-Crypto surfaces reward/risk health and residual bracket exposure before any live execution is possible: <https://bitsgap.com/blog/how-to-choose-crypto-trading-bot-settings-in-2026-range-investment-stop-loss-and-take-profit>
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
- Binance's current futures position-mode docs distinguish one-way mode, where opening the opposite direction cancels or reduces the existing position, from hedge mode, where long and short positions can coexist; Auto-Crypto's paper bracket reversals follow the one-way netting model and persist `netted_quantity` plus `opened_quantity` before any venue-specific live behavior is enabled: <https://www.binance.com/en/support/faq/detail/360041513552>
- OKX describes one-way mode as allowing only one direction at a time, with buys or sells adjusting the existing position instead of opening a separate opposite position; this matches Auto-Crypto's paper-first reversal accounting for bracketed buy and sell signals: <https://www.okx.com/help/comparing-futures-mode-and-multi-currency-margin-mode>
- CCXT's order FAQ also recommends checking exchange feature flags for native take-profit and stop-loss support; this is why staged TP/SL simulation is recorded as paper behavior instead of assuming a portable live bracket implementation: <https://github.com/ccxt/ccxt/wiki/FAQ/9e4963a7b3438ba4fee47be1ec6922f4baf6684e>
- CCXT describes trailing orders as exchange-dependent, sometimes usable with `reduceOnly`, and able to trail by percentage or quote amount, so Auto-Crypto accepts `trailing_stop_pct`, fixed `trailing_stop_amount`, and exact paper trail starts while keeping live execution gated until adapter support is explicit: <https://docs.ccxt.com/docs/faq>
- CCXT's current manual describes attached stop-loss/take-profit orders, OCO-like linked exits, and exchange-dependent trailing parameters, so Auto-Crypto now exposes `/signals/exchange-plan` as a capability-based planning preview instead of assuming one portable live bracket implementation: <https://github.com/ccxt/ccxt/wiki/manual>
- Coinbase documents bracket and TP/SL behavior as paired exits where the triggered side executes and the other side cancels; exact `trailing_stop_price` remains paper-only because this venue behavior differs from portable trailing-order semantics: <https://help.coinbase.com/en/coinbase/trading-and-funding/advanced-trade/order-types>
- Coinbase's current bracket-order guidance describes a paired take-profit/stop-loss target where one side filling cancels the other, so Auto-Crypto now exposes `execution_sequence` and `exchange_order_family` in `/signals/exchange-plan` before any non-paper venue can be mapped: <https://help.coinbase.com/en/coinbase/trading-and-funding/advanced-trade/order-types>
- Coinbase Advanced Trade API attached TP/SL examples attach one stop-loss/take-profit configuration to the originating order, so Auto-Crypto treats staged or partial bracket shapes as `paper_required_for_staged_or_partial_bracket` unless a future adapter explicitly proves the native mapping: <https://docs.cdp.coinbase.com/coinbase-app/advanced-trade-apis/guides/orders>
- CCXT's current FAQ separates attached stop-loss/take-profit parameters, standalone closing orders, exchange-specific trailing support, and reduce-only handling, reinforcing why Auto-Crypto now labels each planned exit with an `exchange_order_family` instead of assuming every bracket/trailing combination is portable: <https://docs.ccxt.com/docs/faq>
- Coinbase says Advanced Trade supports programmatic trading, order management, and real-time market data over REST and WebSocket, so Auto-Crypto now marks Coinbase as a high-priority BTC/ETH/SOL venue while keeping execution behind paper planning and future native JWT review: <https://docs.cdp.coinbase.com/coinbase-app/advanced-trade-apis/overview>
- Coinbase's product endpoints list available trading pairs and product detail/candle data, which maps to Auto-Crypto's non-secret platform metadata for default BTC/USD, ETH/USD, and SOL/USD review pairs: <https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/products/list-products>
- Kraken's developer docs cover spot, derivatives, OTC, custody, and payments over REST, WebSocket, and FIX, and its Add Order endpoint requires signed private requests, so Auto-Crypto exposes Kraken adapter metadata but does not add a live order path: <https://docs.kraken.com/> and <https://docs.kraken.com/api-reference/trading/add-order>
- Binance.US documents REST and WebSocket access for market data, user data, account data, and trade order management, so Auto-Crypto tracks Binance.US separately from global Binance in the curated platform registry: <https://docs.binance.us/>
- Binance's official Spot API docs identify production REST base endpoints and separate Spot Testnet/Demo endpoints; Auto-Crypto therefore records global Binance as a BTC/ETH/SOL venue with `sandbox: "spot_testnet"` and keeps live submission disabled: <https://developers.binance.com/docs/binance-spot-api-docs/rest-api> and <https://developers.binance.com/docs/binance-spot-api-docs/testnet/rest-api/general-api-information>
- Binance Spot Testnet request-security docs state that API keys can be permission-scoped and that trading permission is not enabled by default, matching Auto-Crypto's `required_permissions` metadata and explicit live gate fields: <https://developers.binance.com/docs/binance-spot-api-docs/testnet/rest-api/request-security>
- Gemini's REST API docs cover order management, market data, derivatives, and fund management, while its create-order docs note the API does not directly support market orders and recommends aggressive protected limits instead; Auto-Crypto records Gemini as a paper-safe spot adapter target and keeps market-style behavior simulated until a native mapping is reviewed: <https://developer.gemini.com/rest-api/rest-api> and <https://developer.gemini.com/rest-api/trading/orders/create-new-order>
- Bot setting guidance consistently emphasizes stop loss, take profit, demo/paper testing, backtesting, and position sizing before live automation: <https://bitsgap.com/blog/how-to-choose-crypto-trading-bot-settings-in-2026-range-investment-stop-loss-and-take-profit>
- Recent crypto-bot guidance calls out multiple take-profit levels, partial position closing, dynamic stop-loss adjustment, and trailing stops as advanced strategy controls, so Auto-Crypto now supports `trail_after_take_profit` as a paper-only way to wait for a target fill before a trailing leg starts ratcheting: <https://tickerly.net/trading-bot-crypto-complete-guide-2026/>
- Current bot-setting guidance treats take-profit and stop-loss selection as part of the strategy's risk/reward profile, so Auto-Crypto now reports and can gate both first-target and weighted staged-target reward/risk before paper execution: <https://bitsgap.com/blog/how-to-choose-crypto-trading-bot-settings-in-2026-range-investment-stop-loss-and-take-profit>
- Recent crypto-bot risk guidance highlights fixed-fraction sizing, commonly around 1-2% per trade, plus stop-loss and drawdown limits before live automation; Auto-Crypto's `risk_pct` sizing stays paper-only and can be capped with `AUTO_CRYPTO_MAX_RISK_PER_TRADE_PCT`, while any fixed-stop bracket can also be capped by estimated quote loss with `AUTO_CRYPTO_MAX_RISK_AMOUNT`: <https://cryptorobot.ai/blog/essential-tips-managing-risks-crypto-trading-bots>
- The same risk guidance emphasizes portfolio-level drawdown limits alongside per-trade stops, so Auto-Crypto now offers aggregate paper bracket-risk caps with `AUTO_CRYPTO_MAX_OPEN_RISK_AMOUNT` and `AUTO_CRYPTO_MAX_OPEN_RISK_EQUITY_PCT` before any additional bracket entry is accepted: <https://cryptorobot.ai/blog/essential-tips-managing-risks-crypto-trading-bots>
- Current bot risk guidance emphasizes position controls, daily loss limits, and kill switches; Auto-Crypto now adds a per-symbol paper concentration cap through `AUTO_CRYPTO_MAX_SYMBOL_OPEN_NOTIONAL` before any live exchange path is considered: <https://blog.traderspost.io/article/best-automated-trading-bots-2025>
- Current automated trading guidance recommends volatility-aware sizing and portfolio-level stress tests rather than fixed trade sizes alone; Auto-Crypto can reject signals carrying high `volatility_pct` or `atr_pct` and can replay multiple stress scenarios through `/backtest/stress`: <https://www.tradingbotexperts.com/blog/best-automated-trading-platforms>
- Current crypto backtesting guidance emphasizes testing strategies on historical or simulated price paths before launch; Auto-Crypto's `/backtest/signal` endpoint applies that idea to bracket and trailing-stop paper logic without mutating active state: <https://bitsgap.com/blog/crypto-backtesting-guide-2025-tools-tips-and-how-bitsgap-helps>
- Current crypto-bot backtesting guidance warns that clean historical fills can hide slippage, fees, latency, and stressed-market liquidity; Auto-Crypto's backtest-only `fee_bps` and `slippage_bps` inputs make those assumptions explicit while keeping live execution disabled: <https://bitsgap.com/blog/crypto-bot-backtesting-in-2026-what-it-shows-and-what-it-cannot-predict>
- Recent backtesting guidance emphasizes replaying rules with realistic fees, slippage, position sizing, drawdown review, and forward testing before risking capital; Auto-Crypto's breakeven-after-TP option is therefore modeled first in paper exits and backtest snapshots: <https://coinbureau.com/guides/how-to-backtest-your-crypto-trading-strategy>
- Backtrader's slippage documentation notes that real-market conditions can miss requested prices and exposes configurable percentage/fixed slippage in simulation; Auto-Crypto uses the same idea in isolated paper backtest and paper-exchange cost knobs: <https://www.backtrader.com/docu/slippage/slippage/>
- Interactive Brokers' current walk-forward analysis guidance describes rolling in-sample/out-of-sample testing as a closer simulation of real trading conditions than one fixed historical backtest, which is why Auto-Crypto now supports labeled candle batches suitable for chunked walk-forward checks: <https://www.interactivebrokers.com/campus/ibkr-quant-news/the-future-of-backtesting-a-deep-dive-into-walk-forward-analysis/>
- Recent crypto backtesting guidance warns that out-of-sample and walk-forward checks help expose curve fitting; Auto-Crypto's conservative candle mode keeps same-bar stop/target ambiguity from overstating bracket performance: <https://stoic.ai/blog/backtesting-trading-strategies/>
- Current bot-launch guidance recommends out-of-sample validation, walk-forward testing, demo exchange testing, and gradual rollout before live exchange API use; Auto-Crypto keeps these additions in isolated paper backtests and active bracket snapshots only: <https://skyrexio.com/blog/no-code-crypto-trading-bot-how-to-build-an-algorithmic-strategy-backtest-it-and-launch-via-exchange-api/>
- Current crypto bot settings guidance continues to emphasize stop loss, take profit, demo testing, and backtesting as core setup steps; Auto-Crypto keeps time-stop exits paper-only so stale-trade rules can be tested beside price exits before any live rollout: <https://bitsgap.com/blog/how-to-choose-crypto-trading-bot-settings-in-2026-range-investment-stop-loss-and-take-profit>
- Recent crypto bot risk guidance recommends combining trailing stops with a fixed initial stop so risk is defined before the trailing logic activates; Auto-Crypto's pending-trailing risk gate rejects activation-gated or take-profit-gated trailing stops without a fixed stop-loss by default: <https://cryptorobot.ai/blog/essential-tips-managing-risks-crypto-trading-bots>
- Current order-type guidance describes OCO/bracket orders as paired conditional exits where one fill cancels the other, reinforcing why Auto-Crypto records `time_exit` sibling cancellation metadata in paper mode before considering exchange-native behavior: <https://bitsgap.com/blog/the-only-guide-to-order-types-youll-ever-need>
- Backtesting guidance for entry and exit rules emphasizes objective, structured exits; `max_hold_marks` adds a deterministic paper time-stop rule that can be replayed through `/backtest/signal` alongside stop-loss, take-profit, and trailing-stop paths: <https://www.fortraders.com/blog/backtesting-entry-and-exit-rules-best-practices>
- Current trailing-stop guidance describes the order type as a dynamic stop that follows favorable price movement and exits on reversal, which is why Auto-Crypto preview responses now show the simulated post-mark trailing trigger before applying the mark: <https://www.investopedia.com/terms/t/trailingstop.asp>
- Coinbase order-type guidance describes bracket orders as target exits for locking profit or avoiding losses; Auto-Crypto keeps those bracket updates synthetic and now exposes both current and previewed exit snapshots for operator review: <https://help.coinbase.com/en/coinbase/trading-and-funding/advanced-trade/order-types>
- Recent crypto backtesting guidance recommends analyzing drawdowns alongside returns, fees, slippage, and forward tests; Auto-Crypto's isolated backtest summary now includes closed-P&L drawdown/runup while live execution remains disabled: <https://coinbureau.com/guides/how-to-backtest-your-crypto-trading-strategy>
- Coinbase's current order-type guidance describes bracket orders as attached TP/SL exits used to manage risk with predefined levels, so `/signals/exchange-plan` now labels profit-taking and protective legs separately before any paper submission: <https://help.coinbase.com/en/coinbase/trading-and-funding/advanced-trade/order-types>
- Recent 2026 crypto-bot setup guidance treats stop loss, take profit, demo testing, and backtesting as core configuration choices, which is why the exchange-plan preview now exposes leg counts and partial-exit metadata instead of only a strategy label: <https://bitsgap.com/blog/how-to-choose-crypto-trading-bot-settings-in-2026-range-investment-stop-loss-and-take-profit>
- Current crypto-bot risk guidance recommends combining trailing stops with a fixed initial stop so initial risk is defined before trailing logic activates; Auto-Crypto now marks activation-gated trailing legs as pending in exchange-plan warnings and summaries: <https://cryptorobot.ai/blog/essential-tips-managing-risks-crypto-trading-bots>
- Backtesting guidance continues to warn that transaction costs and slippage can make clean simulations unrealistic, reinforcing why these exchange-plan details remain review metadata while execution stays paper-first and backtest cost assumptions stay explicit: <https://quantra.quantinsti.com/glossary/How-to-Avoid-Common-Mistakes-in-Backtesting>
- CCXT's current stop-loss/take-profit FAQ distinguishes attached TP/SL, standalone closing orders, and exchange-specific trailing support, which is why Auto-Crypto's aggregate bracket summary treats unarmed paper trailing stops separately from currently open protective exits: <https://docs.ccxt.com/docs/faq>
- Current crypto-bot risk guidance recommends combining trailing stops with a fixed initial stop so initial risk is defined before trailing logic activates; Auto-Crypto now keeps `pending_activation` trailing legs out of nearest-protective risk totals until paper price marks arm them: <https://cryptorobot.ai/blog/essential-tips-managing-risks-crypto-trading-bots>
- Freqtrade's current stoploss documentation describes trailing behavior that starts only after an offset and ignores stop changes that would loosen the stop, which matches Auto-Crypto's new paper profit-lock amendment guard: <https://www.freqtrade.io/en/stable/stoploss/>
- CCXT's current FAQ notes that attached stop-loss/take-profit and exchange-specific trailing support vary by venue, reinforcing why Auto-Crypto exposes bracket health and profit-lock controls as synthetic paper maintenance rather than live order modification: <https://docs.ccxt.com/docs/faq>
- CCXT's current base order spec exposes `reduceOnly`, trigger prices, stop-loss, and take-profit parameters as exchange-dependent order fields, and Binance documents OCO/trailing order constraints such as equal OCO quantities and per-symbol trailing support; Auto-Crypto therefore labels `sell_to_close_long` vs `buy_to_cover_short` in `/signals/exchange-plan` and keeps reduce-only bracket fields as warnings instead of live placement instructions: <https://docs.ccxt.com/docs/base-spec> and <https://developers.binance.com/docs/binance-spot-api-docs/rest-api/trading-endpoints>
- Coinbase's current Advanced Trade order guide describes bracket orders as a single sell order with linked limit and trigger prices where one side filling disables the other, and Coinbase Exchange TP/SL docs note only one TP/SL on one side; Auto-Crypto keeps staged or partial close plans paper-required and makes the close action explicit in the planning preview: <https://docs.cdp.coinbase.com/coinbase-app/advanced-trade-apis/guides/orders> and <https://docs.cdp.coinbase.com/exchange/fix-api/order-entry-messages/tpsl-orders>
- CCXT's current manual describes stop-loss and take-profit orders as trigger prices for closing a position, and recent crypto-bot risk guidance recommends combining trailing stops with a fixed initial stop before trailing activation; Auto-Crypto now rejects absolute bracket and trailing prices that sit on the wrong side of entry for the intended buy or short paper bracket: <https://github.com/ccxt/ccxt/wiki/manual> and <https://cryptorobot.ai/blog/essential-tips-managing-risks-crypto-trading-bots>
- Current backtesting guidance warns against look-ahead bias and unrealistic assumptions; Auto-Crypto persists `risk_pct`, `risk_amount`, and `max_hold_marks` through approval restarts so paper approvals replay the same rules that were originally reviewed instead of silently changing the test setup: <https://www.fortraders.com/blog/how-to-avoid-bias-in-backtesting>
- Binance's current trailing-stop FAQ describes trailing stops as dynamic contingent orders that move with favorable price action and trigger on reversal, while Freqtrade's current stoploss docs note that trailing stop changes should only tighten risk; Auto-Crypto's bracket path preview therefore shows a step-by-step synthetic ratchet/trigger sequence on a copied paper bracket before any operator applies real paper marks: <https://developers.binance.com/docs/binance-spot-api-docs/faqs/trailing-stop-faq> and <https://www.freqtrade.io/en/stable/stoploss/>
- CCXT's current FAQ separates attached TP/SL parameters, standalone closing orders, trailing support, and exchange-specific feature checks, so `/brackets/{signal_id}/decision-support` remains paper-only operator telemetry rather than an exchange order instruction: <https://docs.ccxt.com/docs/faq>
- Current 2026 bot-setting guidance still treats stop loss, take profit, demo testing, and backtesting as core setup work, so the decision-support endpoint surfaces exit health and trigger sequencing before any live venue mapping: <https://bitsgap.com/blog/how-to-choose-crypto-trading-bot-settings-in-2026-range-investment-stop-loss-and-take-profit>
- Walk-forward/backtesting guidance warns against false confidence from one fixed historical test, so Auto-Crypto exposes next-trailing-ratchet telemetry on active snapshots and path previews to make paper assumptions auditable mark by mark: <https://blog.quantinsti.com/walk-forward-optimization-introduction/>
- Recent backtesting guidance emphasizes measuring worst-case scenarios and drawdowns before sizing or trusting a strategy; Auto-Crypto's bracket candle preview therefore uses an adverse-first same-candle policy for active paper brackets instead of assuming a take-profit filled before a stop inside the same OHLC range: <https://blog.traderspost.io/article/how-to-backtest-trading-strategies>
- Current CCXT docs describe trailing orders by percentage or quote amount and as exchange-dependent with optional `reduceOnly`, while current crypto-bot risk guidance recommends pairing trailing logic with fixed initial stops; Auto-Crypto therefore lets strict text alerts express fixed trail amounts, exact trail/activation prices, and partial paper trailing closes while keeping execution synthetic and risk-gated: <https://docs.ccxt.com/docs/faq> and <https://cryptorobot.ai/blog/essential-tips-managing-risks-crypto-trading-bots>
- Current order-type guidance treats bracket/OCO exits as linked conditional orders, and current trading API guidance keeps bracket and trailing orders as venue-specific order families rather than one universal shape; Auto-Crypto therefore inventories reused synthetic OCA groups and flags cross-bracket reuse before any operator treats paper buy/sell bracket logic as portable live execution: <https://bitsgap.com/blog/the-only-guide-to-order-types-youll-ever-need> and <https://alpaca.markets/learn/how-to-build-stock-trading-bot-with-alpaca>
- CCXT's current FAQ notes that attached TP/SL and trailing support are exchange-specific, while Binance documents trailing stops as contingent orders that can cancel an OCO sibling when triggered; Auto-Crypto accepts several webhook bracket-leg shapes but normalizes them into one synthetic paper model with explicit partial-close caps before any venue adapter is allowed to submit live orders: <https://docs.ccxt.com/docs/faq> and <https://developers.binance.com/docs/binance-spot-api-docs/faqs/trailing-stop-faq>
- Bitunix's official futures market-data docs expose `GET /api/v1/futures/market/kline` with `symbol`, `interval`, optional time bounds, `limit` capped at `200`, and price type selection; Auto-Crypto uses that public kline response only as an isolated paper backtest input, not as a live execution trigger: <https://www.bitunix.com/api-docs/futures/market/get_kline.html>

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
AUTO_CRYPTO_MAX_SYMBOL_OPEN_NOTIONAL=0
AUTO_CRYPTO_MAX_OPEN_RISK_AMOUNT=0
AUTO_CRYPTO_MAX_OPEN_RISK_EQUITY_PCT=0
AUTO_CRYPTO_MAX_POSITION_EQUITY_PCT=0
AUTO_CRYPTO_MAX_RISK_AMOUNT=0
AUTO_CRYPTO_MAX_RISK_PER_TRADE_PCT=0
AUTO_CRYPTO_MAX_ENTRY_VOLATILITY_PCT=0
AUTO_CRYPTO_MAX_LEVERAGE=1
AUTO_CRYPTO_MAX_DAILY_LOSS=500
AUTO_CRYPTO_MAX_CONSECUTIVE_LOSSES=0
AUTO_CRYPTO_MAX_SLIPPAGE_BPS=100
AUTO_CRYPTO_REQUIRE_STOP_LOSS=true
AUTO_CRYPTO_REQUIRE_FIXED_STOP_FOR_PENDING_TRAILING=true
AUTO_CRYPTO_MAX_STOP_LOSS_PCT=0
AUTO_CRYPTO_MAX_TRAILING_STOP_PCT=0
AUTO_CRYPTO_MIN_REWARD_RISK_RATIO=0
AUTO_CRYPTO_MIN_TOTAL_REWARD_RISK_RATIO=0
AUTO_CRYPTO_MAX_TAKE_PROFIT_TARGETS=0

AUTO_CRYPTO_DEFAULT_EXCHANGE=paper
AUTO_CRYPTO_ALLOWED_EXCHANGES=paper

# Priority crypto exchange credentials. Keep live gates false by default.
AUTO_CRYPTO_COINBASE_API_KEY_NAME=
AUTO_CRYPTO_COINBASE_PRIVATE_KEY=
AUTO_CRYPTO_COINBASE_LIVE_ENABLED=false

AUTO_CRYPTO_KRAKEN_API_KEY=
AUTO_CRYPTO_KRAKEN_API_SECRET=
AUTO_CRYPTO_KRAKEN_LIVE_ENABLED=false

AUTO_CRYPTO_BINANCEUS_API_KEY=
AUTO_CRYPTO_BINANCEUS_API_SECRET=
AUTO_CRYPTO_BINANCEUS_LIVE_ENABLED=false

AUTO_CRYPTO_BINANCE_API_KEY=
AUTO_CRYPTO_BINANCE_API_SECRET=
AUTO_CRYPTO_BINANCE_LIVE_ENABLED=false

AUTO_CRYPTO_GEMINI_API_KEY=
AUTO_CRYPTO_GEMINI_API_SECRET=
AUTO_CRYPTO_GEMINI_LIVE_ENABLED=false

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

- `GET /bracket-templates`
- `GET /bracket-templates/{template_name}`
- `POST /webhooks/tradingview`
- `POST /webhooks/text-alert`
- `POST /signals/parse-text`
- `POST /signals/preview-text`
- `POST /signals/preview`
- `POST /signals/preview-template`
- `POST /signals/submit-template`
- `POST /signals/submit-text`
- `POST /signals/submit`
- `POST /backtest/signal`
- `POST /backtest/bitunix-klines`
- `POST /backtest/stress`

Paper market updates:

- `POST /market/price`
- `POST /market/price/preview`
- `GET /brackets`
- `GET /brackets/risk-summary`
- `GET /brackets/health`
- `GET /brackets/coverage`
- `GET /brackets/{signal_id}`
- `GET /brackets/{signal_id}/exit-ladder`
- `POST /brackets/{signal_id}/preview`
- `POST /brackets/{signal_id}/preview-path`
- `POST /brackets/{signal_id}/preview-candle`
- `POST /brackets/{signal_id}/stop`
- `POST /brackets/{signal_id}/trailing-stop`
- `POST /brackets/{signal_id}/trailing-stop/mark`
- `POST /brackets/{signal_id}/trailing-stop/preview-path`
- `POST /brackets/{signal_id}/take-profit`
- `POST /brackets/{signal_id}/breakeven`
- `POST /brackets/{signal_id}/lock-profit`
- `POST /brackets/{signal_id}/close`
- `POST /brackets/{signal_id}/close-protective`
- `POST /brackets/{signal_id}/cancel`

`POST /signals/preview` and `POST /signals/preview-text` return a `bracket_plan` object with the synthetic entry side, exit side, OCA group, trailing arming state, trailing activation price, stop/take-profit/trailing triggers, estimated notional and quantity, worst-case stop loss, equity risk percent, first-target reward/risk, and weighted total staged target reward/risk that would apply if the signal were submitted. The preview echoes both percentage and fixed-amount trail fields in the normalized signal payload.

`POST /backtest/signal` accepts a `signal` object plus a `prices` list, runs the signal through an isolated paper engine, marks each supplied price, and returns triggered exits, active exit snapshots after each mark, final paper P&L, final open notional, final positions, closed-P&L drawdown/runup, final mark-to-market unrealized P&L, final total P&L, and the applied cost assumptions. It does not save orders, write audit events, or mutate the active engine.

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

When a backtest ends with an open simulated bracket lot, `final_unrealized_pnl` marks the remaining quantity at `final_mark_price` and `final_total_pnl` combines realized and unrealized P&L. Add `close_final_positions: true` or `force_close_final: true` to close remaining paper bracket quantity at the final mark inside the sandbox; this records `final_close_triggers`, moves the P&L into `final_daily_pnl`, and leaves the live engine unchanged.

`POST /backtest/bitunix-klines` fetches Bitunix futures kline candles, normalizes them into the same candle backtest path, and returns the regular isolated paper summary plus a `market_data` block. It requires `symbol` and `interval`; optional `start_time`, `end_time`, `limit` up to `200`, and `price_type` map to the native Bitunix market-data request. This endpoint reads public market data only and does not enable Bitunix order execution.

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8004/backtest/bitunix-klines -ContentType "application/json" -Body '{
  "signal": {
    "symbol": "BTCUSDT",
    "side": "buy",
    "quote_amount": "100",
    "price": "60000",
    "stop_loss_pct": "3",
    "take_profit_pct": "6",
    "trailing_stop_pct": "2"
  },
  "symbol": "BTCUSDT",
  "interval": "15m",
  "limit": 100,
  "price_type": "LAST_PRICE",
  "close_final_positions": true
}'
```

`POST /backtest/stress` accepts one `signal` plus a `scenarios` list. Each scenario has a `name` and either `prices` or `candles`, and may carry its own `costs`. The response returns every isolated backtest plus an aggregate `worst_final_daily_pnl`, `worst_max_drawdown`, accepted/rejected counts, and total trigger count. This is useful for comparing normal, fee-shock, slippage-shock, and adverse candle paths without changing active paper orders.

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8004/backtest/stress -ContentType "application/json" -Body '{
  "signal": {
    "symbol": "BTCUSDT",
    "side": "buy",
    "quote_amount": "100",
    "price": "100",
    "stop_loss_pct": "5",
    "take_profit_pct": "10"
  },
  "scenarios": [
    {"name": "normal-target", "prices": ["104", "110"]},
    {"name": "fee-shock", "prices": ["110"], "costs": {"fee_bps": "100", "slippage_bps": "0"}}
  ]
}'
```

`POST /market/price/preview` returns the paper exits that would trigger at a hypothetical mark without mutating orders, positions, audit history, daily P&L, or exposure. It also returns `preview_active_exits` and `preview_positions`, which are sandbox snapshots after the hypothetical mark, so operators can see whether an activation-gated trailing stop would arm or ratchet before applying the mark.

`POST /market/price` applies the mark, returns any triggered exits, refreshes account open notional through the trading engine, and returns the current `active_exits` snapshot, including ratcheted trailing-stop trigger prices, percentage or amount trail distance, activation state, activation price, trigger distance, and water marks.

`GET /brackets` returns active synthetic paper brackets grouped by signal, including a summary of remaining notional, nearest open protective trigger, worst-case stop loss, and first-target reward/risk when available. `GET /brackets/risk-summary` aggregates active paper brackets across the portfolio with remaining notional, worst-case stop risk, locked protective P&L, target reward, trailing-stop counts, pending trailing-stop counts, and time-stop counts. `GET /brackets/health` returns per-bracket attention flags for missing open protective exits, still-risking protective exits, pending trailing stops, and missing take-profit exits. `GET /brackets/{signal_id}` returns the same summary plus active synthetic paper exit legs for one signal. `POST /brackets/{signal_id}/preview` previews only that bracket against a mark without mutating active state and returns both the current `active_exits` and sandboxed `preview_active_exits` after the hypothetical mark. `POST /brackets/{signal_id}/trailing-stop/preview-path` replays only the trailing-stop state across hypothetical marks and returns before/after activation and ratchet flags without recording orders or changing active water marks. `POST /brackets/{signal_id}/stop` tightens a paper stop without loosening risk, persists an amendment order, and records audit context. `POST /brackets/{signal_id}/trailing-stop` tightens a paper trailing trigger without loosening risk, persists an amendment order, and records audit context. `POST /brackets/{signal_id}/trailing-stop/mark` derives the next paper trailing trigger from a favorable mark while honoring activation, take-profit gating, trailing-step thresholds, and no-loosening guards. `POST /brackets/{signal_id}/breakeven` moves open protective exits to entry when it tightens risk, persists an amendment order, and records audit context. `POST /brackets/{signal_id}/lock-profit` moves open protective exits to an entry-relative profit-lock trigger when doing so tightens risk, persists an amendment order, and records audit context. `POST /brackets/{signal_id}/close` closes or partially reduces the selected synthetic paper bracket at the supplied mark, persists a reduce-only close or reduce order, records realized paper P&L, and cancels remaining exits only when the bracket is fully closed. `POST /brackets/{signal_id}/cancel` removes those synthetic exits, persists a cancellation order, and records audit context without closing the position.

Bracket summaries now include `protective_distance_pct`, `protective_locked_pnl`, `total_target_reward`, and `total_target_reward_risk_ratio`. A negative protective distance means the stop/trailing trigger has moved beyond entry and the paper bracket has locked in profit if that protective exit fires at the trigger. `worst_case_loss` floors at zero once the protective exit is at or beyond breakeven. Activation-gated trailing stops are excluded from nearest-protective calculations until they move from `pending_activation` to `open`, so dormant trails do not understate current stop risk. For staged exits, total target reward is weighted by each target's `close_pct`, so operators can compare the whole bracket plan instead of only the nearest target.

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

`GET /exchanges/platforms` returns the curated integration backlog and readiness state for high-priority crypto venues. Each row includes driver type, CCXT mapping when available, market types, API coverage, BTC/ETH/SOL priority assets, default review symbols, sandbox posture, required permission labels, credential-field status, documentation URL, adapter scope, and live-execution gate state.

Current platforms:

| Platform | Exchange ID | Adapter path | Markets |
| --- | --- | --- | --- |
| Coinbase Advanced Trade | `coinbase` | CCXT now, native planned | spot, derivatives |
| Kraken | `kraken` | CCXT | spot, margin, futures |
| Binance.US | `binanceus` | CCXT | spot, OTC |
| Binance | `binance` | CCXT, sandbox planned | spot, margin, swaps, futures, options |
| Gemini | `gemini` | CCXT, sandbox-aware metadata | spot |
| Bitstamp | `bitstamp` | CCXT | spot |
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
Invoke-RestMethod http://127.0.0.1:8004/exchanges/binance/integration
Invoke-RestMethod http://127.0.0.1:8004/exchanges/binanceus/integration
Invoke-RestMethod http://127.0.0.1:8004/exchanges/deribit/integration
Invoke-RestMethod http://127.0.0.1:8004/exchanges/bitmex/integration
Invoke-RestMethod http://127.0.0.1:8004/exchanges/coinbase/integration
```

Use `.[exchange]` dependencies to let CCXT-backed platforms report `adapter_ready`. Platform metadata is non-secret review data; it does not enable live execution. Keep `AUTO_CRYPTO_ALLOWED_EXCHANGES=paper` unless you are deliberately testing a non-paper path, keep every `AUTO_CRYPTO_<VENUE>_LIVE_ENABLED=false`, and use trade-only keys without withdrawal permission. A native Robinhood adapter and richer native Alpaca broker flows require separate request-signing implementations before live execution can be considered.

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
Invoke-RestMethod "http://127.0.0.1:8004/exchanges/bitunix/futures/klines?symbol=BTCUSDT&interval=15m&limit=100"
Invoke-RestMethod "http://127.0.0.1:8004/exchanges/bitunix/futures/account?margin_coin=USDT"
```

The kline check uses Bitunix's public futures market-data endpoint. The private account check signs the request using Bitunix's required `api-key`, `nonce`, `timestamp`, and `sign` headers. The bot never returns or logs the secret key.

## Persistence

When SQLite is enabled:

- Signal IDs are claimed with insert-only idempotency writes.
- Duplicate signal IDs after restart return `status=duplicate`.
- Pending approvals persist across restarts.
- Pending approval payloads preserve `risk_amount`, `risk_pct`, and `max_hold_marks` so approved paper orders match the reviewed risk-sized bracket and time-stop plan after restart.
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
