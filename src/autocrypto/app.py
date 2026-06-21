from __future__ import annotations

import os
from copy import deepcopy
from collections.abc import Callable
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .approvals import ApprovalQueue
from .backtest import run_signal_backtest, run_signal_candle_backtest, run_signal_stress_backtest
from .brackets import (
    active_exit_payload,
    bracket_coverage_payload,
    decimal_to_plain,
    exit_close_quantity,
    exit_distance,
    exit_intent,
    exit_ladder_sort_key,
    exit_order_payload,
    exit_pnl,
    trailing_activation_price,
    trailing_ratchet_impacts,
)
from .bracket_templates import apply_bracket_template, get_bracket_template, list_bracket_templates
from .bot_event_bus import BotEvent, event_bus
from .config import load_settings
from .edge_actions import apply_edge_action
from .engine import TradingEngine
from .exchanges.bitunix_adapter import (
    BitunixConfigurationError,
    BitunixRequestError,
    BitunixRestClient,
    bitunix_kline_candles,
    bitunix_credentials_configured,
    bitunix_live_execution_enabled,
    load_bitunix_credentials_from_env,
)
from .exchanges.ccxt_adapter import (
    CcxtExchangeAdapter,
    CcxtNotInstalledError,
    ExchangeCapabilities,
    list_ccxt_exchange_ids,
)
from .exchanges.order_planner import plan_bracket_execution
from .exchanges.platform_registry import get_platform, platform_rows
from .execution import ExecutionCostConfig, PaperExchange, build_exit_orders
from .intake import SignalIntakeService
from .repository import SQLiteRepository
from .risk import AccountState, RiskConfig, RiskDecision, evaluate_signal
from .security import (
    InMemoryWebhookReplayStore,
    WebhookReplayError,
    WebhookSignatureError,
    verify_webhook_signature,
)
from .signals import CryptoSignal, SignalValidationError, normalize_signal, normalize_symbol
from .strategy_presets import apply_strategy_preset, get_strategy_preset, list_strategy_presets
from .text_signals import parse_text_signal


def create_app(
    *,
    exchange: PaperExchange | None = None,
    risk_config: RiskConfig | None = None,
    account_state: AccountState | None = None,
    webhook_secret: str | None = None,
    webhook_clock: Callable[[], float] | None = None,
    webhook_tolerance_seconds: int | None = None,
    repository: SQLiteRepository | None = None,
    require_approval: bool = False,
) -> FastAPI:
    app = FastAPI(title="Auto-Crypto", version="0.1.0")
    static_dir = Path(__file__).with_name("static")
    if static_dir.exists():
        app.mount("/ui/static", StaticFiles(directory=static_dir), name="ui-static")

    paper_exchange = exchange
    if paper_exchange is None:
        paper_exchange = PaperExchange.from_order_history(repository.list_orders()) if repository else PaperExchange()
    if account_state is None:
        account_state = AccountState(
            open_notional=paper_exchange.open_notional(),
            open_risk_amount=paper_exchange.open_risk_amount(),
        )
    engine = TradingEngine(
        exchange=paper_exchange,
        risk_config=risk_config or RiskConfig(),
        account_state=account_state,
    )
    secret = webhook_secret if webhook_secret is not None else os.getenv("AUTO_CRYPTO_WEBHOOK_SECRET")
    replay_store = InMemoryWebhookReplayStore()
    intake = SignalIntakeService(
        engine=engine,
        approvals=ApprovalQueue(),
        repository=repository,
        require_approval=require_approval,
    )

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "default_mode": "paper",
            "orders": len(engine.exchange.orders),
            "halted": engine.halted,
            "halt_reason": engine.halt_reason,
        }

    @app.get("/", include_in_schema=False)
    @app.get("/ui", include_in_schema=False)
    def ui_index() -> FileResponse:
        index_path = static_dir / "index.html"
        if not index_path.exists():
            raise HTTPException(status_code=404, detail="operator UI is not installed")
        return FileResponse(index_path)

    @app.get("/ui/state")
    def ui_state() -> dict[str, Any]:
        orders_payload = repository.list_orders() if repository else [order.to_dict() for order in engine.exchange.orders]
        return {
            "health": {
                "status": "ok",
                "default_mode": "paper",
                "orders": len(engine.exchange.orders),
                "halted": engine.halted,
                "halt_reason": engine.halt_reason,
            },
            "control": {"halted": engine.halted, "reason": engine.halt_reason},
            "execution": {
                "require_approval": require_approval,
                "submit_intent": "queue_for_approval" if require_approval else "paper_order",
            },
            "risk": _risk_config_to_dict(engine.risk_config),
            "account": _account_state_to_dict(engine.account_state),
            "orders": orders_payload,
            "positions": engine.exchange.list_positions(),
            "signals": repository.list_signals() if repository else [],
            "approvals": intake.list_approvals(),
            "audit": [event.to_dict() for event in repository.list_audit()] if repository else [],
            "active_exits": _active_exits_to_dict(engine.exchange.lots),
        }

    @app.get("/control/status")
    def control_status() -> dict[str, Any]:
        return {"halted": engine.halted, "reason": engine.halt_reason}

    @app.post("/bus/events")
    async def publish_bus_event(event: BotEvent) -> dict[str, Any]:
        accepted = event_bus.publish(event)
        result: dict[str, Any] | None = None
        if event.event_type == "edge.action":
            result = apply_edge_action(event=accepted, engine=engine, repository=repository)
        return {
            "status": "accepted",
            "event": accepted.model_dump(mode="json"),
            "result": result,
        }

    @app.get("/bus/events")
    def recent_bus_events(limit: int = 100, event_type: str | None = None) -> dict[str, Any]:
        return {"events": event_bus.recent(limit=limit, event_type=event_type)}

    @app.post("/bus/edge-actions")
    async def publish_edge_action(payload: dict[str, Any]) -> dict[str, Any]:
        event = event_bus.publish(
            BotEvent(
                event_type="edge.action",
                source_bot="sentinel-edge",
                correlation_id=str(payload.get("idempotency_key") or ""),
                dedupe_key=str(payload.get("idempotency_key") or ""),
                target_bots=["auto-crypto"],
                payload={"contract_version": "edge.action.v1", **payload},
            )
        )
        result = apply_edge_action(event=event, engine=engine, repository=repository)
        return {"status": "accepted", "event": event.model_dump(mode="json"), "result": result}

    @app.post("/control/halt")
    async def halt(request: Request) -> dict[str, Any]:
        payload = await request.json()
        reason = str(payload.get("reason") or "manual halt")
        engine.halt(reason)
        if repository:
            repository.record_audit("trading.halted", {"reason": reason})
        return {"halted": True, "reason": reason}

    @app.post("/control/resume")
    def resume() -> dict[str, Any]:
        engine.resume()
        if repository:
            repository.record_audit("trading.resumed", {})
        return {"halted": False, "reason": ""}

    def verify_signed_request(request: Request, body: bytes) -> None:
        try:
            verify_webhook_signature(
                secret=secret,
                body=body,
                timestamp=request.headers.get("x-auto-crypto-timestamp"),
                signature=request.headers.get("x-auto-crypto-signature"),
                clock=webhook_clock,
                tolerance_seconds=webhook_tolerance_seconds,
                replay_store=replay_store,
            )
        except WebhookSignatureError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except WebhookReplayError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/webhooks/tradingview")
    async def tradingview_webhook(request: Request) -> dict[str, Any]:
        body = await request.body()
        verify_signed_request(request, body)

        payload = await request.json()
        try:
            signal = normalize_signal(payload, source="tradingview")
        except SignalValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return intake.handle(signal)

    @app.post("/webhooks/text-alert")
    async def text_alert_webhook(request: Request) -> dict[str, Any]:
        body = await request.body()
        verify_signed_request(request, body)
        payload = await request.json()
        try:
            signal = parse_text_signal(str(payload.get("message") or ""), source="text-alert")
        except SignalValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return intake.handle(signal)

    @app.get("/orders")
    def orders() -> dict[str, Any]:
        if repository:
            return {"orders": repository.list_orders()}
        return {"orders": [order.to_dict() for order in engine.exchange.orders]}

    @app.get("/positions")
    def positions() -> dict[str, Any]:
        return {"positions": engine.exchange.list_positions()}

    @app.get("/exchanges")
    def exchanges() -> dict[str, Any]:
        exchange_rows = [_exchange_row("paper", "paper"), _bitunix_exchange_row()]
        try:
            ccxt_exchange_ids = list_ccxt_exchange_ids()
        except CcxtNotInstalledError:
            return {"ccxt_available": False, "exchanges": exchange_rows}

        exchange_rows.extend(_exchange_row(exchange_id, "ccxt") for exchange_id in ccxt_exchange_ids)
        return {"ccxt_available": True, "exchanges": exchange_rows}

    @app.get("/exchanges/platforms")
    def exchange_platforms() -> dict[str, Any]:
        try:
            ccxt_exchange_ids = set(list_ccxt_exchange_ids())
        except CcxtNotInstalledError:
            return {"ccxt_available": False, "platforms": platform_rows(None)}
        return {"ccxt_available": True, "platforms": platform_rows(ccxt_exchange_ids)}

    @app.get("/exchanges/{exchange_id}/capabilities")
    def exchange_capabilities(exchange_id: str) -> dict[str, Any]:
        if exchange_id == "paper":
            return {"capabilities": _paper_capabilities().to_dict()}
        if exchange_id == "bitunix":
            client = BitunixRestClient(credentials=load_bitunix_credentials_from_env())
            return {"capabilities": client.capabilities().to_dict()}
        platform = get_platform(exchange_id)
        if platform and platform.ccxt_id:
            exchange_id = platform.ccxt_id
        try:
            capabilities = CcxtExchangeAdapter(exchange_id).capabilities()
        except CcxtNotInstalledError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"capabilities": capabilities.to_dict()}

    @app.get("/exchanges/{exchange_id}/integration")
    def exchange_integration(exchange_id: str) -> dict[str, Any]:
        platform = get_platform(exchange_id)
        if platform is None:
            raise HTTPException(status_code=404, detail=f"unsupported platform: {exchange_id}")

        try:
            ccxt_exchange_ids = set(list_ccxt_exchange_ids())
        except CcxtNotInstalledError:
            ccxt_exchange_ids = None

        payload: dict[str, Any] = {"platform": platform.to_dict(ccxt_exchange_ids=ccxt_exchange_ids)}
        if platform.exchange_id == "bitunix":
            payload["capabilities"] = BitunixRestClient(credentials=load_bitunix_credentials_from_env()).capabilities().to_dict()
            return payload
        if platform.ccxt_id and platform.driver_available(ccxt_exchange_ids):
            try:
                payload["capabilities"] = CcxtExchangeAdapter(platform.ccxt_id).capabilities().to_dict()
            except (CcxtNotInstalledError, ValueError) as exc:
                payload["capability_error"] = str(exc)
        return payload

    @app.get("/exchanges/bitunix/futures/tickers")
    def bitunix_futures_tickers(symbols: str | None = None) -> dict[str, Any]:
        try:
            return BitunixRestClient(credentials=load_bitunix_credentials_from_env()).get_futures_tickers(symbols)
        except BitunixRequestError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/exchanges/bitunix/futures/klines")
    def bitunix_futures_klines(
        symbol: str,
        interval: str,
        start_time: int | None = None,
        end_time: int | None = None,
        limit: int | None = None,
        price_type: str | None = None,
    ) -> dict[str, Any]:
        try:
            payload = BitunixRestClient(credentials=load_bitunix_credentials_from_env()).get_futures_klines(
                symbol,
                interval,
                start_time=start_time,
                end_time=end_time,
                limit=limit,
                price_type=price_type,
            )
            return {"source": "bitunix", "raw": payload, "candles": bitunix_kline_candles(payload)}
        except BitunixRequestError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/exchanges/bitunix/futures/account")
    def bitunix_futures_account(margin_coin: str = "USDT") -> dict[str, Any]:
        try:
            return BitunixRestClient(credentials=load_bitunix_credentials_from_env()).get_futures_account(margin_coin)
        except BitunixConfigurationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except BitunixRequestError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    async def _market_price_payload(request: Request) -> tuple[str, Decimal, bool]:
        payload = await request.json()
        try:
            symbol = normalize_symbol(payload.get("symbol"))
            price = _positive_decimal(payload.get("price"))
        except (SignalValidationError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        include_order_metadata = str(payload.get("include_order_metadata") or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        return symbol, price, include_order_metadata

    @app.post("/market/price/preview")
    async def market_price_preview(request: Request) -> dict[str, Any]:
        symbol, price, _include_order_metadata = await _market_price_payload(request)
        live_lots = deepcopy(engine.exchange.lots)
        preview_exchange = engine.exchange.preview_price_exchange(symbol, price)
        return {
            "symbol": symbol,
            "price": str(price),
            "would_trigger": engine.exchange.preview_price(symbol, price),
            "trailing_ratchets": trailing_ratchet_impacts(live_lots, preview_exchange.lots),
            "active_exits": _active_exits_to_dict(engine.exchange.lots, mark_price=price),
            "preview_active_exits": _active_exits_to_dict(preview_exchange.lots, mark_price=price),
            "positions": engine.exchange.list_positions(),
            "preview_positions": preview_exchange.list_positions(),
            "account": _account_state_to_dict(engine.account_state),
        }

    @app.post("/market/price")
    async def market_price(request: Request) -> dict[str, Any]:
        symbol, price, include_order_metadata = await _market_price_payload(request)
        order_offset = len(engine.exchange.orders)
        before_lots = deepcopy(engine.exchange.lots)
        update = engine.mark_price(symbol, price)
        triggered = (
            _triggered_with_order_metadata(update.triggered, engine.exchange.orders[order_offset:])
            if include_order_metadata
            else update.triggered
        )
        if repository:
            for order in engine.exchange.orders[order_offset:]:
                repository.save_order(order)
            if update.triggered:
                repository.record_audit(
                    "exit.triggered",
                    {"symbol": symbol, "price": str(price), "triggered": triggered},
                )
        return {
            "symbol": symbol,
            "price": str(price),
            "triggered": triggered,
            "trailing_ratchets": trailing_ratchet_impacts(before_lots, engine.exchange.lots),
            "active_exits": _active_exits_to_dict(engine.exchange.lots, mark_price=price),
            "realized_pnl_delta": str(update.realized_pnl_delta),
            "daily_pnl": str(update.daily_pnl),
            "consecutive_losses": update.consecutive_losses,
            "open_notional": str(update.open_notional),
            "positions": engine.exchange.list_positions(),
        }

    @app.get("/brackets")
    def list_brackets() -> dict[str, Any]:
        return {"brackets": _active_brackets_to_dict(engine.exchange.lots)}

    @app.get("/brackets/risk-summary")
    def bracket_risk_summary() -> dict[str, Any]:
        return {"summary": _bracket_risk_summary(engine.exchange.lots)}

    @app.get("/brackets/health")
    def bracket_health() -> dict[str, Any]:
        return {"health": _bracket_health(engine.exchange.lots)}

    @app.get("/brackets/coverage")
    def bracket_coverage() -> dict[str, Any]:
        active_lots = [
            lot
            for lot in engine.exchange.lots
            if lot.remaining_quantity > 0 and lot.exit_orders
        ]
        return {
            "bracket_count": len(active_lots),
            "coverage": [_bracket_coverage_to_dict(lot) for lot in active_lots],
        }

    @app.get("/brackets/oca-groups")
    def bracket_oca_groups() -> dict[str, Any]:
        return _bracket_oca_groups(engine.exchange.lots)

    @app.get("/brackets/{signal_id}")
    def bracket_status(signal_id: str) -> dict[str, Any]:
        lots = [
            lot
            for lot in engine.exchange.lots
            if lot.signal_id == signal_id and lot.remaining_quantity > 0 and lot.exit_orders
        ]
        exits = _active_exits_to_dict(lots, signal_id=signal_id)
        if not exits:
            raise HTTPException(status_code=404, detail="active bracket not found")
        return {"signal_id": signal_id, "summary": _bracket_summary(lots[0]), "active_exits": exits}

    @app.get("/brackets/{signal_id}/exit-ladder")
    def bracket_exit_ladder(signal_id: str, mark_price: str | None = None) -> dict[str, Any]:
        lots = [
            lot
            for lot in engine.exchange.lots
            if lot.signal_id == signal_id and lot.remaining_quantity > 0 and lot.exit_orders
        ]
        if not lots:
            raise HTTPException(status_code=404, detail="active bracket not found")
        parsed_mark_price: Decimal | None = None
        if mark_price not in (None, ""):
            try:
                parsed_mark_price = _positive_decimal(mark_price)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "signal_id": signal_id,
            "symbol": lots[0].symbol,
            "direction": lots[0].direction,
            "mark_price": str(parsed_mark_price) if parsed_mark_price is not None else None,
            "ladders": [_bracket_exit_ladder_to_dict(lot, mark_price=parsed_mark_price) for lot in lots],
        }

    @app.get("/brackets/{signal_id}/decision-support")
    def bracket_decision_support(signal_id: str, mark_price: str | None = None) -> dict[str, Any]:
        lots = [
            lot
            for lot in engine.exchange.lots
            if lot.signal_id == signal_id and lot.remaining_quantity > 0 and lot.exit_orders
        ]
        if not lots:
            raise HTTPException(status_code=404, detail="active bracket not found")
        parsed_mark_price: Decimal | None = None
        if mark_price not in (None, ""):
            try:
                parsed_mark_price = _positive_decimal(mark_price)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "signal_id": signal_id,
            "symbol": lots[0].symbol,
            "direction": lots[0].direction,
            "mark_price": str(parsed_mark_price) if parsed_mark_price is not None else None,
            "mutates_state": False,
            "summaries": [_bracket_decision_support_to_dict(lot, mark_price=parsed_mark_price) for lot in lots],
        }

    @app.post("/brackets/{signal_id}/preview")
    async def bracket_preview(signal_id: str, request: Request) -> dict[str, Any]:
        payload = await request.json()
        try:
            price = _positive_decimal(payload.get("price"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        lots = [
            lot
            for lot in engine.exchange.lots
            if lot.signal_id == signal_id and lot.remaining_quantity > 0 and lot.exit_orders
        ]
        if not lots:
            raise HTTPException(status_code=404, detail="active bracket not found")
        symbol = lots[0].symbol
        would_trigger = engine.exchange.preview_bracket(signal_id, price)
        preview_exchange = engine.exchange.preview_bracket_exchange(signal_id, price)
        return {
            "signal_id": signal_id,
            "symbol": symbol,
            "price": str(price),
            "would_trigger": would_trigger,
            "impact": _bracket_preview_impact(
                lots,
                preview_exchange=preview_exchange,
                signal_id=signal_id,
                would_trigger=would_trigger,
            ),
            "active_exits": _active_exits_to_dict(lots, signal_id=signal_id, mark_price=price),
            "preview_active_exits": _active_exits_to_dict(
                preview_exchange.lots if preview_exchange is not None else [],
                signal_id=signal_id,
                mark_price=price,
            ),
            "preview_positions": preview_exchange.list_positions() if preview_exchange is not None else [],
            "positions": engine.exchange.list_positions(),
            "account": _account_state_to_dict(engine.account_state),
        }

    @app.post("/brackets/{signal_id}/preview-path")
    async def bracket_preview_path(signal_id: str, request: Request) -> dict[str, Any]:
        payload = await request.json()
        marks_payload = payload.get("prices") or payload.get("marks") or []
        if not isinstance(marks_payload, list) or not marks_payload:
            raise HTTPException(status_code=400, detail="prices or marks must be a non-empty list")
        try:
            prices = [_positive_decimal(price) for price in marks_payload]
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        lots = [
            lot
            for lot in engine.exchange.lots
            if lot.signal_id == signal_id and lot.remaining_quantity > 0 and lot.exit_orders
        ]
        if not lots:
            raise HTTPException(status_code=404, detail="active bracket not found")

        symbol = lots[0].symbol
        preview_exchange = deepcopy(engine.exchange)
        preview_exchange.lots = [
            lot for lot in preview_exchange.lots if lot.signal_id == signal_id or lot.symbol != symbol
        ]
        marks: list[dict[str, Any]] = []
        for index, price in enumerate(prices, start=1):
            triggered = preview_exchange.update_price(symbol, price)
            marks.append(
                {
                    "index": index,
                    "price": str(price),
                    "would_trigger": triggered,
                    "preview_active_exits": _active_exits_to_dict(
                        preview_exchange.lots,
                        signal_id=signal_id,
                        mark_price=price,
                    ),
                    "preview_positions": preview_exchange.list_positions(),
                }
            )

        return {
            "signal_id": signal_id,
            "symbol": symbol,
            "mutates_state": False,
            "prices": [str(price) for price in prices],
            "active_exits": _active_exits_to_dict(lots, signal_id=signal_id),
            "marks": marks,
            "final_preview_positions": preview_exchange.list_positions(),
            "positions": engine.exchange.list_positions(),
            "account": _account_state_to_dict(engine.account_state),
        }

    @app.post("/brackets/{signal_id}/preview-candle")
    async def bracket_preview_candle(signal_id: str, request: Request) -> dict[str, Any]:
        payload = await request.json()
        try:
            open_price = _optional_positive_decimal(payload.get("open") or payload.get("open_price"))
            high = _positive_decimal(payload.get("high"))
            low = _positive_decimal(payload.get("low"))
            close = _positive_decimal(payload.get("close"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if high < low:
            raise HTTPException(status_code=400, detail="high must be greater than or equal to low")
        if open_price is not None and (open_price < low or open_price > high):
            raise HTTPException(status_code=400, detail="open must be inside the high/low range")
        if close < low or close > high:
            raise HTTPException(status_code=400, detail="close must be inside the high/low range")
        intrabar_policy = str(payload.get("intrabar_policy") or payload.get("policy") or "conservative_adverse_first")
        if intrabar_policy not in {"conservative_adverse_first", "favorable_first"}:
            raise HTTPException(status_code=400, detail="unsupported intrabar_policy")

        lots = [
            lot
            for lot in engine.exchange.lots
            if lot.signal_id == signal_id and lot.remaining_quantity > 0 and lot.exit_orders
        ]
        if not lots:
            raise HTTPException(status_code=404, detail="active bracket not found")
        if any(lot.direction != lots[0].direction for lot in lots):
            raise HTTPException(status_code=409, detail="bracket contains mixed directions")

        symbol = lots[0].symbol
        direction = lots[0].direction
        marks_to_preview = _candle_preview_marks(
            direction=direction,
            high=high,
            low=low,
            close=close,
            open_price=open_price,
            intrabar_policy=intrabar_policy,
        )
        ambiguity = _candle_ambiguity(lots, high=high, low=low)
        preview = _simulate_bracket_mark_sequence(
            engine.exchange,
            signal_id=signal_id,
            symbol=symbol,
            marks_to_preview=marks_to_preview,
        )
        compare_policies = _truthy(payload.get("compare_policies") or payload.get("compare_intrabar_policies"))
        policy_comparison = None
        if compare_policies:
            policy_comparison = _candle_policy_comparison(
                engine.exchange,
                signal_id=signal_id,
                symbol=symbol,
                direction=direction,
                high=high,
                low=low,
                close=close,
                open_price=open_price,
            )

        return {
            "signal_id": signal_id,
            "symbol": symbol,
            "mutates_state": False,
            "intrabar_policy": intrabar_policy,
            "supported_intrabar_policies": ["conservative_adverse_first", "favorable_first"],
            "ambiguous_intrabar": ambiguity["ambiguous"],
            "ambiguity": ambiguity,
            "direction": direction,
            "open": str(open_price) if open_price is not None else None,
            "high": str(high),
            "low": str(low),
            "close": str(close),
            "prices": [str(price) for _, price in marks_to_preview],
            "active_exits": _active_exits_to_dict(lots, signal_id=signal_id),
            "marks": preview["marks"],
            "outcome": preview["outcome"],
            "policy_comparison": policy_comparison,
            "final_preview_positions": preview["final_preview_positions"],
            "positions": engine.exchange.list_positions(),
            "account": _account_state_to_dict(engine.account_state),
        }

    @app.post("/brackets/{signal_id}/trailing-stop/preview-path")
    async def bracket_trailing_stop_preview_path(signal_id: str, request: Request) -> dict[str, Any]:
        payload = await request.json()
        marks_payload = payload.get("prices") or payload.get("marks") or []
        if not isinstance(marks_payload, list) or not marks_payload:
            raise HTTPException(status_code=400, detail="prices or marks must be a non-empty list")
        try:
            prices = [_positive_decimal(price) for price in marks_payload]
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        lots = [
            lot
            for lot in engine.exchange.lots
            if lot.signal_id == signal_id and lot.remaining_quantity > 0 and lot.exit_orders
        ]
        if not lots:
            raise HTTPException(status_code=404, detail="active bracket not found")
        if not any(exit_order.kind == "trailing_stop" for lot in lots for exit_order in lot.exit_orders):
            raise HTTPException(status_code=404, detail="active trailing stop not found")

        symbol = lots[0].symbol
        preview_exchange = deepcopy(engine.exchange)
        preview_exchange.lots = [
            lot for lot in preview_exchange.lots if lot.signal_id == signal_id or lot.symbol != symbol
        ]
        steps: list[dict[str, Any]] = []
        for index, price in enumerate(prices, start=1):
            before = _trailing_preview_snapshot(preview_exchange.lots, signal_id=signal_id, mark_price=price)
            triggered = preview_exchange.update_price(symbol, price)
            after = _trailing_preview_snapshot(preview_exchange.lots, signal_id=signal_id, mark_price=price)
            steps.append(
                {
                    "index": index,
                    "price": str(price),
                    "would_trigger": triggered,
                    "before": before,
                    "after": after,
                    "ratcheted": _trailing_snapshot_ratcheted(before, after),
                    "activated": _trailing_snapshot_activated(before, after),
                }
            )

        return {
            "signal_id": signal_id,
            "symbol": symbol,
            "mutates_state": False,
            "prices": [str(price) for price in prices],
            "active_trailing": _trailing_preview_snapshot(lots, signal_id=signal_id, mark_price=None),
            "steps": steps,
            "final_preview_trailing": _trailing_preview_snapshot(
                preview_exchange.lots,
                signal_id=signal_id,
                mark_price=prices[-1],
            ),
            "positions": engine.exchange.list_positions(),
            "preview_positions": preview_exchange.list_positions(),
            "account": _account_state_to_dict(engine.account_state),
        }

    @app.post("/brackets/{signal_id}/stop")
    async def amend_bracket_stop(signal_id: str, request: Request) -> dict[str, Any]:
        payload = await request.json()
        try:
            trigger_price = _positive_decimal(payload.get("trigger_price") or payload.get("stop_loss_price"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        reason = str(payload.get("reason") or "manual protective stop amend")
        order = engine.exchange.amend_bracket_stop(signal_id, trigger_price, reason=reason)
        if order is None:
            raise HTTPException(status_code=409, detail="active bracket not found or stop would loosen risk")
        engine.account_state.open_notional = engine.exchange.open_notional()
        if repository:
            repository.save_order(order)
            repository.record_audit(
                "bracket.stop_amended",
                {
                    "signal_id": signal_id,
                    "reason": reason,
                    "trigger_price": str(trigger_price),
                    "exit_orders": [
                        {
                            "kind": exit_order.kind,
                            "trigger_price": str(exit_order.trigger_price),
                            "close_pct": str(exit_order.close_pct),
                            "oca_group": exit_order.oca_group,
                            "status": exit_order.status,
                        }
                        for exit_order in order.exit_orders
                    ],
                },
            )
        return {
            "status": "amended",
            "signal_id": signal_id,
            "order": order.to_dict(),
            "active_exits": _active_exits_to_dict(engine.exchange.lots, signal_id=signal_id),
            "positions": engine.exchange.list_positions(),
            "account": _account_state_to_dict(engine.account_state),
        }

    @app.post("/brackets/{signal_id}/trailing-stop")
    async def amend_bracket_trailing_stop(signal_id: str, request: Request) -> dict[str, Any]:
        payload = await request.json()
        try:
            trigger_price = _positive_decimal(payload.get("trigger_price") or payload.get("trailing_stop_price"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        reason = str(payload.get("reason") or "manual trailing stop amend")
        order = engine.exchange.amend_bracket_trailing_stop(signal_id, trigger_price, reason=reason)
        if order is None:
            raise HTTPException(status_code=409, detail="active trailing stop not found or amendment would loosen risk")
        engine.account_state.open_notional = engine.exchange.open_notional()
        if repository:
            repository.save_order(order)
            repository.record_audit(
                "bracket.trailing_stop_amended",
                {
                    "signal_id": signal_id,
                    "reason": reason,
                    "trigger_price": str(trigger_price),
                    "exit_orders": [
                        {
                            "kind": exit_order.kind,
                            "trigger_price": str(exit_order.trigger_price),
                            "close_pct": str(exit_order.close_pct),
                            "oca_group": exit_order.oca_group,
                            "status": exit_order.status,
                        }
                        for exit_order in order.exit_orders
                    ],
                },
            )
        return {
            "status": "amended",
            "signal_id": signal_id,
            "order": order.to_dict(),
            "active_exits": _active_exits_to_dict(engine.exchange.lots, signal_id=signal_id),
            "positions": engine.exchange.list_positions(),
            "account": _account_state_to_dict(engine.account_state),
        }

    @app.post("/brackets/{signal_id}/trailing-stop/mark")
    async def tighten_bracket_trailing_stop_to_mark(signal_id: str, request: Request) -> dict[str, Any]:
        payload = await request.json()
        try:
            mark_price = _positive_decimal(payload.get("mark_price") or payload.get("price"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        reason = str(payload.get("reason") or "manual trailing stop tighten from mark")
        order = engine.exchange.tighten_bracket_trailing_stop_to_mark(signal_id, mark_price, reason=reason)
        if order is None:
            raise HTTPException(
                status_code=409,
                detail="active trailing stop not found, mark is not favorable, or amendment would loosen risk",
            )
        engine.account_state.open_notional = engine.exchange.open_notional()
        if repository:
            repository.save_order(order)
            repository.record_audit(
                "bracket.trailing_stop_mark_amended",
                {
                    "signal_id": signal_id,
                    "reason": reason,
                    "mark_price": str(mark_price),
                    "exit_orders": [
                        {
                            "kind": exit_order.kind,
                            "trigger_price": str(exit_order.trigger_price),
                            "close_pct": str(exit_order.close_pct),
                            "oca_group": exit_order.oca_group,
                            "status": exit_order.status,
                        }
                        for exit_order in order.exit_orders
                    ],
                },
            )
        return {
            "status": "amended",
            "signal_id": signal_id,
            "mark_price": str(mark_price),
            "order": order.to_dict(),
            "active_exits": _active_exits_to_dict(engine.exchange.lots, signal_id=signal_id, mark_price=mark_price),
            "positions": engine.exchange.list_positions(),
            "account": _account_state_to_dict(engine.account_state),
        }

    @app.post("/brackets/{signal_id}/take-profit")
    async def amend_bracket_take_profit(signal_id: str, request: Request) -> dict[str, Any]:
        payload = await request.json()
        try:
            trigger_price = _positive_decimal(payload.get("trigger_price") or payload.get("take_profit_price"))
            target_index = _non_negative_int(payload.get("target_index"), default=0)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        reason = str(payload.get("reason") or "manual take-profit amend")
        order = engine.exchange.amend_bracket_take_profit(
            signal_id,
            trigger_price,
            target_index=target_index,
            reason=reason,
        )
        if order is None:
            raise HTTPException(
                status_code=409,
                detail="active take-profit target not found or amendment would reduce projected reward",
            )
        engine.account_state.open_notional = engine.exchange.open_notional()
        if repository:
            repository.save_order(order)
            repository.record_audit(
                "bracket.take_profit_amended",
                {
                    "signal_id": signal_id,
                    "reason": reason,
                    "trigger_price": str(trigger_price),
                    "target_index": target_index,
                    "exit_orders": [
                        {
                            "kind": exit_order.kind,
                            "trigger_price": str(exit_order.trigger_price),
                            "close_pct": str(exit_order.close_pct),
                            "oca_group": exit_order.oca_group,
                            "status": exit_order.status,
                        }
                        for exit_order in order.exit_orders
                    ],
                },
            )
        return {
            "status": "amended",
            "signal_id": signal_id,
            "order": order.to_dict(),
            "active_exits": _active_exits_to_dict(engine.exchange.lots, signal_id=signal_id),
            "positions": engine.exchange.list_positions(),
            "account": _account_state_to_dict(engine.account_state),
        }

    @app.post("/brackets/{signal_id}/breakeven")
    async def move_bracket_to_breakeven(signal_id: str, request: Request) -> dict[str, Any]:
        payload = await request.json()
        reason = str(payload.get("reason") or "manual move protective exits to breakeven")
        order = engine.exchange.move_bracket_to_breakeven(signal_id, reason=reason)
        if order is None:
            raise HTTPException(
                status_code=409,
                detail="active bracket not found, no protective exit found, or breakeven would loosen risk",
            )
        engine.account_state.open_notional = engine.exchange.open_notional()
        if repository:
            repository.save_order(order)
            repository.record_audit(
                "bracket.breakeven_amended",
                {
                    "signal_id": signal_id,
                    "reason": reason,
                    "entry_price": str(order.price),
                    "exit_orders": [
                        {
                            "kind": exit_order.kind,
                            "trigger_price": str(exit_order.trigger_price),
                            "close_pct": str(exit_order.close_pct),
                            "oca_group": exit_order.oca_group,
                            "status": exit_order.status,
                        }
                        for exit_order in order.exit_orders
                    ],
                },
            )
        return {
            "status": "amended",
            "signal_id": signal_id,
            "order": order.to_dict(),
            "active_exits": _active_exits_to_dict(engine.exchange.lots, signal_id=signal_id),
            "positions": engine.exchange.list_positions(),
            "account": _account_state_to_dict(engine.account_state),
        }

    @app.post("/brackets/{signal_id}/lock-profit")
    async def lock_bracket_profit(signal_id: str, request: Request) -> dict[str, Any]:
        payload = await request.json()
        try:
            lock_profit_pct = _positive_decimal(payload.get("lock_profit_pct") or payload.get("profit_lock_pct"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        reason = str(payload.get("reason") or "manual lock protective exits into profit")
        order = engine.exchange.lock_bracket_profit(signal_id, lock_profit_pct, reason=reason)
        if order is None:
            raise HTTPException(
                status_code=409,
                detail="active bracket not found, no protective exit found, or profit lock would loosen risk",
            )
        engine.account_state.open_notional = engine.exchange.open_notional()
        if repository:
            repository.save_order(order)
            repository.record_audit(
                "bracket.profit_locked",
                {
                    "signal_id": signal_id,
                    "reason": reason,
                    "lock_profit_pct": str(lock_profit_pct),
                    "lock_price": str(order.price),
                    "exit_orders": [
                        {
                            "kind": exit_order.kind,
                            "trigger_price": str(exit_order.trigger_price),
                            "close_pct": str(exit_order.close_pct),
                            "oca_group": exit_order.oca_group,
                            "status": exit_order.status,
                        }
                        for exit_order in order.exit_orders
                    ],
                },
            )
        return {
            "status": "amended",
            "signal_id": signal_id,
            "lock_profit_pct": str(lock_profit_pct),
            "order": order.to_dict(),
            "active_exits": _active_exits_to_dict(engine.exchange.lots, signal_id=signal_id),
            "positions": engine.exchange.list_positions(),
            "account": _account_state_to_dict(engine.account_state),
        }

    @app.post("/brackets/{signal_id}/cancel")
    async def cancel_bracket(signal_id: str, request: Request) -> dict[str, Any]:
        payload = await request.json()
        reason = str(payload.get("reason") or "manual bracket cancel")
        order = engine.exchange.cancel_bracket(signal_id, reason=reason)
        if order is None:
            raise HTTPException(status_code=404, detail="active bracket not found")
        engine.account_state.open_notional = engine.exchange.open_notional()
        if repository:
            repository.save_order(order)
            repository.record_audit(
                "bracket.canceled",
                {
                    "signal_id": signal_id,
                    "reason": reason,
                    "canceled_exit_orders": [
                        {
                            "kind": exit_order.kind,
                            "trigger_price": str(exit_order.trigger_price),
                            "close_pct": str(exit_order.close_pct),
                            "oca_group": exit_order.oca_group,
                            "status": exit_order.status,
                        }
                        for exit_order in order.canceled_exit_orders
                    ],
                },
            )
        return {
            "status": "canceled",
            "signal_id": signal_id,
            "order": order.to_dict(),
            "active_exits": _active_exits_to_dict(engine.exchange.lots),
            "positions": engine.exchange.list_positions(),
            "account": _account_state_to_dict(engine.account_state),
        }

    @app.post("/brackets/{signal_id}/close")
    async def close_bracket(signal_id: str, request: Request) -> dict[str, Any]:
        payload = await request.json()
        try:
            price = _positive_decimal(payload.get("price") or payload.get("mark_price"))
            close_pct = _optional_positive_decimal(payload.get("close_pct"))
            base_amount = _optional_positive_decimal(payload.get("base_amount") or payload.get("quantity") or payload.get("qty"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if close_pct is not None and close_pct > 100:
            raise HTTPException(status_code=400, detail="close_pct cannot exceed 100")
        if close_pct is not None and base_amount is not None:
            raise HTTPException(status_code=400, detail="send close_pct or base_amount, not both")
        reason = str(payload.get("reason") or "manual paper bracket close")
        lots = [
            lot
            for lot in engine.exchange.lots
            if lot.signal_id == signal_id and lot.remaining_quantity > 0 and lot.exit_orders
        ]
        realized_before = _position_realized_pnl(engine.exchange, lots[0].symbol) if lots else Decimal("0")
        order = engine.exchange.close_bracket(
            signal_id,
            price,
            close_pct=close_pct,
            base_amount=base_amount,
            reason=reason,
        )
        if order is None:
            raise HTTPException(status_code=404, detail="active bracket not found")
        realized_pnl_delta = _position_realized_pnl(engine.exchange, order.symbol) - realized_before
        if realized_pnl_delta:
            engine.account_state.daily_pnl += realized_pnl_delta
            if realized_pnl_delta < 0:
                engine.account_state.consecutive_losses += 1
            elif realized_pnl_delta > 0:
                engine.account_state.consecutive_losses = 0
        engine.account_state.open_notional = engine.exchange.open_notional()
        if repository:
            repository.save_order(order)
            repository.record_audit(
                "bracket.closed",
                {
                    "signal_id": signal_id,
                    "reason": reason,
                    "price": str(price),
                    "close_pct": str(close_pct) if close_pct is not None else None,
                    "base_amount": str(base_amount) if base_amount is not None else None,
                    "realized_pnl_delta": str(realized_pnl_delta),
                    "canceled_exit_orders": [
                        {
                            "kind": exit_order.kind,
                            "trigger_price": str(exit_order.trigger_price),
                            "close_pct": str(exit_order.close_pct),
                            "oca_group": exit_order.oca_group,
                            "status": exit_order.status,
                        }
                        for exit_order in order.canceled_exit_orders
                    ],
                },
            )
        return {
            "status": "closed",
            "signal_id": signal_id,
            "order": order.to_dict(),
            "active_exits": _active_exits_to_dict(engine.exchange.lots),
            "realized_pnl_delta": str(realized_pnl_delta),
            "positions": engine.exchange.list_positions(),
            "account": _account_state_to_dict(engine.account_state),
        }

    @app.post("/brackets/{signal_id}/close-protective")
    async def close_bracket_at_protective_exit(signal_id: str, request: Request) -> dict[str, Any]:
        payload = await request.json()
        try:
            close_pct = _optional_positive_decimal(payload.get("close_pct"))
            base_amount = _optional_positive_decimal(payload.get("base_amount") or payload.get("quantity") or payload.get("qty"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if close_pct is not None and close_pct > 100:
            raise HTTPException(status_code=400, detail="close_pct cannot exceed 100")
        if close_pct is not None and base_amount is not None:
            raise HTTPException(status_code=400, detail="send close_pct or base_amount, not both")
        reason = str(payload.get("reason") or "manual paper bracket protective close")
        lots = [
            lot
            for lot in engine.exchange.lots
            if lot.signal_id == signal_id and lot.remaining_quantity > 0 and lot.exit_orders
        ]
        realized_before = _position_realized_pnl(engine.exchange, lots[0].symbol) if lots else Decimal("0")
        order = engine.exchange.close_bracket_at_protective_exit(
            signal_id,
            close_pct=close_pct,
            base_amount=base_amount,
            reason=reason,
        )
        if order is None:
            raise HTTPException(status_code=404, detail="active bracket with protective exit not found")
        realized_pnl_delta = _position_realized_pnl(engine.exchange, order.symbol) - realized_before
        if realized_pnl_delta:
            engine.account_state.daily_pnl += realized_pnl_delta
            if realized_pnl_delta < 0:
                engine.account_state.consecutive_losses += 1
            elif realized_pnl_delta > 0:
                engine.account_state.consecutive_losses = 0
        engine.account_state.open_notional = engine.exchange.open_notional()
        if repository:
            repository.save_order(order)
            repository.record_audit(
                "bracket.protective_closed",
                {
                    "signal_id": signal_id,
                    "reason": reason,
                    "price": str(order.price),
                    "close_pct": str(close_pct) if close_pct is not None else None,
                    "base_amount": str(base_amount) if base_amount is not None else None,
                    "realized_pnl_delta": str(realized_pnl_delta),
                    "canceled_exit_orders": [
                        {
                            "kind": exit_order.kind,
                            "trigger_price": str(exit_order.trigger_price),
                            "close_pct": str(exit_order.close_pct),
                            "oca_group": exit_order.oca_group,
                            "status": exit_order.status,
                        }
                        for exit_order in order.canceled_exit_orders
                    ],
                },
            )
        return {
            "status": "closed",
            "signal_id": signal_id,
            "order": order.to_dict(),
            "active_exits": _active_exits_to_dict(engine.exchange.lots),
            "realized_pnl_delta": str(realized_pnl_delta),
            "positions": engine.exchange.list_positions(),
            "account": _account_state_to_dict(engine.account_state),
        }

    @app.get("/approvals")
    def list_approvals() -> dict[str, Any]:
        return {"pending": intake.list_approvals()}

    @app.post("/approvals/{signal_id}/approve")
    def approve_signal(signal_id: str) -> dict[str, Any]:
        result = intake.approve(signal_id)
        if result is None:
            raise HTTPException(status_code=404, detail="pending signal not found")
        return result

    @app.post("/approvals/{signal_id}/reject")
    async def reject_signal(signal_id: str, request: Request) -> dict[str, Any]:
        payload = await request.json()
        reason = str(payload.get("reason") or "")
        result = intake.reject(signal_id, reason)
        if result is None:
            raise HTTPException(status_code=404, detail="pending signal not found")
        return result

    @app.get("/signals")
    def signals() -> dict[str, Any]:
        return {"signals": repository.list_signals() if repository else []}

    @app.get("/bracket-templates")
    def bracket_templates() -> dict[str, Any]:
        return {
            "templates": list_bracket_templates(),
            "paper_only": True,
            "live_submission_enabled": False,
        }

    @app.get("/bracket-templates/{template_name}")
    def bracket_template(template_name: str) -> dict[str, Any]:
        try:
            template = get_bracket_template(template_name)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            "template": template.to_dict(),
            "paper_only": True,
            "live_submission_enabled": False,
        }

    @app.get("/strategy-presets")
    def strategy_presets() -> dict[str, Any]:
        return {
            "presets": list_strategy_presets(),
            "paper_only": True,
            "live_submission_enabled": False,
            "submit_endpoint": None,
        }

    @app.get("/strategy-presets/{preset_name}")
    def strategy_preset(preset_name: str) -> dict[str, Any]:
        try:
            preset = get_strategy_preset(preset_name)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            "preset": preset.to_dict(),
            "paper_only": True,
            "live_submission_enabled": False,
            "submit_endpoint": None,
        }

    @app.post("/signals/parse-text")
    async def parse_text(request: Request) -> dict[str, Any]:
        payload = await request.json()
        try:
            signal = parse_text_signal(str(payload.get("message") or ""), source="api")
        except SignalValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"signal": _signal_to_dict(signal)}

    @app.post("/signals/preview-text")
    async def preview_text(request: Request) -> dict[str, Any]:
        payload = await request.json()
        try:
            signal = parse_text_signal(str(payload.get("message") or ""), source="operator-preview")
        except SignalValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _signal_preview(signal, engine, require_approval=require_approval)

    @app.post("/signals/submit-text")
    async def submit_text(request: Request) -> dict[str, Any]:
        payload = await request.json()
        try:
            signal = parse_text_signal(str(payload.get("message") or ""), source="operator-ui")
        except SignalValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return intake.handle(signal)

    @app.post("/signals/submit")
    async def submit_signal(request: Request) -> dict[str, Any]:
        payload = await request.json()
        try:
            signal = normalize_signal(payload, source="operator-ui")
        except SignalValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return intake.handle(signal)

    @app.post("/signals/preview")
    async def preview_signal(request: Request) -> dict[str, Any]:
        payload = await request.json()
        try:
            signal = normalize_signal(payload, source="operator-preview")
        except SignalValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _signal_preview(signal, engine, require_approval=require_approval)

    @app.post("/signals/preview-template")
    async def preview_template_signal(request: Request) -> dict[str, Any]:
        payload = await request.json()
        try:
            templated_payload = _templated_signal_payload(payload)
            signal = normalize_signal(templated_payload, source="operator-template-preview")
        except SignalValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        preview = _signal_preview(signal, engine, require_approval=require_approval)
        preview["template"] = get_bracket_template(str(templated_payload["bracket_template"])).to_dict()
        preview["merged_signal_payload"] = templated_payload
        preview["paper_only"] = True
        return preview

    @app.post("/signals/preview-strategy")
    async def preview_strategy_signal(request: Request) -> dict[str, Any]:
        payload = await request.json()
        try:
            preset_payload = _strategy_signal_payload(payload)
            signal = normalize_signal(preset_payload, source="operator-strategy-preview")
        except SignalValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        preview = _signal_preview(signal, engine, require_approval=require_approval)
        preview["strategy_preset"] = get_strategy_preset(str(preset_payload["strategy_preset"])).to_dict()
        bracket_template_name = preset_payload.get("bracket_template")
        if bracket_template_name:
            preview["template"] = get_bracket_template(str(bracket_template_name)).to_dict()
        preview["merged_signal_payload"] = preset_payload
        preview["paper_only"] = True
        preview["live_submission_enabled"] = False
        return preview

    @app.post("/signals/submit-template")
    async def submit_template_signal(request: Request) -> dict[str, Any]:
        payload = await request.json()
        try:
            templated_payload = _templated_signal_payload(payload)
            signal = normalize_signal(templated_payload, source="operator-template")
        except SignalValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        result = intake.handle(signal)
        result["template"] = get_bracket_template(str(templated_payload["bracket_template"])).to_dict()
        result["paper_only"] = True
        return result

    @app.post("/signals/exchange-plan")
    async def signal_exchange_plan(request: Request) -> dict[str, Any]:
        payload = await request.json()
        try:
            signal = normalize_signal(payload, source="operator-plan")
            capabilities = _capabilities_for_signal_exchange(signal.exchange)
        except SignalValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except CcxtNotInstalledError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except BitunixConfigurationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        plan = plan_bracket_execution(signal, capabilities)
        return {"signal": _signal_to_dict(signal), "capabilities": capabilities.to_dict(), "plan": plan.to_dict()}

    @app.post("/backtest/signal")
    async def backtest_signal(request: Request) -> dict[str, Any]:
        payload = await request.json()
        signal_payload = payload.get("signal") if isinstance(payload.get("signal"), dict) else payload
        marks_payload = payload.get("prices") or payload.get("marks") or []
        candles_payload = payload.get("candles") or []
        if candles_payload and marks_payload:
            raise HTTPException(status_code=400, detail="send either prices or candles, not both")
        if candles_payload and not isinstance(candles_payload, list):
            raise HTTPException(status_code=400, detail="candles must be a non-empty list")
        if not candles_payload and (not isinstance(marks_payload, list) or not marks_payload):
            raise HTTPException(status_code=400, detail="prices or candles must be a non-empty list")
        try:
            signal = normalize_signal(signal_payload, source="operator-backtest")
            costs = _execution_cost_payload(payload)
            close_final_positions = _truthy(payload.get("close_final_positions") or payload.get("force_close_final"))
            if candles_payload:
                candles = [_candle_payload(candle) for candle in candles_payload]
                return run_signal_candle_backtest(
                    engine,
                    signal,
                    candles,
                    costs=costs,
                    close_final_positions=close_final_positions,
                ).to_dict()
            prices = [_positive_decimal(price) for price in marks_payload]
        except (SignalValidationError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return run_signal_backtest(
            engine,
            signal,
            prices,
            costs=costs,
            close_final_positions=close_final_positions,
        ).to_dict()

    @app.post("/backtest/bitunix-klines")
    async def backtest_bitunix_klines(request: Request) -> dict[str, Any]:
        payload = await request.json()
        signal_payload = payload.get("signal") if isinstance(payload.get("signal"), dict) else payload
        try:
            signal = normalize_signal(signal_payload, source="operator-bitunix-kline-backtest")
            costs = _execution_cost_payload(payload)
            query = _bitunix_kline_query(payload)
            raw = BitunixRestClient(credentials=load_bitunix_credentials_from_env()).get_futures_klines(**query)
            candles = [_candle_payload(candle) for candle in bitunix_kline_candles(raw)]
            if not candles:
                raise ValueError("Bitunix returned no candles")
        except (SignalValidationError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except BitunixRequestError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        summary = run_signal_candle_backtest(
            engine,
            signal,
            candles,
            costs=costs,
            close_final_positions=_truthy(payload.get("close_final_positions") or payload.get("force_close_final")),
        ).to_dict()
        summary["market_data"] = {
            "source": "bitunix",
            "symbol": query["symbol"],
            "interval": query["interval"],
            "price_type": query.get("price_type"),
            "candle_count": len(candles),
        }
        return summary

    @app.post("/backtest/batch")
    async def backtest_batch(request: Request) -> dict[str, Any]:
        payload = await request.json()
        candidates_payload = payload.get("candidates")
        base_signal = payload.get("signal") if isinstance(payload.get("signal"), dict) else {}
        if not isinstance(candidates_payload, list) or not candidates_payload:
            raise HTTPException(status_code=400, detail="candidates must be a non-empty list")
        if len(candidates_payload) > 50:
            raise HTTPException(status_code=400, detail="candidates cannot exceed 50")

        results: list[dict[str, Any]] = []
        for index, candidate_payload in enumerate(candidates_payload, start=1):
            if not isinstance(candidate_payload, dict):
                raise HTTPException(status_code=400, detail="candidate entries must be objects")
            try:
                candidate = _batch_backtest_candidate_payload(
                    candidate_payload,
                    base_signal=base_signal,
                    default_name=f"candidate-{index}",
                    default_payload=payload,
                )
                signal = normalize_signal(candidate["signal"], source="operator-batch-backtest")
                summary = _run_backtest_candidate(
                    engine,
                    signal,
                    candidate,
                ).to_dict()
            except (SignalValidationError, ValueError) as exc:
                if not _truthy(payload.get("continue_on_error")):
                    raise HTTPException(status_code=400, detail=f"{candidate_payload.get('name') or index}: {exc}") from exc
                results.append(
                    {
                        "name": str(candidate_payload.get("name") or candidate_payload.get("label") or f"candidate-{index}"),
                        "accepted": False,
                        "status": "invalid",
                        "error": str(exc),
                    }
                )
                continue
            result = {
                "name": candidate["name"],
                "symbol": signal.symbol,
                **summary,
            }
            results.append(result)

        accepted = [result for result in results if result.get("accepted")]
        ranked = sorted(
            accepted,
            key=lambda result: (
                _decimal(result.get("final_total_pnl"), default=Decimal("0")),
                -_decimal(result.get("risk_summary", {}).get("max_drawdown"), default=Decimal("0")),
            ),
            reverse=True,
        )
        return {
            "candidate_count": len(results),
            "accepted_count": len(accepted),
            "rejected_count": len(results) - len(accepted),
            "best_by_total_pnl": _batch_result_rank_row(ranked[0]) if ranked else None,
            "worst_by_total_pnl": _batch_result_rank_row(ranked[-1]) if ranked else None,
            "worst_max_drawdown": str(
                max(
                    (_decimal(result.get("risk_summary", {}).get("max_drawdown"), default=Decimal("0")) for result in accepted),
                    default=Decimal("0"),
                )
            ),
            "ranked": [_batch_result_rank_row(result) for result in ranked],
            "results": results,
        }

    @app.post("/backtest/stress")
    async def backtest_stress(request: Request) -> dict[str, Any]:
        payload = await request.json()
        signal_payload = payload.get("signal") if isinstance(payload.get("signal"), dict) else payload
        scenarios_payload = payload.get("scenarios")
        if not isinstance(scenarios_payload, list) or not scenarios_payload:
            raise HTTPException(status_code=400, detail="scenarios must be a non-empty list")
        try:
            signal = normalize_signal(signal_payload, source="operator-stress-backtest")
            scenarios = [_stress_scenario_payload(scenario) for scenario in scenarios_payload]
        except (SignalValidationError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return run_signal_stress_backtest(engine, signal, scenarios).to_dict()

    @app.get("/audit")
    def audit() -> dict[str, Any]:
        return {"events": [event.to_dict() for event in repository.list_audit()] if repository else []}

    return app


def create_app_from_env() -> FastAPI:
    settings = load_settings()
    repository = SQLiteRepository(settings.db_path) if settings.db_path else None
    return create_app(
        risk_config=settings.risk,
        webhook_secret=settings.webhook_secret,
        webhook_tolerance_seconds=settings.webhook_tolerance_seconds,
        repository=repository,
        require_approval=settings.require_approval,
    )


app = create_app()


def _positive_decimal(value: Any) -> Decimal:
    if value is None or value == "":
        raise ValueError("price is required")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid price: {value}") from exc
    if parsed <= 0:
        raise ValueError("price must be positive")
    return parsed


def _optional_positive_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid decimal: {value}") from exc
    if parsed <= 0:
        raise ValueError("decimal value must be positive")
    return parsed


def _truthy(value: Any) -> bool:
    if value is None or value == "":
        return False
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def _non_negative_int(value: Any, *, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid integer: {value}") from exc
    if parsed < 0:
        raise ValueError("integer value must be non-negative")
    return parsed


def _candle_payload(value: Any) -> dict[str, Decimal]:
    if not isinstance(value, dict):
        raise ValueError("candles entries must be objects")
    high = _positive_decimal(value.get("high"))
    low = _positive_decimal(value.get("low"))
    if low > high:
        raise ValueError("candle low cannot exceed high")
    return {
        "label": value.get("label") or value.get("time") or value.get("timestamp"),
        "high": high,
        "low": low,
        "close": _positive_decimal(value.get("close")),
    }


def _stress_scenario_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("scenario entries must be objects")
    marks_payload = value.get("prices") or value.get("marks") or []
    candles_payload = value.get("candles") or []
    if candles_payload and marks_payload:
        raise ValueError("scenario must send either prices or candles, not both")
    if candles_payload:
        if not isinstance(candles_payload, list):
            raise ValueError("scenario candles must be a list")
        path = {"candles": [_candle_payload(candle) for candle in candles_payload]}
    else:
        if not isinstance(marks_payload, list) or not marks_payload:
            raise ValueError("scenario prices or candles must be a non-empty list")
        path = {"prices": [_positive_decimal(price) for price in marks_payload]}
    costs_payload = value.get("costs") if isinstance(value.get("costs"), dict) else value
    return {
        "name": str(value.get("name") or value.get("label") or "scenario"),
        **path,
        "close_final_positions": _truthy(value.get("close_final_positions") or value.get("force_close_final")),
        "costs": ExecutionCostConfig(
            fee_bps=_non_negative_decimal(costs_payload.get("fee_bps"), default=Decimal("0")),
            slippage_bps=_non_negative_decimal(costs_payload.get("slippage_bps"), default=Decimal("0")),
            funding_rate_bps=_decimal(costs_payload.get("funding_rate_bps"), default=Decimal("0")),
            funding_periods_per_mark=_non_negative_decimal(
                costs_payload.get("funding_periods_per_mark"),
                default=Decimal("0"),
            ),
        ),
    }


def _batch_backtest_candidate_payload(
    value: dict[str, Any],
    *,
    base_signal: dict[str, Any],
    default_name: str,
    default_payload: dict[str, Any],
) -> dict[str, Any]:
    candidate_signal = value.get("signal") if isinstance(value.get("signal"), dict) else {}
    inline_signal = {
        key: item
        for key, item in value.items()
        if key
        not in {
            "name",
            "label",
            "signal",
            "prices",
            "marks",
            "candles",
            "costs",
            "close_final_positions",
            "force_close_final",
        }
    }
    signal_payload = {**base_signal, **inline_signal, **candidate_signal}
    if not signal_payload.get("symbol") and value.get("symbol"):
        signal_payload["symbol"] = value["symbol"]

    marks_payload = value.get("prices") or value.get("marks") or []
    candles_payload = value.get("candles") or []
    if candles_payload and marks_payload:
        raise ValueError("candidate must send either prices or candles, not both")
    if candles_payload:
        if not isinstance(candles_payload, list):
            raise ValueError("candidate candles must be a list")
        path = {"candles": [_candle_payload(candle) for candle in candles_payload]}
    else:
        if not isinstance(marks_payload, list) or not marks_payload:
            raise ValueError("candidate prices or candles must be a non-empty list")
        path = {"prices": [_positive_decimal(price) for price in marks_payload]}

    cost_payload = {**default_payload, **value}
    return {
        "name": str(value.get("name") or value.get("label") or signal_payload.get("symbol") or default_name),
        "signal": signal_payload,
        **path,
        "close_final_positions": _truthy(
            value.get("close_final_positions")
            if "close_final_positions" in value
            else value.get("force_close_final")
            if "force_close_final" in value
            else default_payload.get("close_final_positions") or default_payload.get("force_close_final")
        ),
        "costs": _execution_cost_payload(cost_payload),
    }


def _run_backtest_candidate(engine: TradingEngine, signal: CryptoSignal, candidate: dict[str, Any]) -> Any:
    if "candles" in candidate:
        return run_signal_candle_backtest(
            engine,
            signal,
            candidate["candles"],
            costs=candidate["costs"],
            close_final_positions=candidate["close_final_positions"],
        )
    return run_signal_backtest(
        engine,
        signal,
        candidate["prices"],
        costs=candidate["costs"],
        close_final_positions=candidate["close_final_positions"],
    )


def _batch_result_rank_row(result: dict[str, Any]) -> dict[str, Any]:
    metrics = result.get("report_metrics", {})
    risk = result.get("risk_summary", {})
    return {
        "name": result.get("name"),
        "symbol": result.get("symbol"),
        "status": result.get("status"),
        "final_total_pnl": result.get("final_total_pnl"),
        "final_daily_pnl": result.get("final_daily_pnl"),
        "total_return_pct": metrics.get("total_return_pct"),
        "win_rate_pct": metrics.get("win_rate_pct"),
        "profit_factor": metrics.get("profit_factor"),
        "max_drawdown": risk.get("max_drawdown"),
        "total_triggers": result.get("total_triggers"),
    }


def _execution_cost_payload(payload: dict[str, Any]) -> ExecutionCostConfig:
    costs = payload.get("costs") if isinstance(payload.get("costs"), dict) else {}
    fee_bps = costs.get("fee_bps") if costs else payload.get("fee_bps")
    slippage_bps = costs.get("slippage_bps") if costs else payload.get("slippage_bps")
    funding_rate_bps = costs.get("funding_rate_bps") if costs else payload.get("funding_rate_bps")
    funding_periods_per_mark = (
        costs.get("funding_periods_per_mark") if costs else payload.get("funding_periods_per_mark")
    )
    return ExecutionCostConfig(
        fee_bps=_non_negative_decimal(fee_bps, default=Decimal("0")),
        slippage_bps=_non_negative_decimal(slippage_bps, default=Decimal("0")),
        funding_rate_bps=_decimal(funding_rate_bps, default=Decimal("0")),
        funding_periods_per_mark=_non_negative_decimal(funding_periods_per_mark, default=Decimal("0")),
    )


def _bitunix_kline_query(payload: dict[str, Any]) -> dict[str, Any]:
    market_data = payload.get("market_data") if isinstance(payload.get("market_data"), dict) else {}
    symbol = str(payload.get("symbol") or market_data.get("symbol") or "").strip().upper()
    if not symbol:
        signal = payload.get("signal") if isinstance(payload.get("signal"), dict) else {}
        symbol = str(signal.get("symbol") or "").strip().upper().replace("/", "")
    interval = str(payload.get("interval") or market_data.get("interval") or "").strip()
    if not symbol:
        raise ValueError("Bitunix kline symbol is required")
    if not interval:
        raise ValueError("Bitunix kline interval is required")
    limit = payload.get("limit") if payload.get("limit") is not None else market_data.get("limit")
    query: dict[str, Any] = {
        "symbol": symbol,
        "interval": interval,
        "start_time": _optional_int(payload.get("start_time") or market_data.get("start_time")),
        "end_time": _optional_int(payload.get("end_time") or market_data.get("end_time")),
        "limit": _optional_int(limit),
        "price_type": payload.get("price_type") or market_data.get("price_type") or payload.get("type"),
    }
    if query["limit"] is not None and (query["limit"] <= 0 or query["limit"] > 200):
        raise ValueError("Bitunix kline limit must be between 1 and 200")
    return query


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid integer: {value}") from exc


def _non_negative_decimal(value: Any, *, default: Decimal) -> Decimal:
    if value is None or value == "":
        return default
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid decimal: {value}") from exc
    if parsed < 0:
        raise ValueError("value must be non-negative")
    return parsed


def _decimal(value: Any, *, default: Decimal) -> Decimal:
    if value is None or value == "":
        return default
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid decimal: {value}") from exc


def _paper_capabilities() -> ExchangeCapabilities:
    return ExchangeCapabilities(
        exchange_id="paper",
        spot=True,
        margin=False,
        swap=False,
        future=False,
        option=False,
        create_order=True,
        cancel_order=False,
        fetch_balance=False,
        attached_stop_loss_take_profit=True,
        oco_order=True,
        trailing_order=True,
        reduce_only=True,
    )


def _templated_signal_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    template_name = str(payload.get("template") or payload.get("template_name") or "").strip()
    if not template_name:
        raise ValueError("template is required")
    signal_payload = payload.get("signal")
    if signal_payload is None:
        signal_payload = {
            key: value
            for key, value in payload.items()
            if key not in {"template", "template_name", "overrides", "template_overrides"}
        }
    overrides = payload.get("overrides") or payload.get("template_overrides")
    return apply_bracket_template(signal_payload, template_name, overrides=overrides)


def _strategy_signal_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    preset_name = str(payload.get("strategy") or payload.get("strategy_preset") or payload.get("preset") or "").strip()
    if not preset_name:
        raise ValueError("strategy preset is required")
    signal_payload = payload.get("signal")
    if signal_payload is None:
        signal_payload = {
            key: value
            for key, value in payload.items()
            if key
            not in {
                "strategy",
                "strategy_preset",
                "preset",
                "overrides",
                "strategy_overrides",
                "template",
                "template_name",
                "bracket_template",
                "template_overrides",
            }
        }
    overrides = payload.get("overrides") or payload.get("strategy_overrides")
    preset_payload = apply_strategy_preset(signal_payload, preset_name, overrides=overrides)
    template_name = (
        payload.get("template")
        or payload.get("template_name")
        or payload.get("bracket_template")
        or get_strategy_preset(preset_name).suggested_bracket_template
    )
    return apply_bracket_template(
        preset_payload,
        str(template_name),
        overrides=payload.get("template_overrides"),
    )


def _capabilities_for_signal_exchange(exchange_id: str) -> ExchangeCapabilities:
    normalized = exchange_id.strip().lower()
    if normalized == "paper":
        return _paper_capabilities()
    if normalized == "bitunix":
        return BitunixRestClient(credentials=load_bitunix_credentials_from_env()).capabilities()
    platform = get_platform(normalized)
    if platform and platform.ccxt_id:
        normalized = platform.ccxt_id
    return CcxtExchangeAdapter(normalized).capabilities()


def _exchange_row(exchange_id: str, driver: str) -> dict[str, Any]:
    return {
        "exchange_id": exchange_id,
        "driver": driver,
        "driver_available": True,
        "credentials_configured": False,
        "live_execution_enabled": False,
    }


def _triggered_with_order_metadata(triggered: list[dict], orders: list[Any]) -> list[dict]:
    enriched: list[dict] = []
    order_index = 0
    for item in triggered:
        payload = dict(item)
        while order_index < len(orders):
            order = orders[order_index]
            order_index += 1
            if order.exit_kind != item.get("kind"):
                continue
            if order.exit_orders:
                exit_order = order.exit_orders[0]
                payload["oca_group"] = exit_order.oca_group
                payload["trigger_price"] = str(exit_order.trigger_price)
                payload["trigger_gap"] = _decimal_to_plain(_trigger_gap(item, exit_order))
            if order.canceled_exit_orders:
                payload["canceled_exit_orders"] = [
                    exit_order_payload(exit_order) for exit_order in order.canceled_exit_orders
                ]
            break
        enriched.append(payload)
    return enriched


def _trigger_gap(triggered: dict, exit_order: Any) -> Decimal:
    fill_price = Decimal(str(triggered["price"]))
    if exit_order.kind in {"stop_loss", "trailing_stop"}:
        if fill_price <= exit_order.trigger_price:
            return exit_order.trigger_price - fill_price
        return fill_price - exit_order.trigger_price
    if exit_order.kind == "take_profit":
        if fill_price >= exit_order.trigger_price:
            return fill_price - exit_order.trigger_price
        return exit_order.trigger_price - fill_price
    return abs(fill_price - exit_order.trigger_price)


def _bitunix_exchange_row() -> dict[str, Any]:
    return {
        "exchange_id": "bitunix",
        "driver": "bitunix-native",
        "driver_available": True,
        "credentials_configured": bitunix_credentials_configured(),
        "live_execution_enabled": bitunix_live_execution_enabled(),
    }

def _risk_config_to_dict(config: RiskConfig) -> dict[str, Any]:
    return {
        "max_order_notional": str(config.max_order_notional),
        "max_open_notional": str(config.max_open_notional),
        "max_symbol_open_notional": str(config.max_symbol_open_notional),
        "max_open_risk_amount": str(config.max_open_risk_amount),
        "max_open_risk_equity_pct": str(config.max_open_risk_equity_pct),
        "max_position_equity_pct": str(config.max_position_equity_pct),
        "max_risk_amount": str(config.max_risk_amount),
        "max_risk_per_trade_pct": str(config.max_risk_per_trade_pct),
        "max_entry_volatility_pct": str(config.max_entry_volatility_pct),
        "max_leverage": str(config.max_leverage),
        "max_daily_loss": str(config.max_daily_loss),
        "max_consecutive_losses": config.max_consecutive_losses,
        "require_stop_loss": config.require_stop_loss,
        "max_stop_loss_pct": str(config.max_stop_loss_pct),
        "max_trailing_stop_pct": str(config.max_trailing_stop_pct),
        "min_reward_risk_ratio": str(config.min_reward_risk_ratio),
        "min_total_reward_risk_ratio": str(config.min_total_reward_risk_ratio),
        "max_take_profit_targets": config.max_take_profit_targets,
        "max_slippage_bps": config.max_slippage_bps,
        "allowed_exchanges": sorted(config.allowed_exchanges),
        "allowed_symbols": sorted(config.allowed_symbols),
        "blocked_symbols": sorted(config.blocked_symbols),
        "require_fixed_stop_for_pending_trailing": config.require_fixed_stop_for_pending_trailing,
    }


def _account_state_to_dict(account_state: AccountState) -> dict[str, Any]:
    return {
        "equity": str(account_state.equity),
        "daily_pnl": str(account_state.daily_pnl),
        "open_notional": str(account_state.open_notional),
        "symbol_open_notional": str(account_state.symbol_open_notional),
        "open_risk_amount": str(account_state.open_risk_amount),
        "consecutive_losses": account_state.consecutive_losses,
    }


def _signal_to_dict(signal: CryptoSignal) -> dict[str, Any]:
    return {
        "signal_id": signal.signal_id,
        "source": signal.source,
        "symbol": signal.symbol,
        "side": signal.side,
        "exchange": signal.exchange,
        "market_type": signal.market_type,
        "quote_amount": str(signal.quote_amount) if signal.quote_amount is not None else None,
        "base_amount": str(signal.base_amount) if signal.base_amount is not None else None,
        "risk_amount": str(signal.risk_amount) if signal.risk_amount is not None else None,
        "risk_pct": str(signal.risk_pct) if signal.risk_pct is not None else None,
        "volatility_pct": str(signal.volatility_pct) if signal.volatility_pct is not None else None,
        "price": str(signal.price) if signal.price is not None else None,
        "stop_loss_pct": str(signal.stop_loss_pct) if signal.stop_loss_pct is not None else None,
        "stop_loss_price": str(signal.stop_loss_price) if signal.stop_loss_price is not None else None,
        "take_profit_pct": str(signal.take_profit_pct) if signal.take_profit_pct is not None else None,
        "take_profit_price": str(signal.take_profit_price) if signal.take_profit_price is not None else None,
        "take_profit_targets": [
            {
                "pct": str(target.pct) if target.pct is not None else None,
                "trigger_price": str(target.trigger_price) if target.trigger_price is not None else None,
                "close_pct": str(target.close_pct),
            }
            for target in signal.take_profit_targets
        ],
        "trailing_stop_pct": str(signal.trailing_stop_pct) if signal.trailing_stop_pct is not None else None,
        "trailing_stop_amount": str(signal.trailing_stop_amount) if signal.trailing_stop_amount is not None else None,
        "trailing_stop_price": str(signal.trailing_stop_price) if signal.trailing_stop_price is not None else None,
        "trailing_step_pct": str(signal.trailing_step_pct) if signal.trailing_step_pct is not None else None,
        "trailing_step_amount": str(signal.trailing_step_amount) if signal.trailing_step_amount is not None else None,
        "trailing_activation_pct": str(signal.trailing_activation_pct)
        if signal.trailing_activation_pct is not None
        else None,
        "trailing_activation_price": str(signal.trailing_activation_price)
        if signal.trailing_activation_price is not None
        else None,
        "trail_after_take_profit": signal.trail_after_take_profit,
        "breakeven_trigger_pct": str(signal.breakeven_trigger_pct)
        if signal.breakeven_trigger_pct is not None
        else None,
        "breakeven_after_take_profit": signal.breakeven_after_take_profit,
        "profit_lock_after_take_profit_pct": str(signal.profit_lock_after_take_profit_pct)
        if signal.profit_lock_after_take_profit_pct is not None
        else None,
        "max_hold_marks": signal.max_hold_marks,
        "oca_group": signal.oca_group,
        "leverage": str(signal.leverage),
        "max_slippage_bps": signal.max_slippage_bps,
        "reduce_only": signal.reduce_only,
        "strategy_id": signal.strategy_id,
    }


def _risk_decision_to_dict(decision: RiskDecision) -> dict[str, Any]:
    return {
        "approved": decision.approved,
        "reason_codes": decision.reason_codes,
        "order_notional": _decimal_to_plain(decision.order_notional) if decision.order_notional is not None else None,
    }


def _signal_preview(
    signal: CryptoSignal,
    engine: TradingEngine,
    *,
    require_approval: bool,
) -> dict[str, Any]:
    decision = evaluate_signal(signal, engine.risk_config, engine.account_state)
    if engine.halted:
        next_status = "halted"
    elif not decision.approved:
        next_status = "rejected"
    elif require_approval:
        next_status = "approval_required"
    else:
        next_status = "accepted"

    return {
        "signal": _signal_to_dict(signal),
        "risk": _risk_decision_to_dict(decision),
        "execution": {
            "next_status": next_status,
            "would_place_order": decision.approved and not engine.halted and not require_approval,
            "halted": engine.halted,
            "halt_reason": engine.halt_reason,
            "approval_required": require_approval,
        },
        "bracket_plan": _bracket_plan_to_dict(signal, decision, engine.account_state),
        "account": _account_state_to_dict(engine.account_state),
    }


def _bracket_plan_to_dict(signal: CryptoSignal, decision: RiskDecision, account_state: AccountState) -> dict[str, Any]:
    exits = build_exit_orders(signal)
    exit_side = "sell" if signal.side == "buy" else "buy"
    trailing_starts_armed = (
        (signal.trailing_stop_pct is not None or signal.trailing_stop_amount is not None)
        and signal.trailing_activation_pct is None
        and signal.trailing_activation_price is None
        and not signal.trail_after_take_profit
    )
    trailing_activation_price = _planned_trailing_activation_price(signal)
    stop_exit = next((exit_order for exit_order in exits if exit_order.kind == "stop_loss"), None)
    first_target = next((exit_order for exit_order in exits if exit_order.kind == "take_profit"), None)
    estimated_quantity = (
        decision.order_notional / signal.price
        if decision.order_notional is not None and signal.price is not None
        else None
    )
    worst_case_loss = _worst_case_loss(signal, decision.order_notional, stop_exit)
    first_target_reward = _target_reward(signal, decision.order_notional, first_target)
    total_target_reward = _total_target_reward(signal, decision.order_notional, exits)
    return {
        "entry_side": signal.side,
        "exit_side": exit_side,
        "oca_group": exits[0].oca_group if exits else None,
        "trailing_starts_armed": trailing_starts_armed,
        "trailing_activation_price": _decimal_to_plain(trailing_activation_price)
        if trailing_activation_price is not None
        else None,
        "trail_after_take_profit": signal.trail_after_take_profit,
        "breakeven_after_take_profit": signal.breakeven_after_take_profit,
        "profit_lock_after_take_profit_pct": _decimal_to_plain(signal.profit_lock_after_take_profit_pct)
        if signal.profit_lock_after_take_profit_pct is not None
        else None,
        "max_hold_marks": signal.max_hold_marks,
        "estimated_notional": _decimal_to_plain(decision.order_notional) if decision.order_notional is not None else None,
        "estimated_quantity": _decimal_to_plain(estimated_quantity) if estimated_quantity is not None else None,
        "worst_case_loss": _decimal_to_plain(worst_case_loss) if worst_case_loss is not None else None,
        "risk_pct_of_equity": _decimal_to_plain(worst_case_loss / account_state.equity * Decimal("100"))
        if worst_case_loss is not None and account_state.equity > 0
        else None,
        "first_target_reward": _decimal_to_plain(first_target_reward) if first_target_reward is not None else None,
        "first_target_reward_risk_ratio": _decimal_to_plain(first_target_reward / worst_case_loss)
        if first_target_reward is not None and worst_case_loss is not None and worst_case_loss > 0
        else None,
        "total_target_reward": _decimal_to_plain(total_target_reward) if total_target_reward is not None else None,
        "total_target_reward_risk_ratio": _decimal_to_plain(total_target_reward / worst_case_loss)
        if total_target_reward is not None and worst_case_loss is not None and worst_case_loss > 0
        else None,
        "exits": [
            {
                "kind": exit_order.kind,
                "trigger_price": str(exit_order.trigger_price),
                "close_pct": str(exit_order.close_pct),
                "oca_group": exit_order.oca_group,
                "status": exit_order.status,
                "trailing_step_pct": str(signal.trailing_step_pct)
                if exit_order.kind == "trailing_stop" and signal.trailing_step_pct is not None
                else None,
                "trailing_step_amount": str(signal.trailing_step_amount)
                if exit_order.kind == "trailing_stop" and signal.trailing_step_amount is not None
                else None,
                "trail_after_take_profit": signal.trail_after_take_profit
                if exit_order.kind == "trailing_stop"
                else None,
                "max_hold_marks": signal.max_hold_marks if exit_order.kind == "time_exit" else None,
            }
            for exit_order in exits
        ],
    }


def _worst_case_loss(signal: CryptoSignal, notional: Decimal | None, stop_exit: Any | None) -> Decimal | None:
    if notional is None or signal.price is None or stop_exit is None:
        return None
    stop_distance = (
        signal.price - stop_exit.trigger_price
        if signal.side == "buy"
        else stop_exit.trigger_price - signal.price
    )
    if stop_distance <= 0:
        return None
    return notional * stop_distance / signal.price


def _target_reward(signal: CryptoSignal, notional: Decimal | None, target_exit: Any | None) -> Decimal | None:
    if notional is None or signal.price is None or target_exit is None:
        return None
    target_distance = (
        target_exit.trigger_price - signal.price
        if signal.side == "buy"
        else signal.price - target_exit.trigger_price
    )
    if target_distance <= 0:
        return None
    target_notional = notional * target_exit.close_pct / Decimal("100")
    return target_notional * target_distance / signal.price


def _total_target_reward(signal: CryptoSignal, notional: Decimal | None, exits: list[Any]) -> Decimal | None:
    rewards = [
        reward
        for exit_order in exits
        if exit_order.kind == "take_profit"
        for reward in [_target_reward(signal, notional, exit_order)]
        if reward is not None
    ]
    if not rewards:
        return None
    return sum(rewards, Decimal("0"))


def _position_realized_pnl(exchange: PaperExchange, symbol: str) -> Decimal:
    position = exchange.positions.get(symbol)
    return position.realized_pnl if position else Decimal("0")


def _candle_preview_marks(
    *,
    direction: str,
    high: Decimal,
    low: Decimal,
    close: Decimal,
    open_price: Decimal | None,
    intrabar_policy: str,
) -> list[tuple[str, Decimal]]:
    adverse = low if direction == "long" else high
    favorable = high if direction == "long" else low
    if intrabar_policy == "favorable_first":
        phases = [("favorable", favorable), ("adverse", adverse), ("close", close)]
    else:
        phases = [("adverse", adverse), ("favorable", favorable), ("close", close)]
    if open_price is None:
        return phases
    return [("open", open_price), *phases]


def _simulate_bracket_mark_sequence(
    base_exchange: PaperExchange,
    *,
    signal_id: str,
    symbol: str,
    marks_to_preview: list[tuple[str, Decimal]],
) -> dict[str, Any]:
    preview_exchange = deepcopy(base_exchange)
    preview_exchange.lots = [
        lot for lot in preview_exchange.lots if lot.signal_id == signal_id or lot.symbol != symbol
    ]
    marks: list[dict[str, Any]] = []
    triggered_rows: list[dict[str, Any]] = []
    for phase, price in marks_to_preview:
        triggered = preview_exchange.update_price(symbol, price)
        for row in triggered:
            triggered_rows.append({"phase": phase, **row})
        marks.append(
            {
                "phase": phase,
                "price": str(price),
                "would_trigger": triggered,
                "preview_active_exits": _active_exits_to_dict(
                    preview_exchange.lots,
                    signal_id=signal_id,
                    mark_price=price,
                ),
                "preview_positions": preview_exchange.list_positions(),
            }
        )
    positions = preview_exchange.list_positions()
    final_position = next((position for position in positions if position["symbol"] == symbol), None)
    remaining_quantity = sum(
        (
            lot.remaining_quantity
            for lot in preview_exchange.lots
            if lot.signal_id == signal_id and lot.symbol == symbol and lot.remaining_quantity > 0
        ),
        Decimal("0"),
    )
    first_trigger = triggered_rows[0] if triggered_rows else None
    return {
        "marks": marks,
        "final_preview_positions": positions,
        "outcome": {
            "trigger_count": len(triggered_rows),
            "triggered_kinds": [row["kind"] for row in triggered_rows],
            "first_trigger_phase": first_trigger["phase"] if first_trigger else None,
            "first_trigger_kind": first_trigger["kind"] if first_trigger else None,
            "bracket_closed": remaining_quantity == 0,
            "remaining_quantity": _decimal_to_plain(remaining_quantity),
            "final_position_quantity": final_position["quantity"] if final_position else "0.00000000",
            "final_realized_pnl": final_position["realized_pnl"] if final_position else "0.00000000",
        },
    }


def _candle_policy_comparison(
    base_exchange: PaperExchange,
    *,
    signal_id: str,
    symbol: str,
    direction: str,
    high: Decimal,
    low: Decimal,
    close: Decimal,
    open_price: Decimal | None,
) -> dict[str, Any]:
    outcomes: dict[str, Any] = {}
    for policy in ("conservative_adverse_first", "favorable_first"):
        marks_to_preview = _candle_preview_marks(
            direction=direction,
            high=high,
            low=low,
            close=close,
            open_price=open_price,
            intrabar_policy=policy,
        )
        preview = _simulate_bracket_mark_sequence(
            base_exchange,
            signal_id=signal_id,
            symbol=symbol,
            marks_to_preview=marks_to_preview,
        )
        outcomes[policy] = {
            "prices": [str(price) for _, price in marks_to_preview],
            "phases": [phase for phase, _ in marks_to_preview],
            "outcome": preview["outcome"],
        }
    conservative_pnl = Decimal(outcomes["conservative_adverse_first"]["outcome"]["final_realized_pnl"])
    favorable_pnl = Decimal(outcomes["favorable_first"]["outcome"]["final_realized_pnl"])
    return {
        "policies": outcomes,
        "outcome_diverged": outcomes["conservative_adverse_first"]["outcome"] != outcomes["favorable_first"]["outcome"],
        "pnl_range": {
            "low": _decimal_to_plain(min(conservative_pnl, favorable_pnl)),
            "high": _decimal_to_plain(max(conservative_pnl, favorable_pnl)),
            "spread": _decimal_to_plain(abs(favorable_pnl - conservative_pnl)),
        },
    }


def _candle_ambiguity(lots: list[Any], *, high: Decimal, low: Decimal) -> dict[str, Any]:
    rows = [_lot_candle_ambiguity(lot, high=high, low=low) for lot in lots]
    protective_touched = sum(row["protective_touched"] for row in rows)
    profit_touched = sum(row["profit_touched"] for row in rows)
    ambiguous_lots = [row for row in rows if row["ambiguous"]]
    return {
        "ambiguous": bool(ambiguous_lots),
        "policy_note": "candle range contains both protective and profit exits; compare policies before trusting a single-fill outcome"
        if ambiguous_lots
        else None,
        "protective_touched_count": protective_touched,
        "profit_touched_count": profit_touched,
        "lots": rows,
    }


def _lot_candle_ambiguity(lot: Any, *, high: Decimal, low: Decimal) -> dict[str, Any]:
    protective: list[dict[str, str]] = []
    profit: list[dict[str, str]] = []
    for exit_order in lot.exit_orders:
        if exit_order.status not in {"open", "waiting"}:
            continue
        touched = _exit_touched_by_candle(lot, exit_order, high=high, low=low)
        if not touched:
            continue
        row = {"kind": exit_order.kind, "trigger_price": str(exit_order.trigger_price)}
        if exit_order.kind in {"stop_loss", "trailing_stop", "time_exit"}:
            protective.append(row)
        elif exit_order.kind == "take_profit":
            profit.append(row)
    return {
        "signal_id": lot.signal_id,
        "symbol": lot.symbol,
        "direction": lot.direction,
        "protective_touched": len(protective),
        "profit_touched": len(profit),
        "ambiguous": bool(protective and profit),
        "protective_exits": protective,
        "profit_exits": profit,
    }


def _exit_touched_by_candle(lot: Any, exit_order: Any, *, high: Decimal, low: Decimal) -> bool:
    if exit_order.kind == "time_exit":
        return False
    if lot.direction == "long":
        if exit_order.kind in {"stop_loss", "trailing_stop"}:
            return low <= exit_order.trigger_price
        if exit_order.kind == "take_profit":
            return high >= exit_order.trigger_price
        return False
    if exit_order.kind in {"stop_loss", "trailing_stop"}:
        return high >= exit_order.trigger_price
    if exit_order.kind == "take_profit":
        return low <= exit_order.trigger_price
    return False


def _planned_trailing_activation_price(signal: CryptoSignal) -> Decimal | None:
    if signal.price is None or (signal.trailing_stop_pct is None and signal.trailing_stop_amount is None):
        return None
    if signal.trailing_activation_price is not None:
        return signal.trailing_activation_price
    if signal.trailing_activation_pct is None:
        return None
    direction = Decimal("1") if signal.side == "buy" else Decimal("-1")
    return signal.price * (Decimal("1") + direction * signal.trailing_activation_pct / Decimal("100"))


def _decimal_to_plain(value: Decimal) -> str:
    return decimal_to_plain(value)


def _active_exits_to_dict(
    lots: list[Any],
    *,
    signal_id: str | None = None,
    mark_price: Decimal | None = None,
) -> list[dict[str, Any]]:
    return [
        _active_exit_to_dict(lot, exit_order, mark_price=mark_price)
        for lot in sorted(lots, key=lambda item: (item.symbol, item.signal_id))
        if lot.remaining_quantity > 0 and (signal_id is None or lot.signal_id == signal_id)
        for exit_order in lot.exit_orders
    ]


def _active_exit_to_dict(lot: Any, exit_order: Any, *, mark_price: Decimal | None) -> dict[str, Any]:
    activation_price = trailing_activation_price(lot) if exit_order.kind == "trailing_stop" else None
    distance = exit_distance(lot, exit_order, mark_price) if mark_price is not None else None
    trailing_telemetry = _trailing_telemetry(lot, exit_order, mark_price=mark_price)
    return {
        **active_exit_payload(lot, exit_order, bool_style="string"),
        "computed_trailing_activation_price": str(activation_price) if activation_price is not None else None,
        "next_trailing_trigger": trailing_telemetry["next_trailing_trigger"],
        "next_trailing_trigger_change": trailing_telemetry["next_trailing_trigger_change"],
        "trailing_step_required": trailing_telemetry["trailing_step_required"],
        "trailing_ratchet_ready_at_mark": trailing_telemetry["trailing_ratchet_ready_at_mark"],
        "trailing_activation_ready_at_mark": trailing_telemetry["trailing_activation_ready_at_mark"],
        "distance_to_trigger": str(distance) if distance is not None else None,
        "distance_to_trigger_pct": str(distance / mark_price * Decimal("100"))
        if distance is not None and mark_price is not None and mark_price > 0
        else None,
        "breakeven_trigger_pct": str(lot.breakeven_trigger_pct) if lot.breakeven_trigger_pct else None,
        "profit_lock_after_take_profit_pct": str(lot.profit_lock_after_take_profit_pct)
        if lot.profit_lock_after_take_profit_pct
        else None,
    }


def _trailing_preview_snapshot(
    lots: list[Any],
    *,
    signal_id: str,
    mark_price: Decimal | None,
) -> list[dict[str, Any]]:
    return [
        _active_exit_to_dict(lot, exit_order, mark_price=mark_price)
        for lot in lots
        if lot.signal_id == signal_id and lot.remaining_quantity > 0
        for exit_order in lot.exit_orders
        if exit_order.kind == "trailing_stop"
    ]


def _trailing_snapshot_ratcheted(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> bool:
    before_by_group = _trailing_snapshot_by_group(before)
    after_by_group = _trailing_snapshot_by_group(after)
    for key, before_row in before_by_group.items():
        after_row = after_by_group.get(key)
        if after_row is None:
            continue
        if before_row.get("trigger_price") != after_row.get("trigger_price"):
            return True
    return False


def _trailing_snapshot_activated(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> bool:
    before_by_group = _trailing_snapshot_by_group(before)
    after_by_group = _trailing_snapshot_by_group(after)
    for key, before_row in before_by_group.items():
        after_row = after_by_group.get(key)
        if after_row is None:
            continue
        if before_row.get("status") != "open" and after_row.get("status") == "open":
            return True
        if before_row.get("trailing_activated") == "false" and after_row.get("trailing_activated") == "true":
            return True
    return False


def _trailing_snapshot_by_group(rows: list[dict[str, Any]]) -> dict[tuple[str | None, str | None], dict[str, Any]]:
    return {
        (row.get("signal_id"), row.get("oca_group")): row
        for row in rows
    }


def _active_brackets_to_dict(lots: list[Any]) -> list[dict[str, Any]]:
    brackets: list[dict[str, Any]] = []
    for lot in sorted(lots, key=lambda item: (item.symbol, item.signal_id)):
        if lot.remaining_quantity <= 0 or not lot.exit_orders:
            continue
        brackets.append(
            {
                "signal_id": lot.signal_id,
                "symbol": lot.symbol,
                "direction": lot.direction,
                "remaining_quantity": str(lot.remaining_quantity),
                "entry_price": str(lot.entry_price),
                "summary": _bracket_summary(lot),
                "exits": _active_exits_to_dict([lot], signal_id=lot.signal_id),
            }
        )
    return brackets


def _bracket_exit_ladder_to_dict(lot: Any, *, mark_price: Decimal | None = None) -> dict[str, Any]:
    ordered_exits = sorted(lot.exit_orders, key=lambda exit_order: exit_ladder_sort_key(lot, exit_order))
    rows = [
        _exit_ladder_row(lot, exit_order, trigger_order=index + 1, mark_price=mark_price)
        for index, exit_order in enumerate(ordered_exits)
    ]
    return {
        "signal_id": lot.signal_id,
        "symbol": lot.symbol,
        "direction": lot.direction,
        "entry_price": str(lot.entry_price),
        "remaining_quantity": str(lot.remaining_quantity),
        "remaining_notional": _decimal_to_plain(lot.remaining_quantity * lot.entry_price),
        "exit_count": len(rows),
        "full_close_count": sum(1 for row in rows if row["would_close_remaining"]),
        "partial_close_count": sum(1 for row in rows if not row["would_close_remaining"]),
        "rows": rows,
    }


def _bracket_decision_support_to_dict(lot: Any, *, mark_price: Decimal | None = None) -> dict[str, Any]:
    rows = [
        _decision_support_row(lot, exit_order, trigger_order=index + 1, mark_price=mark_price)
        for index, exit_order in enumerate(
            sorted(lot.exit_orders, key=lambda exit_order: exit_ladder_sort_key(lot, exit_order))
        )
    ]
    next_trigger = next((row for row in rows if row["status"] == "open"), rows[0] if rows else None)
    trailing_rows = [row for row in rows if row["kind"] == "trailing_stop"]
    return {
        "signal_id": lot.signal_id,
        "symbol": lot.symbol,
        "direction": lot.direction,
        "entry_price": str(lot.entry_price),
        "remaining_quantity": str(lot.remaining_quantity),
        "summary": _bracket_summary(lot),
        "health": _bracket_health_row(lot),
        "next_open_trigger": next_trigger,
        "trailing": trailing_rows,
        "trigger_sequence": rows,
    }


def _bracket_coverage_to_dict(lot: Any) -> dict[str, Any]:
    return bracket_coverage_payload(lot)


def _bracket_preview_impact(
    lots: list[Any],
    *,
    preview_exchange: Any | None,
    signal_id: str,
    would_trigger: list[dict[str, Any]],
) -> dict[str, Any]:
    preview_lots = [
        lot
        for lot in (preview_exchange.lots if preview_exchange is not None else [])
        if lot.signal_id == signal_id and lot.remaining_quantity > 0 and lot.exit_orders
    ]
    before_quantity = sum((lot.remaining_quantity for lot in lots), Decimal("0"))
    after_quantity = sum((lot.remaining_quantity for lot in preview_lots), Decimal("0"))
    return {
        "mutates_state": False,
        "will_trigger": bool(would_trigger),
        "triggered_kinds": [item["kind"] for item in would_trigger],
        "will_close_bracket": before_quantity > 0 and after_quantity == 0,
        "remaining_quantity_before": _decimal_to_plain(before_quantity),
        "remaining_quantity_after": _decimal_to_plain(after_quantity),
        "quantity_delta": _decimal_to_plain(after_quantity - before_quantity),
        "trailing_ratchets": trailing_ratchet_impacts(lots, preview_lots),
    }


def _decision_support_row(
    lot: Any,
    exit_order: Any,
    *,
    trigger_order: int,
    mark_price: Decimal | None,
) -> dict[str, Any]:
    row = _exit_ladder_row(lot, exit_order, trigger_order=trigger_order, mark_price=mark_price)
    row["protective"] = exit_order.kind in {"stop_loss", "trailing_stop"}
    row["profit_taking"] = exit_order.kind == "take_profit"
    row["paper_only"] = True
    row.update(_trailing_telemetry(lot, exit_order, mark_price=mark_price))
    return row


def _exit_ladder_row(
    lot: Any,
    exit_order: Any,
    *,
    trigger_order: int,
    mark_price: Decimal | None,
) -> dict[str, Any]:
    quantity = exit_close_quantity(lot, exit_order)
    estimated_notional = quantity * exit_order.trigger_price
    estimated_pnl = exit_pnl(lot, exit_order, quantity)
    distance = exit_distance(lot, exit_order, mark_price) if mark_price is not None else None
    return {
        "trigger_order": trigger_order,
        "kind": exit_order.kind,
        "intent": exit_intent(exit_order),
        "status": exit_order.status,
        "trigger_price": str(exit_order.trigger_price),
        "close_pct": str(exit_order.close_pct),
        "estimated_exit_quantity": _decimal_to_plain(quantity),
        "estimated_exit_notional": _decimal_to_plain(estimated_notional),
        "estimated_pnl": _decimal_to_plain(estimated_pnl),
        "estimated_pnl_pct": _decimal_to_plain(estimated_pnl / (quantity * lot.entry_price) * Decimal("100"))
        if quantity > 0 and lot.entry_price > 0
        else None,
        "would_close_remaining": quantity >= lot.remaining_quantity,
        "oca_group": exit_order.oca_group,
        "distance_to_trigger": str(distance) if distance is not None else None,
        "distance_to_trigger_pct": str(distance / mark_price * Decimal("100"))
        if distance is not None and mark_price is not None and mark_price > 0
        else None,
        "trailing_activation_price": str(trailing_activation_price(lot))
        if exit_order.kind == "trailing_stop" and trailing_activation_price(lot) is not None
        else None,
        "marks_remaining": max(lot.max_hold_marks - lot.marks_seen, 0)
        if exit_order.kind == "time_exit" and lot.max_hold_marks is not None
        else None,
        **_trailing_telemetry(lot, exit_order, mark_price=mark_price),
    }

def _trailing_telemetry(lot: Any, exit_order: Any, *, mark_price: Decimal | None) -> dict[str, Any]:
    empty = {
        "next_trailing_trigger": None,
        "next_trailing_trigger_change": None,
        "trailing_step_required": None,
        "trailing_ratchet_ready_at_mark": None,
        "trailing_activation_ready_at_mark": None,
    }
    if exit_order.kind != "trailing_stop":
        return empty

    activation_ready = _trailing_activation_ready(lot, mark_price) if mark_price is not None else None
    step_required = _trailing_step_required(lot, exit_order.trigger_price)
    if mark_price is None or exit_order.status != "open":
        return {
            **empty,
            "trailing_step_required": _decimal_to_plain(step_required) if step_required is not None else None,
            "trailing_activation_ready_at_mark": str(activation_ready).lower() if activation_ready is not None else None,
        }

    next_trigger = _candidate_trailing_trigger(lot, mark_price)
    if next_trigger is None:
        return {
            **empty,
            "trailing_step_required": _decimal_to_plain(step_required) if step_required is not None else None,
            "trailing_ratchet_ready_at_mark": "false",
            "trailing_activation_ready_at_mark": str(activation_ready).lower() if activation_ready is not None else None,
        }
    change = next_trigger - exit_order.trigger_price if lot.direction == "long" else exit_order.trigger_price - next_trigger
    ratchet_ready = change > 0 and (step_required is None or change >= step_required)
    return {
        "next_trailing_trigger": str(next_trigger) if ratchet_ready else None,
        "next_trailing_trigger_change": _decimal_to_plain(change) if change > 0 else None,
        "trailing_step_required": _decimal_to_plain(step_required) if step_required is not None else None,
        "trailing_ratchet_ready_at_mark": str(ratchet_ready).lower(),
        "trailing_activation_ready_at_mark": str(activation_ready).lower() if activation_ready is not None else None,
    }


def _trailing_activation_ready(lot: Any, mark_price: Decimal | None) -> bool | None:
    activation_price = trailing_activation_price(lot)
    if activation_price is None or mark_price is None:
        return None
    return mark_price >= activation_price if lot.direction == "long" else mark_price <= activation_price


def _candidate_trailing_trigger(lot: Any, mark_price: Decimal) -> Decimal | None:
    if lot.trailing_stop_pct is None and lot.trailing_stop_amount is None:
        return None
    if lot.direction == "long":
        water_mark = max(lot.high_water_mark or lot.entry_price, mark_price)
        distance = _trailing_distance(lot, water_mark)
        return _money(water_mark - distance)
    water_mark = min(lot.low_water_mark or lot.entry_price, mark_price)
    distance = _trailing_distance(lot, water_mark)
    return _money(water_mark + distance)


def _trailing_distance(lot: Any, price: Decimal) -> Decimal:
    if lot.trailing_stop_amount is not None:
        return lot.trailing_stop_amount
    if lot.trailing_stop_pct is None:
        return Decimal("0")
    return price * lot.trailing_stop_pct / Decimal("100")


def _trailing_step_required(lot: Any, current_trigger: Decimal) -> Decimal | None:
    if lot.trailing_step_amount is not None:
        return lot.trailing_step_amount
    if lot.trailing_step_pct is not None:
        return current_trigger * lot.trailing_step_pct / Decimal("100")
    return Decimal("0")


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _bracket_risk_summary(lots: list[Any]) -> dict[str, Any]:
    active_lots = [lot for lot in lots if lot.remaining_quantity > 0 and lot.exit_orders]
    by_symbol: dict[str, dict[str, Any]] = {}
    totals = _empty_bracket_totals()
    for lot in active_lots:
        summary = _bracket_summary(lot)
        _accumulate_bracket_totals(totals, lot, summary)
        symbol_totals = by_symbol.setdefault(lot.symbol, _empty_bracket_totals(symbol=lot.symbol))
        _accumulate_bracket_totals(symbol_totals, lot, summary)

    return {
        "bracket_count": len(active_lots),
        "long_bracket_count": sum(1 for lot in active_lots if lot.direction == "long"),
        "short_bracket_count": sum(1 for lot in active_lots if lot.direction == "short"),
        "exit_count": sum(len(lot.exit_orders) for lot in active_lots),
        "trailing_stop_count": sum(
            1 for lot in active_lots for exit_order in lot.exit_orders if exit_order.kind == "trailing_stop"
        ),
        "pending_trailing_stop_count": sum(
            1
            for lot in active_lots
            for exit_order in lot.exit_orders
            if exit_order.kind == "trailing_stop" and exit_order.status == "pending_activation"
        ),
        "time_stop_count": sum(1 for lot in active_lots for exit_order in lot.exit_orders if exit_order.kind == "time_exit"),
        "totals": _bracket_totals_to_dict(totals),
        "by_symbol": [_bracket_totals_to_dict(by_symbol[symbol]) for symbol in sorted(by_symbol)],
    }


def _bracket_health(lots: list[Any]) -> dict[str, Any]:
    active_lots = [lot for lot in lots if lot.remaining_quantity > 0 and lot.exit_orders]
    oca_conflict_signal_ids = _oca_conflict_signal_ids(active_lots)
    rows = [
        _bracket_health_row(lot, oca_conflict_signal_ids=oca_conflict_signal_ids)
        for lot in sorted(active_lots, key=lambda item: (item.symbol, item.signal_id))
    ]
    issue_counts: dict[str, int] = {}
    for row in rows:
        for issue in row["issues"]:
            issue_counts[issue] = issue_counts.get(issue, 0) + 1
    return {
        "bracket_count": len(rows),
        "healthy_count": sum(1 for row in rows if row["status"] == "healthy"),
        "attention_count": sum(1 for row in rows if row["status"] == "attention"),
        "issue_counts": issue_counts,
        "brackets": rows,
    }


def _bracket_health_row(lot: Any, *, oca_conflict_signal_ids: set[str] | None = None) -> dict[str, Any]:
    summary = _bracket_summary(lot)
    protective_exit = _nearest_protective_exit(lot)
    first_reward_ratio = _decimal_or_none(summary["first_target_reward_risk_ratio"])
    total_reward_ratio = _decimal_or_none(summary["total_target_reward_risk_ratio"])
    open_take_profit_count = sum(
        1 for exit_order in lot.exit_orders if exit_order.kind == "take_profit" and exit_order.status == "open"
    )
    pending_trailing_count = sum(
        1
        for exit_order in lot.exit_orders
        if exit_order.kind == "trailing_stop" and exit_order.status in {"pending_activation", "pending_take_profit"}
    )
    issues: list[str] = []
    if protective_exit is None:
        issues.append("no_open_protective_exit")
    elif _decimal_or_zero(summary["worst_case_loss"]) > 0:
        issues.append("protective_exit_still_at_risk")
    if pending_trailing_count:
        issues.append("trailing_stop_pending")
    if open_take_profit_count == 0:
        issues.append("no_open_take_profit_exit")
    elif first_reward_ratio is not None and first_reward_ratio < 1:
        issues.append("first_target_reward_below_risk")
    if total_reward_ratio is not None and total_reward_ratio < 1:
        issues.append("total_target_reward_below_risk")
    if oca_conflict_signal_ids and lot.signal_id in oca_conflict_signal_ids:
        issues.append("oca_group_reused_across_brackets")
    return {
        "signal_id": lot.signal_id,
        "symbol": lot.symbol,
        "direction": lot.direction,
        "status": "attention" if issues else "healthy",
        "issues": issues,
        "remaining_quantity": str(lot.remaining_quantity),
        "remaining_notional": summary["remaining_notional"],
        "protective_exit_kind": summary["protective_exit_kind"],
        "protective_trigger_price": summary["protective_trigger_price"],
        "worst_case_loss": summary["worst_case_loss"],
        "protective_locked_pnl": summary["protective_locked_pnl"],
        "first_target_reward_risk_ratio": summary["first_target_reward_risk_ratio"],
        "total_target_reward_risk_ratio": summary["total_target_reward_risk_ratio"],
        "open_take_profit_count": open_take_profit_count,
        "pending_trailing_count": pending_trailing_count,
        "oca_groups": _lot_oca_groups(lot),
    }


def _bracket_oca_groups(lots: list[Any]) -> dict[str, Any]:
    active_lots = [lot for lot in lots if lot.remaining_quantity > 0 and lot.exit_orders]
    groups: dict[str, dict[str, Any]] = {}
    for lot in active_lots:
        for group in _lot_oca_groups(lot):
            row = groups.setdefault(
                group,
                {
                    "oca_group": group,
                    "signal_ids": set(),
                    "symbols": set(),
                    "directions": set(),
                    "exit_count": 0,
                },
            )
            row["signal_ids"].add(lot.signal_id)
            row["symbols"].add(lot.symbol)
            row["directions"].add(lot.direction)
            row["exit_count"] += sum(1 for exit_order in lot.exit_orders if exit_order.oca_group == group)

    rows: list[dict[str, Any]] = []
    for group in sorted(groups):
        row = groups[group]
        signal_ids = sorted(row["signal_ids"])
        symbols = sorted(row["symbols"])
        directions = sorted(row["directions"])
        notes: list[str] = []
        if len(signal_ids) > 1:
            notes.append("oca_group_reused_across_brackets")
        if len(symbols) > 1:
            notes.append("oca_group_spans_symbols")
        if len(directions) > 1:
            notes.append("oca_group_spans_directions")
        rows.append(
            {
                "oca_group": group,
                "bracket_count": len(signal_ids),
                "exit_count": row["exit_count"],
                "signal_ids": signal_ids,
                "symbols": symbols,
                "directions": directions,
                "reused_across_brackets": len(signal_ids) > 1,
                "notes": notes,
            }
        )
    return {
        "group_count": len(rows),
        "reused_group_count": sum(1 for row in rows if row["reused_across_brackets"]),
        "groups": rows,
    }


def _oca_conflict_signal_ids(lots: list[Any]) -> set[str]:
    conflicts: set[str] = set()
    for row in _bracket_oca_groups(lots)["groups"]:
        if row["reused_across_brackets"]:
            conflicts.update(row["signal_ids"])
    return conflicts


def _lot_oca_groups(lot: Any) -> list[str]:
    return sorted(
        {
            exit_order.oca_group
            for exit_order in lot.exit_orders
            if exit_order.status not in {"canceled", "filled"} and exit_order.oca_group
        }
    )


def _empty_bracket_totals(*, symbol: str | None = None) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "bracket_count": 0,
        "remaining_notional": Decimal("0"),
        "worst_case_loss": Decimal("0"),
        "protective_locked_pnl": Decimal("0"),
        "first_target_reward": Decimal("0"),
        "total_target_reward": Decimal("0"),
    }


def _accumulate_bracket_totals(totals: dict[str, Any], lot: Any, summary: dict[str, str | None]) -> None:
    totals["bracket_count"] += 1
    totals["remaining_notional"] += lot.remaining_quantity * lot.entry_price
    totals["worst_case_loss"] += _decimal_or_zero(summary["worst_case_loss"])
    totals["protective_locked_pnl"] += _decimal_or_zero(summary["protective_locked_pnl"])
    totals["first_target_reward"] += _decimal_or_zero(summary["first_target_reward"])
    totals["total_target_reward"] += _decimal_or_zero(summary["total_target_reward"])


def _bracket_totals_to_dict(totals: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "bracket_count": totals["bracket_count"],
        "remaining_notional": _decimal_to_plain(totals["remaining_notional"]),
        "worst_case_loss": _decimal_to_plain(totals["worst_case_loss"]),
        "protective_locked_pnl": _decimal_to_plain(totals["protective_locked_pnl"]),
        "first_target_reward": _decimal_to_plain(totals["first_target_reward"]),
        "first_target_reward_risk_ratio": _decimal_to_plain(
            totals["first_target_reward"] / totals["worst_case_loss"]
        )
        if totals["worst_case_loss"] > 0
        else None,
        "total_target_reward": _decimal_to_plain(totals["total_target_reward"]),
        "total_target_reward_risk_ratio": _decimal_to_plain(
            totals["total_target_reward"] / totals["worst_case_loss"]
        )
        if totals["worst_case_loss"] > 0
        else None,
    }
    if totals["symbol"] is not None:
        payload["symbol"] = totals["symbol"]
    return payload


def _decimal_or_zero(value: str | None) -> Decimal:
    return Decimal(value) if value is not None else Decimal("0")


def _decimal_or_none(value: str | None) -> Decimal | None:
    return Decimal(value) if value is not None else None


def _bracket_summary(lot: Any) -> dict[str, str | None]:
    remaining_notional = lot.remaining_quantity * lot.entry_price
    protective_exit = _nearest_protective_exit(lot)
    first_target = _nearest_take_profit_exit(lot)
    worst_case_loss = _lot_protective_loss(lot, protective_exit)
    protective_locked_pnl = _lot_protective_locked_pnl(lot, protective_exit)
    protective_distance_pct = _lot_protective_distance_pct(lot, protective_exit)
    first_target_reward = _lot_target_reward(lot, first_target)
    total_target_reward = _lot_total_target_reward(lot)
    return {
        "remaining_notional": _decimal_to_plain(remaining_notional),
        "protective_exit_kind": protective_exit.kind if protective_exit is not None else None,
        "protective_trigger_price": str(protective_exit.trigger_price) if protective_exit is not None else None,
        "protective_distance_pct": _decimal_to_plain(protective_distance_pct)
        if protective_distance_pct is not None
        else None,
        "worst_case_loss": _decimal_to_plain(worst_case_loss) if worst_case_loss is not None else None,
        "protective_locked_pnl": _decimal_to_plain(protective_locked_pnl)
        if protective_locked_pnl is not None
        else None,
        "first_target_price": str(first_target.trigger_price) if first_target is not None else None,
        "first_target_reward": _decimal_to_plain(first_target_reward) if first_target_reward is not None else None,
        "first_target_reward_risk_ratio": _decimal_to_plain(first_target_reward / worst_case_loss)
        if first_target_reward is not None and worst_case_loss is not None and worst_case_loss > 0
        else None,
        "total_target_reward": _decimal_to_plain(total_target_reward) if total_target_reward is not None else None,
        "total_target_reward_risk_ratio": _decimal_to_plain(total_target_reward / worst_case_loss)
        if total_target_reward is not None and worst_case_loss is not None and worst_case_loss > 0
        else None,
    }


def _nearest_protective_exit(lot: Any) -> Any | None:
    protective_exits = [
        exit_order
        for exit_order in lot.exit_orders
        if exit_order.kind in {"stop_loss", "trailing_stop"} and exit_order.status == "open"
    ]
    if lot.direction == "long":
        return max(protective_exits, key=lambda item: item.trigger_price, default=None)
    return min(protective_exits, key=lambda item: item.trigger_price, default=None)


def _nearest_take_profit_exit(lot: Any) -> Any | None:
    targets = [
        exit_order
        for exit_order in lot.exit_orders
        if exit_order.kind == "take_profit" and exit_order.status != "canceled"
    ]
    if lot.direction == "long":
        return min(targets, key=lambda item: item.trigger_price, default=None)
    return max(targets, key=lambda item: item.trigger_price, default=None)


def _lot_protective_loss(lot: Any, protective_exit: Any | None) -> Decimal | None:
    if protective_exit is None:
        return None
    if lot.direction == "long":
        distance = lot.entry_price - protective_exit.trigger_price
    else:
        distance = protective_exit.trigger_price - lot.entry_price
    return max(distance, Decimal("0")) * lot.remaining_quantity


def _lot_protective_locked_pnl(lot: Any, protective_exit: Any | None) -> Decimal | None:
    if protective_exit is None:
        return None
    if lot.direction == "long":
        distance = protective_exit.trigger_price - lot.entry_price
    else:
        distance = lot.entry_price - protective_exit.trigger_price
    return distance * lot.remaining_quantity


def _lot_protective_distance_pct(lot: Any, protective_exit: Any | None) -> Decimal | None:
    if protective_exit is None or lot.entry_price <= 0:
        return None
    if lot.direction == "long":
        distance = lot.entry_price - protective_exit.trigger_price
    else:
        distance = protective_exit.trigger_price - lot.entry_price
    return distance / lot.entry_price * Decimal("100")


def _lot_target_reward(lot: Any, target_exit: Any | None) -> Decimal | None:
    if target_exit is None:
        return None
    if lot.direction == "long":
        distance = target_exit.trigger_price - lot.entry_price
    else:
        distance = lot.entry_price - target_exit.trigger_price
    if distance <= 0:
        return None
    target_quantity = min(lot.remaining_quantity, lot.original_quantity * target_exit.close_pct / Decimal("100"))
    return distance * target_quantity


def _lot_total_target_reward(lot: Any) -> Decimal | None:
    rewards = [
        reward
        for exit_order in lot.exit_orders
        if exit_order.kind == "take_profit" and exit_order.status != "canceled"
        for reward in [_lot_target_reward(lot, exit_order)]
        if reward is not None
    ]
    if not rewards:
        return None
    return sum(rewards, Decimal("0"))
