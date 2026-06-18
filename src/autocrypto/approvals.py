from __future__ import annotations

from datetime import datetime

from .signals import CryptoSignal


class ApprovalQueue:
    """In-memory pending signal queue for human approval workflows."""

    def __init__(self) -> None:
        self._pending: dict[str, tuple[CryptoSignal, str]] = {}

    def add(self, signal: CryptoSignal) -> None:
        self._pending[signal.signal_id] = (signal, datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"))

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
        "take_profit_pct": str(signal.take_profit_pct) if signal.take_profit_pct is not None else None,
        "strategy_id": signal.strategy_id,
        "created_at": created_at,
    }
