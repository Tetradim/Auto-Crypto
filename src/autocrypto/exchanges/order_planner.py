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
    intent: str
    trigger_price: Decimal | None = None
    limit_price: Decimal | None = None
    close_pct: Decimal = Decimal("100")
    reduce_only: bool = False
    activation_status: str = "open"
    partial_close: bool = False
    exchange_order_family: str = "single"
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "side": self.side,
            "order_type": self.order_type,
            "intent": self.intent,
            "trigger_price": str(self.trigger_price) if self.trigger_price is not None else None,
            "limit_price": str(self.limit_price) if self.limit_price is not None else None,
            "close_pct": str(self.close_pct),
            "reduce_only": self.reduce_only,
            "activation_status": self.activation_status,
            "partial_close": self.partial_close,
            "exchange_order_family": self.exchange_order_family,
            "params": self.params,
        }


@dataclass(frozen=True)
class BracketExecutionPlan:
    exchange_id: str
    strategy: str
    live_order_safe: bool
    entry: PlannedOrderLeg
    exits: tuple[PlannedOrderLeg, ...]
    summary: dict[str, Any] = field(default_factory=dict)
    execution_sequence: tuple[dict[str, Any], ...] = ()
    warnings: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "exchange_id": self.exchange_id,
            "strategy": self.strategy,
            "live_order_safe": self.live_order_safe,
            "entry": self.entry.to_dict(),
            "exits": [exit_leg.to_dict() for exit_leg in self.exits],
            "summary": self.summary,
            "execution_sequence": list(self.execution_sequence),
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
        intent="open_or_reduce_position",
        limit_price=signal.price,
        reduce_only=signal.reduce_only,
        params=_entry_params(signal),
    )
    exits = tuple(
        _planned_exit(exit_order, signal=signal, side=exit_side, capabilities=capabilities, exit_orders=exit_orders)
        for exit_order in exit_orders
    )
    warnings = _plan_warnings(exit_orders, capabilities)

    if not exit_orders:
        strategy = "single_order"
        notes = ("No bracket exit fields were supplied.",)
    elif capabilities.exchange_id == "paper":
        strategy = "paper_synthetic_bracket"
        notes = ("Paper exchange tracks synthetic OCA exits and trailing movement without live order submission.",)
    elif _has_non_portable_exit_shape(exit_orders):
        strategy = "paper_required_for_staged_or_partial_bracket"
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
        summary=_plan_summary(exit_orders),
        execution_sequence=tuple(_execution_sequence(strategy, signal, exit_orders, capabilities)),
        warnings=tuple(warnings),
        notes=tuple(notes),
    )


def _planned_exit(
    exit_order: ExitOrder,
    *,
    signal: CryptoSignal,
    side: str,
    capabilities: ExchangeCapabilities,
    exit_orders: list[ExitOrder],
) -> PlannedOrderLeg:
    params: dict[str, Any] = {"oca_group": exit_order.oca_group}
    if capabilities.reduce_only:
        params["reduceOnly"] = True
    if exit_order.kind == "stop_loss":
        params["stopLoss"] = {"triggerPrice": str(exit_order.trigger_price)}
        order_type = "stop"
        intent = "protective_exit"
        exchange_order_family = _exit_order_family(exit_order, exit_orders, capabilities)
    elif exit_order.kind == "take_profit":
        params["takeProfit"] = {"triggerPrice": str(exit_order.trigger_price)}
        order_type = "take_profit"
        intent = "profit_exit"
        exchange_order_family = _exit_order_family(exit_order, exit_orders, capabilities)
    elif exit_order.kind == "trailing_stop":
        params["trailing"] = _trailing_params(signal, exit_order)
        order_type = "trailing_stop"
        intent = "protective_exit"
        exchange_order_family = _exit_order_family(exit_order, exit_orders, capabilities)
    elif exit_order.kind == "time_exit":
        params["timeStop"] = {"maxHoldMarks": signal.max_hold_marks}
        order_type = "time_exit"
        intent = "staleness_exit"
        exchange_order_family = "paper_time_stop"
    else:
        order_type = exit_order.kind
        intent = "conditional_exit"
        exchange_order_family = "conditional"
    return PlannedOrderLeg(
        role=exit_order.kind,
        side=side,
        order_type=order_type,
        intent=intent,
        trigger_price=exit_order.trigger_price,
        close_pct=exit_order.close_pct,
        reduce_only=True,
        activation_status=exit_order.status,
        partial_close=exit_order.close_pct < Decimal("100"),
        exchange_order_family=exchange_order_family,
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
    if any(exit_order.kind == "trailing_stop" and exit_order.status == "pending_activation" for exit_order in exit_orders):
        warnings.append("trailing_stop_starts_pending_activation")
    if any(exit_order.kind == "trailing_stop" and exit_order.status == "pending_take_profit" for exit_order in exit_orders):
        warnings.append("trailing_stop_waits_for_take_profit")
    if _has_non_portable_exit_shape(exit_orders) and capabilities.exchange_id != "paper":
        warnings.append("staged_or_partial_exits_require_paper_or_custom_native_mapping")
    if any(exit_order.kind == "time_exit" for exit_order in exit_orders) and capabilities.exchange_id != "paper":
        warnings.append("time_stop_is_paper_only")
    return warnings


def _plan_summary(exit_orders: list[ExitOrder]) -> dict[str, Any]:
    take_profit_close_pct = sum(
        (exit_order.close_pct for exit_order in exit_orders if exit_order.kind == "take_profit"),
        Decimal("0"),
    )
    trailing_close_pct = sum(
        (exit_order.close_pct for exit_order in exit_orders if exit_order.kind == "trailing_stop"),
        Decimal("0"),
    )
    return {
        "exit_count": len(exit_orders),
        "protective_exit_count": sum(1 for exit_order in exit_orders if exit_order.kind in {"stop_loss", "trailing_stop"}),
        "take_profit_count": sum(1 for exit_order in exit_orders if exit_order.kind == "take_profit"),
        "trailing_stop_count": sum(1 for exit_order in exit_orders if exit_order.kind == "trailing_stop"),
        "pending_trailing_stop_count": sum(
            1
            for exit_order in exit_orders
            if exit_order.kind == "trailing_stop" and exit_order.status in {"pending_activation", "pending_take_profit"}
        ),
        "take_profit_gated_trailing_stop_count": sum(
            1
            for exit_order in exit_orders
            if exit_order.kind == "trailing_stop" and exit_order.status == "pending_take_profit"
        ),
        "time_exit_count": sum(1 for exit_order in exit_orders if exit_order.kind == "time_exit"),
        "take_profit_close_pct": str(take_profit_close_pct),
        "trailing_stop_close_pct": str(trailing_close_pct) if trailing_close_pct else None,
        "has_full_size_profit_exit": take_profit_close_pct >= Decimal("100"),
        "has_partial_trailing_exit": any(
            exit_order.kind == "trailing_stop" and exit_order.close_pct < Decimal("100")
            for exit_order in exit_orders
        ),
        "has_staged_take_profit": sum(1 for exit_order in exit_orders if exit_order.kind == "take_profit") > 1,
        "has_partial_take_profit": any(
            exit_order.kind == "take_profit" and exit_order.close_pct < Decimal("100")
            for exit_order in exit_orders
        ),
        "requires_custom_native_mapping": _has_non_portable_exit_shape(exit_orders),
    }


def _trailing_params(signal: CryptoSignal, exit_order: ExitOrder) -> dict[str, Any]:
    params: dict[str, Any] = {
        "triggerPrice": str(exit_order.trigger_price),
        "status": exit_order.status,
        "closePct": str(exit_order.close_pct),
    }
    if signal.trailing_stop_pct is not None:
        params["callbackPct"] = str(signal.trailing_stop_pct)
    if signal.trailing_stop_amount is not None:
        params["callbackAmount"] = str(signal.trailing_stop_amount)
    if signal.trailing_step_pct is not None:
        params["stepPct"] = str(signal.trailing_step_pct)
    if signal.trailing_step_amount is not None:
        params["stepAmount"] = str(signal.trailing_step_amount)
    if signal.trailing_activation_pct is not None:
        params["activationPct"] = str(signal.trailing_activation_pct)
    if signal.trailing_activation_price is not None:
        params["activationPrice"] = str(signal.trailing_activation_price)
    if signal.trail_after_take_profit:
        params["trailAfterTakeProfit"] = True
    return params


def _has_stop_and_take_profit(exit_orders: list[ExitOrder]) -> bool:
    kinds = {exit_order.kind for exit_order in exit_orders}
    return "stop_loss" in kinds and "take_profit" in kinds


def _has_trailing(exit_orders: list[ExitOrder]) -> bool:
    return any(exit_order.kind == "trailing_stop" for exit_order in exit_orders)


def _has_non_portable_exit_shape(exit_orders: list[ExitOrder]) -> bool:
    return (
        sum(1 for exit_order in exit_orders if exit_order.kind == "take_profit") > 1
        or any(
            exit_order.kind in {"take_profit", "trailing_stop"} and exit_order.close_pct < Decimal("100")
            for exit_order in exit_orders
        )
        or any(exit_order.kind == "time_exit" for exit_order in exit_orders)
        or any(exit_order.kind == "trailing_stop" and exit_order.status == "pending_take_profit" for exit_order in exit_orders)
    )


def _exit_order_family(
    exit_order: ExitOrder,
    exit_orders: list[ExitOrder],
    capabilities: ExchangeCapabilities,
) -> str:
    if capabilities.exchange_id == "paper":
        return "synthetic_paper_oca"
    if exit_order.kind == "time_exit":
        return "paper_time_stop"
    if _has_non_portable_exit_shape(exit_orders):
        return "paper_or_custom_native_mapping"
    if exit_order.kind in {"stop_loss", "take_profit"} and _has_stop_and_take_profit(exit_orders):
        if capabilities.attached_stop_loss_take_profit:
            return "attached_take_profit_stop_loss"
        if capabilities.oco_order:
            return "entry_then_oco"
        return "paper_or_custom_native_mapping"
    if exit_order.kind == "trailing_stop":
        return "standalone_trailing_stop" if capabilities.trailing_order else "paper_or_custom_native_mapping"
    return "standalone_conditional"


def _execution_sequence(
    strategy: str,
    signal: CryptoSignal,
    exit_orders: list[ExitOrder],
    capabilities: ExchangeCapabilities,
) -> list[dict[str, Any]]:
    sequence: list[dict[str, Any]] = [
        {
            "step": "submit_entry",
            "side": signal.side,
            "order_type": "limit" if signal.price is not None else "market",
            "live_submission_enabled": False,
        }
    ]
    if not exit_orders:
        return sequence
    sequence.append({"step": "wait_for_entry_fill", "required_before": "protective_exits"})
    if capabilities.exchange_id == "paper" or strategy.startswith("paper_required"):
        sequence.append(
            {
                "step": "track_synthetic_exits",
                "mode": "paper",
                "exit_count": len(exit_orders),
                "live_submission_enabled": False,
            }
        )
        return sequence
    if strategy == "attached_stop_loss_take_profit":
        sequence.append({"step": "attach_stop_loss_take_profit", "venue_feature": "attached_tp_sl"})
    elif strategy == "entry_then_oco_after_fill":
        sequence.append({"step": "place_oco_after_fill", "venue_feature": "oco"})
    elif strategy == "attached_bracket_with_trailing":
        sequence.append({"step": "attach_stop_loss_take_profit", "venue_feature": "attached_tp_sl"})
        sequence.append({"step": "place_or_track_trailing_stop", "venue_feature": "trailing_order"})
    elif strategy == "entry_then_trailing_stop":
        sequence.append({"step": "place_or_track_trailing_stop", "venue_feature": "trailing_order"})
    else:
        sequence.append({"step": "place_conditional_exit_after_fill", "venue_feature": "conditional_order"})
    for step in sequence:
        step.setdefault("live_submission_enabled", False)
    return sequence


def _exit_side(entry_side: str) -> str:
    return "sell" if entry_side == "buy" else "buy"
