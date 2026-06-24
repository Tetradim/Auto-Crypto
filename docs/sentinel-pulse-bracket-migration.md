# Sentinel Pulse Bracket Migration Report

Date: 2026-06-23

## Summary

Sentinel Pulse has useful scalper-loop mechanics, but its bracket implementation is tightly coupled to stock tickers, MongoDB ticker documents, websocket broadcasts, Telegram alerts, and broker-account routing. Auto-Crypto should not copy that implementation directly. The right migration path is to port the deterministic ideas into small crypto-native modules and keep Auto-Crypto's existing paper-first bracket engine as the execution source of truth.

## Sentinel Code Reviewed

- `backend/trading/brackets.py`: drift-based auto-rebracketing, previous bracket snapshots, partial buy and sell legs, stop-loss handling, trailing stops for residual positions, and rebracket-on-flat-position behavior.
- `backend/trading/ticker_evaluation.py`: tight evaluation loop that routes strategy signals first, then bracket buy/sell/stop/trailing logic.
- `backend/trading/trade_accounting.py`: re-entry cooldown after exits, loss accounting, per-symbol auto-stop, and global daily drawdown stop.
- `backend/trading/engine_state.py`: runtime state persistence for recent prices, last rebracket timestamps, trailing highs, pending sells, and market-hours mode switching.
- `backend/risk_controls.py`: hierarchical kill switches, close-only/no-new-entry restrictions, exposure limits, symbol restrictions, fat-finger checks, and projected order checks.
- `backend/advanced_risk.py`: explainable risk score, liquidity-aware size recommendation, VaR/CVaR estimate, and stress-adjusted circuit breaker recommendations.
- Sentinel tests around `test_engine_stress_simulation.py`, `test_reentry_cooldown.py`, `test_pre_trade_projected_exposure.py`, and `test_trade_accounting_exposure.py`.

## Port To Auto-Crypto

1. **Drift-based rebracket planner**
   - Port the idea, not the Mongo-dependent code.
   - Keep recent mark history per symbol/bracket.
   - Trigger only when drift exceeds both `threshold` and `min_drift`.
   - Respect cooldown and lookback.
   - Preserve a previous bracket snapshot so an operator can revert.
   - Express new brackets as preview/amendment plans first; never directly mutate live venue orders without approval.

2. **Scalper bracket profile**
   - Add a reusable profile that maps a center price plus tight spread/buffer into a safe crypto bracket plan.
   - Support long and short directions.
   - Use percent or quote-distance controls because crypto symbols vary widely in price.
   - Feed Auto-Crypto's existing `CryptoSignal`/`PaperExchange` path rather than creating a separate position model.

3. **Re-entry cooldown**
   - Port Sentinel's "after an exit, block fresh entry for N seconds" guard.
   - Apply per symbol and optionally per strategy.
   - Allow reduce-only exits during cooldown.
   - Persist cooldown state through Auto-Crypto's SQLite repository before using it for live paths.

4. **Projected exposure checks**
   - Auto-Crypto already checks open notional and bracket risk, but Sentinel's projected pre-trade checks are useful.
   - Add projected portfolio/symbol checks that account for the new order before accepting it.
   - Keep decisions explainable through reason codes.

5. **Hierarchical restrictions**
   - Port the concepts of `no_new_entries`, `close_only`, and `hard_block`.
   - Map them into Auto-Crypto's existing global halt and risk decision model.
   - This is important for funding windows, liquidation danger, exchange degradation, and operator controls.

6. **Risk score as advisory metadata**
   - Sentinel's advanced score is useful as a transparent warning layer.
   - It should not replace deterministic hard limits for crypto futures.
   - Crypto-specific drivers should include leverage, liquidation distance, funding rate, volatility, spread/slippage, market state, and exchange health.

## Leave Out Or Rework Heavily

1. **US market-hours logic**
   - Sentinel's opening-bell rules, PDT wait-day behavior, and stock market sessions do not belong in a 24/7 crypto bot.
   - Rework the idea into crypto market-state windows: funding windows, liquidity gaps, volatility spikes, exchange maintenance, and weekend/liquidity regimes.

2. **Opening-bell forced trailing and halve-stop rules**
   - These are stock-session controls.
   - For crypto, replace with market-state-based stop tightening during high-volatility or liquidation-risk states.

3. **Mongo ticker document mutation**
   - Auto-Crypto uses normalized signals, paper orders, brackets, and SQLite persistence.
   - Do not introduce Sentinel's ticker-doc-as-strategy-state pattern.

4. **Direct broker routing logic**
   - Sentinel's broker allocation and live broker execution code is not portable to crypto exchanges.
   - Auto-Crypto should keep exchange adapters behind a clean venue interface and keep live execution disabled by default.

5. **Compounding buy power**
   - Automatically increasing position size after wins is risky for leveraged crypto.
   - If added, keep it paper-only and approval-gated.

6. **Stock-share liquidity assumptions**
   - Sentinel's average-daily-volume and share participation model needs crypto replacements: quote volume, order book depth, spread, taker/maker fees, funding, and per-contract constraints.

## Implementation Order

1. Add pure scalper/rebracket domain logic with tests.
2. Wire rebracket previews into paper bracket endpoints.
3. Add re-entry cooldown checks to signal intake and approval execution.
4. Add hierarchical restriction state and reason codes.
5. Add futures risk fields: margin mode, liquidation price estimate, liquidation distance, and funding guard.
6. Add market-state evaluator and use it to set sizing multipliers, approval requirements, and no-new-entry states.
7. Expand exchange adapters to expose funding, positions, balances, symbol filters, and private reconciliation before any live order path.

## First Safe Slice

Implemented in this slice:

- `autocrypto.scalper`: pure Sentinel-style rebracket planner, tight price-band signal payload generator, and re-entry cooldown utility.
- `POST /scalper/rebracket/preview`: preview-only API for drift-based band moves and suggested Auto-Crypto signal payloads.
- `autocrypto.futures_risk`: futures liquidation, stop-before-liquidation, leverage, and adverse-funding checks.
- `POST /futures/risk/preview`: preview-only futures risk assessment endpoint.
- `autocrypto.market_state`: 24/7 crypto market-state evaluator for normal, stressed, and halted entry states.
- `POST /market/state/preview`: preview-only market-state endpoint for entry controls and sizing multiplier.

## Backend Migration Slice

Implemented after the first safe slice:

- `autocrypto.protections`: scoped `no_new_entries`, `close_only`, and `hard_block` rules with expiry, precedence, and reason codes.
- Repository runtime state: generic persisted JSON state for protections, runtime config, cooldown timestamps, and scalper bracket state.
- Runtime controls in signal previews and intake:
  - protection rules block or allow entries/reduce-only signals consistently;
  - re-entry cooldowns persist across restarts and block fresh entries after reduce-only exits;
  - market-state snapshots can force no-new-entry rejection or require approval;
  - futures risk runs against swap/futures signals when entry, stop, notional, and leverage are available.
- `autocrypto.advisory_risk`: explainable advisory score for leverage, liquidation distance, funding, volatility, spread, market state, and exchange degradation. It is metadata only and does not replace deterministic hard gates.
- Scalper state APIs:
  - `GET /scalper/state/{symbol}`;
  - `POST /scalper/rebracket/apply`;
  - `POST /scalper/rebracket/revert`.
  These persist planned bands and previous-band snapshots without submitting or amending exchange orders.
- Protection/runtime APIs:
  - `GET /runtime/config`;
  - `POST /runtime/config`;
  - `GET /protections`;
  - `POST /protections/rules`;
  - `DELETE /protections/rules/{rule_id}`;
  - `POST /protections/preview`.
- Exchange adapter status contract:
  - `GET /exchanges/{exchange_id}/adapter-status`;
  - paper adapter balances, positions, funding support, symbol filters, and reconciliation status;
  - generic native/CCXT status for future private reconciliation work.

Still intentionally not implemented:

- Live venue order placement from these decisions.
- Automatic mutation of active brackets from market-state changes.
- Exchange private reconciliation loops.
- Discord-specific bot login/session handling.
- UI controls for the new runtime state.

Those should be added only after the new control-plane state is exposed in the operator UI, exercised in sandbox exchange environments, and reviewed per venue API.
