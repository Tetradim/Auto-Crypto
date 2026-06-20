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
from .execution import PaperExchange
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

    @app.post("/market/price")
    async def market_price(request: Request) -> dict[str, Any]:
        payload = await request.json()
        try:
            symbol = normalize_symbol(payload.get("symbol"))
            price = _positive_decimal(payload.get("price"))
        except (SignalValidationError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        order_offset = len(engine.exchange.orders)
        realized_before = _position_realized_pnl(engine.exchange, symbol)
        triggered = engine.exchange.update_price(symbol, price)
        if triggered:
            realized_pnl_delta = _position_realized_pnl(engine.exchange, symbol) - realized_before
            engine.account_state.daily_pnl += realized_pnl_delta
            if realized_pnl_delta < 0:
                engine.account_state.consecutive_losses += 1
            elif realized_pnl_delta > 0:
                engine.account_state.consecutive_losses = 0
            engine.account_state.open_notional = engine.exchange.open_notional()
        if repository:
            for order in engine.exchange.orders[order_offset:]:
                repository.save_order(order)
            if triggered:
                repository.record_audit(
                    "exit.triggered",
                    {"symbol": symbol, "price": str(price), "triggered": triggered},
                )
        return {
            "symbol": symbol,
            "price": str(price),
            "triggered": triggered,
            "realized_pnl_delta": str(realized_pnl_delta) if triggered else "0",
            "daily_pnl": str(engine.account_state.daily_pnl),
            "consecutive_losses": engine.account_state.consecutive_losses,
            "positions": engine.exchange.list_positions(),
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
        "max_leverage": str(config.max_leverage),
        "max_daily_loss": str(config.max_daily_loss),
        "max_consecutive_losses": config.max_consecutive_losses,
        "require_stop_loss": config.require_stop_loss,
        "max_stop_loss_pct": str(config.max_stop_loss_pct),
        "min_reward_risk_ratio": str(config.min_reward_risk_ratio),
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
        "price": str(signal.price) if signal.price is not None else None,
        "stop_loss_pct": str(signal.stop_loss_pct) if signal.stop_loss_pct is not None else None,
        "take_profit_pct": str(signal.take_profit_pct) if signal.take_profit_pct is not None else None,
        "trailing_stop_pct": str(signal.trailing_stop_pct) if signal.trailing_stop_pct is not None else None,
        "leverage": str(signal.leverage),
        "max_slippage_bps": signal.max_slippage_bps,
        "strategy_id": signal.strategy_id,
    }


def _risk_decision_to_dict(decision: RiskDecision) -> dict[str, Any]:
    return {
        "approved": decision.approved,
        "reason_codes": decision.reason_codes,
        "order_notional": str(decision.order_notional) if decision.order_notional is not None else None,
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
        "account": _account_state_to_dict(engine.account_state),
    }


def _active_exits_to_dict(lots: list[Any]) -> list[dict[str, str]]:
    return [
        {
            "symbol": lot.symbol,
            "kind": exit_order.kind,
            "trigger_price": str(exit_order.trigger_price),
            "trailing_stop_pct": str(lot.trailing_stop_pct) if exit_order.kind == "trailing_stop" and lot.trailing_stop_pct else None,
            "high_water_mark": str(lot.high_water_mark) if exit_order.kind == "trailing_stop" and lot.high_water_mark else None,
            "signal_id": lot.signal_id,
            "remaining_quantity": str(lot.remaining_quantity),
            "entry_price": str(lot.entry_price),
        }
        for lot in sorted(lots, key=lambda item: (item.symbol, item.signal_id))
        if lot.remaining_quantity > 0
        for exit_order in lot.exit_orders
    ]


def _position_realized_pnl(exchange: PaperExchange, symbol: str) -> Decimal:
    position = exchange.positions.get(symbol)
    return position.realized_pnl if position else Decimal("0")
