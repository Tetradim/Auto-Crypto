from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from .engine import TradingEngine
from .execution import PaperExchange
from .repository import SQLiteRepository
from .risk import AccountState, RiskConfig
from .security import (
    InMemoryWebhookReplayStore,
    WebhookReplayError,
    WebhookSignatureError,
    verify_webhook_signature,
)
from .signals import SignalValidationError, normalize_signal


def create_app(
    *,
    exchange: PaperExchange | None = None,
    risk_config: RiskConfig | None = None,
    account_state: AccountState | None = None,
    webhook_secret: str | None = None,
    webhook_clock: Callable[[], float] | None = None,
    webhook_tolerance_seconds: int | None = None,
    repository: SQLiteRepository | None = None,
) -> FastAPI:
    app = FastAPI(title="Auto-Crypto", version="0.1.0")
    engine = TradingEngine(
        exchange=exchange or PaperExchange(),
        risk_config=risk_config or RiskConfig(),
        account_state=account_state or AccountState(),
    )
    secret = webhook_secret if webhook_secret is not None else os.getenv("AUTO_CRYPTO_WEBHOOK_SECRET")
    replay_store = InMemoryWebhookReplayStore()

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "default_mode": "paper", "orders": len(engine.exchange.orders)}

    @app.post("/webhooks/tradingview")
    async def tradingview_webhook(request: Request) -> dict[str, Any]:
        body = await request.body()
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

        payload = await request.json()
        try:
            signal = normalize_signal(payload, source="tradingview")
        except SignalValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if repository:
            repository.save_signal(signal)
            repository.record_audit("signal.received", {"signal_id": signal.signal_id})
        result = engine.process_signal(signal)
        if repository:
            if result.order:
                repository.save_order(result.order)
                repository.record_audit("order.accepted", {"order_id": result.order.order_id})
            elif result.status == "rejected":
                repository.record_audit(
                    "order.rejected",
                    {"signal_id": signal.signal_id, "reason_codes": result.decision.reason_codes},
                )
        return result.to_dict()

    @app.get("/orders")
    def orders() -> dict[str, Any]:
        if repository:
            return {"orders": repository.list_orders()}
        return {"orders": [order.to_dict() for order in engine.exchange.orders]}

    @app.get("/signals")
    def signals() -> dict[str, Any]:
        return {"signals": repository.list_signals() if repository else []}

    @app.get("/audit")
    def audit() -> dict[str, Any]:
        return {"events": [event.to_dict() for event in repository.list_audit()] if repository else []}

    return app


app = create_app()
