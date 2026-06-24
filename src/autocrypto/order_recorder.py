from __future__ import annotations

from datetime import datetime, timezone

from .execution import PaperOrder
from .repository import SQLiteRepository


COOLDOWN_STATE_PREFIX = "cooldown:"


def save_order_with_runtime_state(repository: SQLiteRepository, order: PaperOrder) -> None:
    repository.save_order(order)
    if not _starts_reentry_cooldown(order):
        return
    repository.set_runtime_state(
        cooldown_state_key(order.symbol, "*"),
        {
            "symbol": order.symbol,
            "strategy_id": "*",
            "last_exit_at": datetime.now(timezone.utc).isoformat(),
            "order_id": order.order_id,
            "signal_id": order.signal_id,
        },
    )


def cooldown_state_key(symbol: str, strategy_id: str) -> str:
    strategy = (strategy_id or "*").strip().lower() or "*"
    return f"{COOLDOWN_STATE_PREFIX}{symbol}:{strategy}"


def _starts_reentry_cooldown(order: PaperOrder) -> bool:
    if not order.reduce_only:
        return False
    return bool(order.exit_kind) or order.netted_quantity > 0
