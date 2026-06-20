from __future__ import annotations

import os
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .approvals import ApprovalQueue
from .backtest import run_signal_backtest, run_signal_candle_backtest
from .bot_event_bus import BotEvent, event_bus
from .config import load_settings
from .edge_actions import apply_edge_action
from .engine import TradingEngine
from .exchanges.bitunix_adapter import (
    BitunixConfigurationError,
    BitunixRequestError,
    BitunixRestClient,
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
        account_state = AccountState(open_notional=paper_exchange.open_notional())
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

    @app.get("/exchanges/bitunix/futures/account")
    def bitunix_futures_account(margin_coin: str = "USDT") -> dict[str, Any]:
        try:
            return BitunixRestClient(credentials=load_bitunix_credentials_from_env()).get_futures_account(margin_coin)
        except BitunixConfigurationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except BitunixRequestError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    async def _market_price_payload(request: Request) -> tuple[str, Decimal]:
        payload = await request.json()
        try:
            symbol = normalize_symbol(payload.get("symbol"))
            price = _positive_decimal(payload.get("price"))
        except (SignalValidationError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return symbol, price

    @app.post("/market/price/preview")
    async def market_price_preview(request: Request) -> dict[str, Any]:
        symbol, price = await _market_price_payload(request)
        return {
            "symbol": symbol,
            "price": str(price),
            "would_trigger": engine.exchange.preview_price(symbol, price),
            "active_exits": _active_exits_to_dict(engine.exchange.lots, mark_price=price),
            "positions": engine.exchange.list_positions(),
            "account": _account_state_to_dict(engine.account_state),
        }

    @app.post("/market/price")
    async def market_price(request: Request) -> dict[str, Any]:
        symbol, price = await _market_price_payload(request)
        order_offset = len(engine.exchange.orders)
        update = engine.mark_price(symbol, price)
        if repository:
            for order in engine.exchange.orders[order_offset:]:
                repository.save_order(order)
            if update.triggered:
                repository.record_audit(
                    "exit.triggered",
                    {"symbol": symbol, "price": str(price), "triggered": update.triggered},
                )
        return {
            "symbol": symbol,
            "price": str(price),
            "triggered": update.triggered,
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
        return {
            "signal_id": signal_id,
            "symbol": symbol,
            "price": str(price),
            "would_trigger": engine.exchange.preview_bracket(signal_id, price),
            "active_exits": _active_exits_to_dict(lots, signal_id=signal_id, mark_price=price),
            "positions": engine.exchange.list_positions(),
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
            if candles_payload:
                candles = [_candle_payload(candle) for candle in candles_payload]
                return run_signal_candle_backtest(engine, signal, candles, costs=costs).to_dict()
            prices = [_positive_decimal(price) for price in marks_payload]
        except (SignalValidationError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return run_signal_backtest(engine, signal, prices, costs=costs).to_dict()

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


def _execution_cost_payload(payload: dict[str, Any]) -> ExecutionCostConfig:
    costs = payload.get("costs") if isinstance(payload.get("costs"), dict) else {}
    fee_bps = costs.get("fee_bps") if costs else payload.get("fee_bps")
    slippage_bps = costs.get("slippage_bps") if costs else payload.get("slippage_bps")
    return ExecutionCostConfig(
        fee_bps=_non_negative_decimal(fee_bps, default=Decimal("0")),
        slippage_bps=_non_negative_decimal(slippage_bps, default=Decimal("0")),
    )


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
    )


def _exchange_row(exchange_id: str, driver: str) -> dict[str, Any]:
    return {
        "exchange_id": exchange_id,
        "driver": driver,
        "driver_available": True,
        "credentials_configured": False,
        "live_execution_enabled": False,
    }


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
        "max_position_equity_pct": str(config.max_position_equity_pct),
        "max_risk_per_trade_pct": str(config.max_risk_per_trade_pct),
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
    }


def _account_state_to_dict(account_state: AccountState) -> dict[str, Any]:
    return {
        "equity": str(account_state.equity),
        "daily_pnl": str(account_state.daily_pnl),
        "open_notional": str(account_state.open_notional),
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
        "breakeven_trigger_pct": str(signal.breakeven_trigger_pct)
        if signal.breakeven_trigger_pct is not None
        else None,
        "breakeven_after_take_profit": signal.breakeven_after_take_profit,
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
        "breakeven_after_take_profit": signal.breakeven_after_take_profit,
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
    return format(value, "f")


def _active_exits_to_dict(
    lots: list[Any],
    *,
    signal_id: str | None = None,
    mark_price: Decimal | None = None,
) -> list[dict[str, str | None]]:
    return [
        _active_exit_to_dict(lot, exit_order, mark_price=mark_price)
        for lot in sorted(lots, key=lambda item: (item.symbol, item.signal_id))
        if lot.remaining_quantity > 0 and (signal_id is None or lot.signal_id == signal_id)
        for exit_order in lot.exit_orders
    ]


def _active_exit_to_dict(lot: Any, exit_order: Any, *, mark_price: Decimal | None) -> dict[str, str | None]:
    trailing_activation_price = _lot_trailing_activation_price(lot) if exit_order.kind == "trailing_stop" else None
    distance = _exit_distance(lot, exit_order, mark_price) if mark_price is not None else None
    return {
        "symbol": lot.symbol,
        "direction": lot.direction,
        "kind": exit_order.kind,
        "trigger_price": str(exit_order.trigger_price),
        "close_pct": str(exit_order.close_pct),
        "oca_group": exit_order.oca_group,
        "status": exit_order.status,
        "trailing_stop_pct": str(lot.trailing_stop_pct) if exit_order.kind == "trailing_stop" and lot.trailing_stop_pct else None,
        "trailing_stop_amount": str(lot.trailing_stop_amount)
        if exit_order.kind == "trailing_stop" and lot.trailing_stop_amount
        else None,
        "initial_trailing_stop_price": str(lot.trailing_stop_price)
        if exit_order.kind == "trailing_stop" and lot.trailing_stop_price
        else None,
        "trailing_step_pct": str(lot.trailing_step_pct)
        if exit_order.kind == "trailing_stop" and lot.trailing_step_pct
        else None,
        "trailing_step_amount": str(lot.trailing_step_amount)
        if exit_order.kind == "trailing_stop" and lot.trailing_step_amount
        else None,
        "trailing_activation_pct": str(lot.trailing_activation_pct)
        if exit_order.kind == "trailing_stop" and lot.trailing_activation_pct
        else None,
        "trailing_activation_price": str(lot.trailing_activation_price)
        if exit_order.kind == "trailing_stop" and lot.trailing_activation_price
        else None,
        "trailing_activation_price": str(trailing_activation_price) if trailing_activation_price is not None else None,
        "trailing_activated": str(lot.trailing_activated).lower() if exit_order.kind == "trailing_stop" else None,
        "high_water_mark": str(lot.high_water_mark) if exit_order.kind == "trailing_stop" and lot.high_water_mark else None,
        "low_water_mark": str(lot.low_water_mark) if exit_order.kind == "trailing_stop" and lot.low_water_mark else None,
        "distance_to_trigger": str(distance) if distance is not None else None,
        "distance_to_trigger_pct": str(distance / mark_price * Decimal("100"))
        if distance is not None and mark_price is not None and mark_price > 0
        else None,
        "breakeven_trigger_pct": str(lot.breakeven_trigger_pct) if lot.breakeven_trigger_pct else None,
        "breakeven_after_take_profit": str(lot.breakeven_after_take_profit).lower(),
        "breakeven_applied": str(lot.breakeven_applied).lower(),
        "signal_id": lot.signal_id,
        "remaining_quantity": str(lot.remaining_quantity),
        "entry_price": str(lot.entry_price),
    }


def _lot_trailing_activation_price(lot: Any) -> Decimal | None:
    if lot.trailing_stop_pct is None and lot.trailing_stop_amount is None:
        return None
    if lot.trailing_activation_price is not None:
        return lot.trailing_activation_price
    if lot.trailing_activation_pct is None:
        return None
    direction = Decimal("1") if lot.direction == "long" else Decimal("-1")
    return lot.entry_price * (Decimal("1") + direction * lot.trailing_activation_pct / Decimal("100"))


def _exit_distance(lot: Any, exit_order: Any, mark_price: Decimal) -> Decimal:
    if lot.direction == "long":
        if exit_order.kind in {"stop_loss", "trailing_stop"}:
            return mark_price - exit_order.trigger_price
        return exit_order.trigger_price - mark_price
    if exit_order.kind in {"stop_loss", "trailing_stop"}:
        return exit_order.trigger_price - mark_price
    return mark_price - exit_order.trigger_price


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
        if exit_order.kind in {"stop_loss", "trailing_stop"} and exit_order.status != "canceled"
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
