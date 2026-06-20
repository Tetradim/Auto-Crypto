from __future__ import annotations

from datetime import datetime

from .signals import CryptoSignal


class ApprovalQueue:
    """In-memory pending signal queue for human approval workflows."""

    def __init__(self) -> None:
        self._pending: dict[str, tuple[CryptoSignal, str]] = {}

    def add(self, signal: CryptoSignal) -> None:
        self._pending[signal.signal_id] = (signal, datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"))

    def get(self, signal_id: str) -> CryptoSignal | None:
        item = self._pending.get(signal_id)
        return item[0] if item else None

    def pop(self, signal_id: str) -> CryptoSignal | None:
        item = self._pending.pop(signal_id, None)
        return item[0] if item else None

    def list_pending(self) -> list[dict]:
        return [_summary(signal, created_at=created_at) for signal, created_at in self._pending.values()]


def _summary(signal: CryptoSignal, *, created_at: str) -> dict:
    return {
        "signal_id": signal.signal_id,
        "source": signal.source,
        "symbol": signal.symbol,
        "side": signal.side,
        "exchange": signal.exchange,
        "quote_amount": str(signal.quote_amount) if signal.quote_amount is not None else None,
        "base_amount": str(signal.base_amount) if signal.base_amount is not None else None,
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
        "strategy_id": signal.strategy_id,
        "reduce_only": signal.reduce_only,
        "created_at": created_at,
    }
