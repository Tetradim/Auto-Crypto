from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from .ccxt_adapter import ExchangeCapabilities
from ..execution import ExitOrder, build_exit_orders
from ..signals import CryptoSignal


@dataclass(frozen=True)
class PlannedOrderLeg:
    role: str
    side: str
    order_type: str
    trigger_price: Decimal | None = None
    limit_price: Decimal | None = None
    close_pct: Decimal = Decimal("100")
    reduce_only: bool = False
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "side": self.side,
            "order_type": self.order_type,
            "trigger_price": str(self.trigger_price) if self.trigger_price is not None else None,
            "limit_price": str(self.limit_price) if self.limit_price is not None else None,
            "close_pct": str(self.close_pct),
            "reduce_only": self.reduce_only,
            "params": self.params,
        }


@dataclass(frozen=True)
class BracketExecutionPlan:
    exchange_id: str
    strategy: str
    live_order_safe: bool
    entry: PlannedOrderLeg
    exits: tuple[PlannedOrderLeg, ...]
    warnings: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "exchange_id": self.exchange_id,
            "strategy": self.strategy,
            "live_order_safe": self.live_order_safe,
            "entry": self.entry.to_dict(),
            "exits": [exit_leg.to_dict() for exit_leg in self.exits],
            "warnings": list(self.warnings),
            "notes": list(self.notes),
        }


def plan_bracket_execution(signal: CryptoSignal, capabilities: ExchangeCapabilities) -> BracketExecutionPlan:
    """Build a non-executing order plan for bracket and trailing intent."""
    exit_orders = build_exit_orders(signal)
    exit_side = _exit_side(signal.side)
    entry = PlannedOrderLeg(
        role="entry" if not signal.reduce_only else "reduce_only",
        side=signal.side,
        order_type="limit" if signal.price is not None else "market",
        limit_price=signal.price,
        reduce_only=signal.reduce_only,
        params=_entry_params(signal),
    )
    exits = tuple(_planned_exit(exit_order, side=exit_side, capabilities=capabilities) for exit_order in exit_orders)
    warnings = _plan_warnings(exit_orders, capabilities)

    if not exit_orders:
        strategy = "single_order"
        notes = ("No bracket exit fields were supplied.",)
    elif capabilities.exchange_id == "paper":
        strategy = "paper_synthetic_bracket"
        notes = ("Paper exchange tracks synthetic OCA exits and trailing movement without live order submission.",)
    elif _has_stop_and_take_profit(exit_orders) and _has_trailing(exit_orders):
        if capabilities.attached_stop_loss_take_profit and capabilities.trailing_order:
            strategy = "attached_bracket_with_trailing"
        else:
            strategy = "paper_required_for_mixed_bracket_trailing"
    elif _has_stop_and_take_profit(exit_orders):
        if capabilities.attached_stop_loss_take_profit:
            strategy = "attached_stop_loss_take_profit"
        elif capabilities.oco_order:
            strategy = "entry_then_oco_after_fill"
        else:
            strategy = "paper_required_for_bracket"
    elif _has_trailing(exit_orders):
        strategy = "entry_then_trailing_stop" if capabilities.trailing_order else "paper_required_for_trailing_stop"
    else:
        strategy = "entry_then_conditional_exit"

    live_order_safe = False
    notes = locals().get("notes", ())
    if strategy.startswith("paper_required"):
        notes = notes + ("Venue capabilities do not prove a portable live bracket/trailing mapping.",)
    elif strategy != "paper_synthetic_bracket" and exit_orders:
        notes = notes + ("This is a planning preview only; Auto-Crypto still does not submit live orders.",)

    return BracketExecutionPlan(
        exchange_id=capabilities.exchange_id,
        strategy=strategy,
        live_order_safe=live_order_safe,
        entry=entry,
        exits=exits,
        warnings=tuple(warnings),
        notes=tuple(notes),
    )


def _planned_exit(
    exit_order: ExitOrder,
    *,
    side: str,
    capabilities: ExchangeCapabilities,
) -> PlannedOrderLeg:
    params: dict[str, Any] = {"oca_group": exit_order.oca_group}
    if capabilities.reduce_only:
        params["reduceOnly"] = True
    if exit_order.kind == "stop_loss":
        params["stopLoss"] = {"triggerPrice": str(exit_order.trigger_price)}
        order_type = "stop"
    elif exit_order.kind == "take_profit":
        params["takeProfit"] = {"triggerPrice": str(exit_order.trigger_price)}
        order_type = "take_profit"
    elif exit_order.kind == "trailing_stop":
        params["trailing"] = {"triggerPrice": str(exit_order.trigger_price)}
        order_type = "trailing_stop"
    else:
        order_type = exit_order.kind
    return PlannedOrderLeg(
        role=exit_order.kind,
        side=side,
        order_type=order_type,
        trigger_price=exit_order.trigger_price,
        close_pct=exit_order.close_pct,
        reduce_only=True,
        params=params,
    )


def _entry_params(signal: CryptoSignal) -> dict[str, Any]:
    params: dict[str, Any] = {"signal_id": signal.signal_id, "market_type": signal.market_type}
    if signal.quote_amount is not None:
        params["quote_amount"] = str(signal.quote_amount)
    if signal.base_amount is not None:
        params["base_amount"] = str(signal.base_amount)
    if signal.risk_amount is not None:
        params["risk_amount"] = str(signal.risk_amount)
    if signal.risk_pct is not None:
        params["risk_pct"] = str(signal.risk_pct)
    return params


def _plan_warnings(exit_orders: list[ExitOrder], capabilities: ExchangeCapabilities) -> list[str]:
    warnings: list[str] = []
    if _has_trailing(exit_orders) and not capabilities.trailing_order and capabilities.exchange_id != "paper":
        warnings.append("trailing_order_not_advertised")
    if _has_stop_and_take_profit(exit_orders) and not (
        capabilities.attached_stop_loss_take_profit or capabilities.oco_order or capabilities.exchange_id == "paper"
    ):
        warnings.append("native_bracket_not_advertised")
    if exit_orders and not capabilities.create_order:
        warnings.append("create_order_not_advertised")
    return warnings


def _has_stop_and_take_profit(exit_orders: list[ExitOrder]) -> bool:
    kinds = {exit_order.kind for exit_order in exit_orders}
    return "stop_loss" in kinds and "take_profit" in kinds


def _has_trailing(exit_orders: list[ExitOrder]) -> bool:
    return any(exit_order.kind == "trailing_stop" for exit_order in exit_orders)


def _exit_side(entry_side: str) -> str:
    return "sell" if entry_side == "buy" else "buy"
