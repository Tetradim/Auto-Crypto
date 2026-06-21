from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP

from .risk import RiskDecision
from .signals import CryptoSignal


MONEY = Decimal("0.01")


@dataclass(frozen=True)
class ExitOrder:
    kind: str
    trigger_price: Decimal
    close_pct: Decimal = Decimal("100")
    oca_group: str | None = None
    status: str = "open"


@dataclass(frozen=True)
class ExecutionCostConfig:
    fee_bps: Decimal = Decimal("0")
    slippage_bps: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if self.fee_bps < 0:
            raise ValueError("fee_bps must be non-negative")
        if self.slippage_bps < 0:
            raise ValueError("slippage_bps must be non-negative")


@dataclass
class PaperLot:
    signal_id: str
    symbol: str
    direction: str
    original_quantity: Decimal
    remaining_quantity: Decimal
    entry_price: Decimal
    exit_orders: list[ExitOrder] = field(default_factory=list)
    trailing_stop_pct: Decimal | None = None
    trailing_stop_amount: Decimal | None = None
    trailing_stop_price: Decimal | None = None
    trailing_step_pct: Decimal | None = None
    trailing_step_amount: Decimal | None = None
    trailing_activation_pct: Decimal | None = None
    trailing_activation_price: Decimal | None = None
    trail_after_take_profit: bool = False
    trailing_activated: bool = True
    take_profit_filled: bool = False
    high_water_mark: Decimal | None = None
    low_water_mark: Decimal | None = None
    breakeven_trigger_pct: Decimal | None = None
    breakeven_after_take_profit: bool = False
    breakeven_applied: bool = False
    max_hold_marks: int | None = None
    marks_seen: int = 0
    entry_fee_remaining: Decimal = Decimal("0")


@dataclass
class PaperPosition:
    symbol: str
    quantity: Decimal = Decimal("0")
    avg_entry: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    fees_paid: Decimal = Decimal("0")

    def buy(self, quantity: Decimal, price: Decimal) -> None:
        total_quantity = self.quantity + quantity
        if total_quantity <= 0:
            self.quantity = Decimal("0")
            self.avg_entry = Decimal("0")
            return
        self.avg_entry = ((self.avg_entry * self.quantity) + (price * quantity)) / total_quantity
        self.quantity = total_quantity

    def sell(self, quantity: Decimal, price: Decimal) -> None:
        if self.quantity <= 0:
            return
        sell_quantity = min(quantity, self.quantity)
        self.realized_pnl += (price - self.avg_entry) * sell_quantity
        self.quantity -= sell_quantity
        if self.quantity <= 0:
            self.quantity = Decimal("0")
            self.avg_entry = Decimal("0")

    def sell_short(self, quantity: Decimal, price: Decimal) -> None:
        current_short = abs(self.quantity) if self.quantity < 0 else Decimal("0")
        total_quantity = current_short + quantity
        if total_quantity <= 0:
            self.quantity = Decimal("0")
            self.avg_entry = Decimal("0")
            return
        self.avg_entry = ((self.avg_entry * current_short) + (price * quantity)) / total_quantity
        self.quantity = -total_quantity

    def to_dict(self) -> dict:
        payload = {
            "symbol": self.symbol,
            "quantity": _fixed8(self.quantity),
            "avg_entry": _fixed8(self.avg_entry),
            "realized_pnl": _fixed8(self.realized_pnl),
        }
        if self.fees_paid != 0:
            payload["fees_paid"] = _fixed8(self.fees_paid)
        return payload


@dataclass(frozen=True)
class PaperOrder:
    order_id: str
    signal_id: str
    mode: str
    exchange: str
    symbol: str
    side: str
    notional: Decimal
    price: Decimal | None
    exit_orders: list[ExitOrder] = field(default_factory=list)
    trailing_stop_pct: Decimal | None = None
    trailing_stop_amount: Decimal | None = None
    trailing_stop_price: Decimal | None = None
    trailing_step_pct: Decimal | None = None
    trailing_step_amount: Decimal | None = None
    trailing_activation_pct: Decimal | None = None
    trailing_activation_price: Decimal | None = None
    trail_after_take_profit: bool = False
    breakeven_trigger_pct: Decimal | None = None
    breakeven_after_take_profit: bool = False
    max_hold_marks: int | None = None
    exit_kind: str | None = None
    amend_target_index: int | None = None
    canceled_exit_orders: list[ExitOrder] = field(default_factory=list)
    reduce_only: bool = False
    fee: Decimal = Decimal("0")
    fee_bps: Decimal = Decimal("0")
    slippage_bps: Decimal = Decimal("0")
    status: str = "accepted"

    def to_dict(self) -> dict:
        return {
            "order_id": self.order_id,
            "signal_id": self.signal_id,
            "mode": self.mode,
            "exchange": self.exchange,
            "symbol": self.symbol,
            "side": self.side,
            "notional": str(self.notional),
            "price": str(self.price) if self.price is not None else None,
            "status": self.status,
            "exit_orders": [
                {
                    "kind": exit_order.kind,
                    "trigger_price": str(exit_order.trigger_price),
                    "close_pct": str(exit_order.close_pct),
                    "oca_group": exit_order.oca_group,
                    "status": exit_order.status,
                }
                for exit_order in self.exit_orders
            ],
            "trailing_stop_pct": str(self.trailing_stop_pct) if self.trailing_stop_pct is not None else None,
            "trailing_stop_amount": str(self.trailing_stop_amount) if self.trailing_stop_amount is not None else None,
            "trailing_stop_price": str(self.trailing_stop_price) if self.trailing_stop_price is not None else None,
            "trailing_step_pct": str(self.trailing_step_pct) if self.trailing_step_pct is not None else None,
            "trailing_step_amount": str(self.trailing_step_amount) if self.trailing_step_amount is not None else None,
            "trailing_activation_pct": str(self.trailing_activation_pct)
            if self.trailing_activation_pct is not None
            else None,
            "trailing_activation_price": str(self.trailing_activation_price)
            if self.trailing_activation_price is not None
            else None,
            "trail_after_take_profit": self.trail_after_take_profit,
            "breakeven_trigger_pct": str(self.breakeven_trigger_pct)
            if self.breakeven_trigger_pct is not None
            else None,
            "breakeven_after_take_profit": self.breakeven_after_take_profit,
            "max_hold_marks": self.max_hold_marks,
            "exit_kind": self.exit_kind,
            "amend_target_index": self.amend_target_index,
            "canceled_exit_orders": [
                {
                    "kind": exit_order.kind,
                    "trigger_price": str(exit_order.trigger_price),
                    "close_pct": str(exit_order.close_pct),
                    "oca_group": exit_order.oca_group,
                    "status": exit_order.status,
                }
                for exit_order in self.canceled_exit_orders
            ],
            "reduce_only": self.reduce_only,
            "fee": str(self.fee),
            "fee_bps": str(self.fee_bps),
            "slippage_bps": str(self.slippage_bps),
        }


@dataclass(frozen=True)
class ExecutionResult:
    status: str
    decision: RiskDecision
    order: PaperOrder | None = None
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "reason": self.reason,
            "risk": {
                "approved": self.decision.approved,
                "reason_codes": self.decision.reason_codes,
                "order_notional": str(self.decision.order_notional)
                if self.decision.order_notional is not None
                else None,
            },
            "order": self.order.to_dict() if self.order else None,
        }


class PaperExchange:
    """Paper exchange that records accepted orders without touching live venues."""

    def __init__(self, *, costs: ExecutionCostConfig | None = None) -> None:
        self.orders: list[PaperOrder] = []
        self.positions: dict[str, PaperPosition] = {}
        self.lots: list[PaperLot] = []
        self.active_exits: dict[str, list[ExitOrder]] = {}
        self.costs = costs or ExecutionCostConfig()

    @classmethod
    def from_order_history(cls, orders: list[dict]) -> PaperExchange:
        exchange = cls()
        for payload in orders:
            exchange._replay_order(_paper_order_from_dict(payload))
        return exchange

    def submit(self, signal: CryptoSignal, decision: RiskDecision) -> PaperOrder:
        if decision.order_notional is None:
            raise ValueError("approved order requires notional")
        exit_orders = build_exit_orders(signal)
        quantity: Decimal | None = None
        fill_price = self._fill_price(signal.side, signal.price) if signal.price is not None else None
        if fill_price is not None:
            quantity = self._fill_quantity(signal, decision.order_notional, fill_price)
        fee = self._fill_fee(quantity or Decimal("0"), fill_price)
        order = PaperOrder(
            order_id=f"paper-{signal.signal_id}",
            signal_id=signal.signal_id,
            mode="paper",
            exchange=signal.exchange,
            symbol=signal.symbol,
            side=signal.side,
            notional=decision.order_notional,
            price=fill_price,
            exit_orders=exit_orders,
            trailing_stop_pct=signal.trailing_stop_pct,
            trailing_stop_amount=signal.trailing_stop_amount,
            trailing_stop_price=signal.trailing_stop_price,
            trailing_step_pct=signal.trailing_step_pct,
            trailing_step_amount=signal.trailing_step_amount,
            trailing_activation_pct=signal.trailing_activation_pct,
            trailing_activation_price=signal.trailing_activation_price,
            trail_after_take_profit=signal.trail_after_take_profit,
            breakeven_trigger_pct=signal.breakeven_trigger_pct,
            breakeven_after_take_profit=signal.breakeven_after_take_profit,
            max_hold_marks=signal.max_hold_marks,
            reduce_only=signal.reduce_only,
            fee=fee,
            fee_bps=self.costs.fee_bps,
            slippage_bps=self.costs.slippage_bps,
        )
        self.orders.append(order)
        if quantity is not None and fill_price is not None:
            self._apply_fill(signal, quantity, fill_price, fee, exit_orders)
        self._refresh_active_exits(signal.symbol)
        return order

    def list_positions(self) -> list[dict]:
        return [
            position.to_dict()
            for position in self.positions.values()
            if position.quantity != 0 or position.realized_pnl != 0
        ]

    def open_notional(self) -> Decimal:
        return sum(
            (abs(position.quantity) * position.avg_entry for position in self.positions.values()),
            Decimal("0"),
        )

    def symbol_open_notional(self, symbol: str) -> Decimal:
        position = self.positions.get(symbol)
        if position is None:
            return Decimal("0")
        return abs(position.quantity) * position.avg_entry

    def update_price(self, symbol: str, price: Decimal) -> list[dict]:
        position = self.positions.get(symbol)
        if position is None or position.quantity == 0:
            return []

        triggered: list[dict] = []
        for lot in list(self.lots):
            if lot.symbol != symbol or lot.remaining_quantity <= 0:
                continue
            self._apply_breakeven(lot, price)
            self._update_trailing_stop(lot, price)
            lot.marks_seen += 1
            while lot.remaining_quantity > 0:
                exit_order = self._triggered_exit(lot, price)
                if exit_order is None:
                    break

                exit_quantity = self._exit_quantity(lot, exit_order)
                exit_side = "sell" if lot.direction == "long" else "buy"
                fill_price = self._fill_price(exit_side, price)
                exit_fee = self._fill_fee(exit_quantity, fill_price)
                notional = exit_quantity * fill_price
                self._close_lot(lot, fill_price, exit_quantity, exit_fee)
                if exit_order.kind == "take_profit" and lot.remaining_quantity > 0:
                    lot.exit_orders = [order for order in lot.exit_orders if order is not exit_order]
                    lot.take_profit_filled = True
                    self._release_trailing_after_take_profit(lot, price)
                    breakeven_amended = self._apply_take_profit_breakeven(lot)
                else:
                    breakeven_amended = False
                if exit_order.kind == "trailing_stop" and lot.remaining_quantity > 0:
                    lot.exit_orders = [order for order in lot.exit_orders if order is not exit_order]
                canceled_exit_orders = self._canceled_sibling_exits(lot, exit_order)
                if lot.remaining_quantity <= 0:
                    lot.exit_orders = []
                order_number = len(self.orders) + 1
                order = PaperOrder(
                    order_id=f"paper-exit-{_order_fragment(lot.signal_id)}-{order_number}",
                    signal_id=f"exit-{_order_fragment(lot.signal_id)}-{order_number}",
                    mode="paper",
                    exchange="paper",
                    symbol=symbol,
                    side=exit_side,
                    notional=notional,
                    price=fill_price,
                    exit_orders=[_filled_exit(exit_order)],
                    exit_kind=exit_order.kind,
                    canceled_exit_orders=canceled_exit_orders,
                    reduce_only=True,
                    fee=exit_fee,
                    fee_bps=self.costs.fee_bps,
                    slippage_bps=self.costs.slippage_bps,
                )
                self.orders.append(order)
                trigger_payload = {
                    "symbol": symbol,
                    "kind": exit_order.kind,
                    "price": _fixed8(fill_price),
                    "quantity": _fixed8(exit_quantity),
                }
                if self.costs.fee_bps > 0 or self.costs.slippage_bps > 0:
                    trigger_payload["mark_price"] = _fixed8(price)
                    trigger_payload["fee"] = _fixed8(exit_fee)
                if breakeven_amended:
                    trigger_payload["breakeven_after_take_profit"] = "true"
                triggered.append(trigger_payload)
                if exit_order.kind != "take_profit":
                    break

        self.lots = [lot for lot in self.lots if lot.remaining_quantity > 0]
        self._refresh_active_exits(symbol)
        return triggered

    def preview_price(self, symbol: str, price: Decimal) -> list[dict]:
        """Return paper exits that would trigger at price without mutating state."""
        return deepcopy(self).update_price(symbol, price)

    def preview_price_exchange(self, symbol: str, price: Decimal) -> PaperExchange:
        """Return a sandbox exchange after applying a hypothetical mark."""
        sandbox = deepcopy(self)
        sandbox.update_price(symbol, price)
        return sandbox

    def preview_bracket(self, signal_id: str, price: Decimal) -> list[dict]:
        """Return exits that would trigger for one paper bracket without mutating state."""
        sandbox = deepcopy(self)
        target_lots = _target_preview_lots(sandbox, signal_id)
        if not target_lots:
            return []
        symbol = target_lots[0].symbol
        sandbox.lots = _preview_lots_for_signal(sandbox, signal_id, symbol)
        return sandbox.update_price(symbol, price)

    def preview_bracket_exchange(self, signal_id: str, price: Decimal) -> PaperExchange | None:
        """Return a sandbox exchange containing only one bracket after a hypothetical mark."""
        sandbox = deepcopy(self)
        target_lots = _target_preview_lots(sandbox, signal_id)
        if not target_lots:
            return None
        symbol = target_lots[0].symbol
        sandbox.lots = _preview_lots_for_signal(sandbox, signal_id, symbol)
        sandbox.update_price(symbol, price)
        return sandbox

    def cancel_bracket(self, signal_id: str, *, reason: str = "") -> PaperOrder | None:
        """Cancel open synthetic bracket exits for a paper lot without closing exposure."""
        target_lots = [
            lot
            for lot in self.lots
            if lot.signal_id == signal_id and lot.remaining_quantity > 0 and lot.exit_orders
        ]
        if not target_lots:
            return None

        canceled: list[ExitOrder] = []
        for lot in target_lots:
            canceled.extend(
                ExitOrder(
                    kind=exit_order.kind,
                    trigger_price=exit_order.trigger_price,
                    close_pct=exit_order.close_pct,
                    oca_group=exit_order.oca_group,
                    status="canceled",
                )
                for exit_order in lot.exit_orders
                if exit_order.status not in {"canceled", "filled"}
            )
            lot.exit_orders = []

        first_lot = target_lots[0]
        order = PaperOrder(
            order_id=f"paper-cancel-{_order_fragment(signal_id)}-{len(self.orders) + 1}",
            signal_id=signal_id,
            mode="paper",
            exchange="paper",
            symbol=first_lot.symbol,
            side="cancel",
            notional=Decimal("0"),
            price=None,
            exit_kind="bracket_cancel",
            canceled_exit_orders=canceled,
            status="canceled",
        )
        self.orders.append(order)
        self._refresh_active_exits(first_lot.symbol)
        return order

    def close_bracket(
        self,
        signal_id: str,
        price: Decimal,
        *,
        close_pct: Decimal | None = None,
        base_amount: Decimal | None = None,
        reason: str = "",
    ) -> PaperOrder | None:
        """Close or reduce a paper bracket lot at the supplied mark."""
        target_lots = [
            lot
            for lot in self.lots
            if lot.signal_id == signal_id and lot.remaining_quantity > 0 and lot.exit_orders
        ]
        if not target_lots:
            return None

        first_lot = target_lots[0]
        if any(lot.symbol != first_lot.symbol or lot.direction != first_lot.direction for lot in target_lots):
            return None

        exit_side = "sell" if first_lot.direction == "long" else "buy"
        fill_price = self._fill_price(exit_side, price)
        if fill_price is None:
            return None
        total_remaining = sum((lot.remaining_quantity for lot in target_lots), Decimal("0"))
        close_quantity_target = _bracket_close_quantity(
            total_remaining,
            close_pct=close_pct,
            base_amount=base_amount,
        )
        if close_quantity_target is None:
            return None

        closed_quantity = Decimal("0")
        exit_fee_total = Decimal("0")
        oca_group = first_lot.exit_orders[0].oca_group if first_lot.exit_orders else None
        canceled_exit_orders: list[ExitOrder] = []
        for lot in target_lots:
            remaining_to_close = close_quantity_target - closed_quantity
            if remaining_to_close <= 0:
                break
            exit_quantity = min(lot.remaining_quantity, remaining_to_close)
            exit_fee = self._fill_fee(exit_quantity, fill_price)
            closed_quantity += exit_quantity
            exit_fee_total += exit_fee
            manual_exit = ExitOrder(
                kind="manual_close",
                trigger_price=_money(fill_price),
                close_pct=Decimal("100"),
                oca_group=lot.exit_orders[0].oca_group if lot.exit_orders else None,
            )
            self._close_lot(lot, fill_price, exit_quantity, exit_fee)
            canceled_exit_orders.extend(self._canceled_sibling_exits(lot, manual_exit))
            if lot.remaining_quantity <= 0:
                lot.exit_orders = []

        self.lots = [lot for lot in self.lots if lot.remaining_quantity > 0]
        exit_kind = "bracket_manual_close" if closed_quantity >= total_remaining else "bracket_manual_reduce"
        order = PaperOrder(
            order_id=f"paper-close-{_order_fragment(signal_id)}-{len(self.orders) + 1}",
            signal_id=signal_id,
            mode="paper",
            exchange="paper",
            symbol=first_lot.symbol,
            side=exit_side,
            notional=closed_quantity * fill_price,
            price=fill_price,
            exit_orders=[
                ExitOrder(
                    kind="manual_close",
                    trigger_price=_money(fill_price),
                    close_pct=Decimal("100"),
                    oca_group=oca_group,
                    status="filled",
                )
            ],
            exit_kind=exit_kind,
            canceled_exit_orders=canceled_exit_orders,
            reduce_only=True,
            fee=exit_fee_total,
            fee_bps=self.costs.fee_bps,
            slippage_bps=self.costs.slippage_bps,
        )
        self.orders.append(order)
        self._refresh_active_exits(first_lot.symbol)
        return order

    def close_bracket_at_protective_exit(
        self,
        signal_id: str,
        *,
        close_pct: Decimal | None = None,
        base_amount: Decimal | None = None,
        reason: str = "",
    ) -> PaperOrder | None:
        """Close or reduce a bracket at its current nearest paper protective trigger."""
        target_lots = [
            lot
            for lot in self.lots
            if lot.signal_id == signal_id and lot.remaining_quantity > 0 and lot.exit_orders
        ]
        if not target_lots:
            return None
        protective_exit = _nearest_protective_exit(target_lots[0])
        if protective_exit is None:
            return None
        return self.close_bracket(
            signal_id,
            protective_exit.trigger_price,
            close_pct=close_pct,
            base_amount=base_amount,
            reason=reason,
        )

    def amend_bracket_stop(self, signal_id: str, trigger_price: Decimal, *, reason: str = "") -> PaperOrder | None:
        """Move a paper bracket stop in the protective direction only."""
        target_lots = [
            lot
            for lot in self.lots
            if lot.signal_id == signal_id and lot.remaining_quantity > 0 and lot.exit_orders
        ]
        if not target_lots:
            return None

        amended_stops: list[ExitOrder] = []
        for lot in target_lots:
            amended_stop = _protective_stop_amendment(lot, _money(trigger_price))
            if amended_stop is None:
                return None
            lot.exit_orders = _replace_or_append_stop(lot.exit_orders, amended_stop)
            amended_stops.append(amended_stop)

        first_lot = target_lots[0]
        order = PaperOrder(
            order_id=f"paper-amend-{_order_fragment(signal_id)}-{len(self.orders) + 1}",
            signal_id=signal_id,
            mode="paper",
            exchange="paper",
            symbol=first_lot.symbol,
            side="amend",
            notional=Decimal("0"),
            price=_money(trigger_price),
            exit_orders=amended_stops,
            exit_kind="bracket_stop_amend",
            status="amended",
        )
        self.orders.append(order)
        self._refresh_active_exits(first_lot.symbol)
        return order

    def amend_bracket_trailing_stop(
        self,
        signal_id: str,
        trigger_price: Decimal,
        *,
        reason: str = "",
    ) -> PaperOrder | None:
        """Move a paper trailing stop trigger in the protective direction only."""
        target_lots = [
            lot
            for lot in self.lots
            if lot.signal_id == signal_id and lot.remaining_quantity > 0 and lot.exit_orders
        ]
        if not target_lots:
            return None

        amended_trails: list[ExitOrder] = []
        for lot in target_lots:
            amended_trail = _protective_trailing_amendment(lot, _money(trigger_price))
            if amended_trail is None:
                return None
            lot.exit_orders = _replace_or_append_trailing_stop(lot.exit_orders, amended_trail)
            _sync_trailing_water_mark(lot, amended_trail.trigger_price)
            amended_trails.append(amended_trail)

        first_lot = target_lots[0]
        order = PaperOrder(
            order_id=f"paper-amend-trail-{_order_fragment(signal_id)}-{len(self.orders) + 1}",
            signal_id=signal_id,
            mode="paper",
            exchange="paper",
            symbol=first_lot.symbol,
            side="amend",
            notional=Decimal("0"),
            price=_money(trigger_price),
            exit_orders=amended_trails,
            exit_kind="bracket_trailing_stop_amend",
            status="amended",
        )
        self.orders.append(order)
        self._refresh_active_exits(first_lot.symbol)
        return order

    def tighten_bracket_trailing_stop_to_mark(
        self,
        signal_id: str,
        mark_price: Decimal,
        *,
        reason: str = "",
    ) -> PaperOrder | None:
        """Tighten a paper trailing stop by deriving its next trigger from a favorable mark."""
        target_lots = [
            lot
            for lot in self.lots
            if lot.signal_id == signal_id and lot.remaining_quantity > 0 and lot.exit_orders
        ]
        if not target_lots:
            return None

        amended_trails: list[ExitOrder] = []
        for lot in target_lots:
            trigger_price = _candidate_trailing_trigger(lot, _money(mark_price))
            if trigger_price is None:
                return None
            current_trigger = _current_trailing_trigger(lot)
            if current_trigger is not None and not _trailing_step_reached(lot, current_trigger, trigger_price):
                return None
            amended_trail = _protective_trailing_amendment(lot, trigger_price)
            if amended_trail is None:
                return None
            lot.exit_orders = _replace_or_append_trailing_stop(lot.exit_orders, amended_trail)
            if lot.direction == "long":
                lot.high_water_mark = max(lot.high_water_mark or lot.entry_price, _money(mark_price))
            else:
                lot.low_water_mark = min(lot.low_water_mark or lot.entry_price, _money(mark_price))
            _sync_trailing_water_mark(lot, amended_trail.trigger_price)
            amended_trails.append(amended_trail)

        first_lot = target_lots[0]
        order = PaperOrder(
            order_id=f"paper-amend-trail-mark-{_order_fragment(signal_id)}-{len(self.orders) + 1}",
            signal_id=signal_id,
            mode="paper",
            exchange="paper",
            symbol=first_lot.symbol,
            side="amend",
            notional=Decimal("0"),
            price=_money(mark_price),
            exit_orders=amended_trails,
            exit_kind="bracket_trailing_stop_mark_amend",
            status="amended",
        )
        self.orders.append(order)
        self._refresh_active_exits(first_lot.symbol)
        return order

    def amend_bracket_take_profit(
        self,
        signal_id: str,
        trigger_price: Decimal,
        *,
        target_index: int = 0,
        reason: str = "",
    ) -> PaperOrder | None:
        """Move a paper take-profit target farther into profit only."""
        target_lots = [
            lot
            for lot in self.lots
            if lot.signal_id == signal_id and lot.remaining_quantity > 0 and lot.exit_orders
        ]
        if not target_lots:
            return None

        amended_targets: list[ExitOrder] = []
        for lot in target_lots:
            amended_target = _take_profit_amendment(lot, _money(trigger_price), target_index=target_index)
            if amended_target is None:
                return None
            lot.exit_orders = _replace_take_profit(lot.exit_orders, amended_target, target_index=target_index)
            amended_targets.append(amended_target)

        first_lot = target_lots[0]
        order = PaperOrder(
            order_id=f"paper-amend-tp-{_order_fragment(signal_id)}-{len(self.orders) + 1}",
            signal_id=signal_id,
            mode="paper",
            exchange="paper",
            symbol=first_lot.symbol,
            side="amend",
            notional=Decimal("0"),
            price=_money(trigger_price),
            exit_orders=amended_targets,
            exit_kind="bracket_take_profit_amend",
            amend_target_index=target_index,
            status="amended",
        )
        self.orders.append(order)
        self._refresh_active_exits(first_lot.symbol)
        return order

    def move_bracket_to_breakeven(self, signal_id: str, *, reason: str = "") -> PaperOrder | None:
        """Move open protective paper exits to entry without loosening risk."""
        target_lots = [
            lot
            for lot in self.lots
            if lot.signal_id == signal_id and lot.remaining_quantity > 0 and lot.exit_orders
        ]
        if not target_lots:
            return None

        amended_exits: list[ExitOrder] = []
        for lot in target_lots:
            updated_exit_orders, lot_amendments = _breakeven_exit_amendments(lot)
            if not lot_amendments:
                continue
            lot.exit_orders = updated_exit_orders
            lot.breakeven_applied = True
            for amendment in lot_amendments:
                if amendment.kind == "trailing_stop":
                    _sync_trailing_water_mark(lot, amendment.trigger_price)
            amended_exits.extend(lot_amendments)

        if not amended_exits:
            return None

        first_lot = target_lots[0]
        order = PaperOrder(
            order_id=f"paper-breakeven-{_order_fragment(signal_id)}-{len(self.orders) + 1}",
            signal_id=signal_id,
            mode="paper",
            exchange="paper",
            symbol=first_lot.symbol,
            side="amend",
            notional=Decimal("0"),
            price=_money(first_lot.entry_price),
            exit_orders=amended_exits,
            exit_kind="bracket_breakeven",
            status="amended",
        )
        self.orders.append(order)
        self._refresh_active_exits(first_lot.symbol)
        return order

    def lock_bracket_profit(
        self,
        signal_id: str,
        lock_profit_pct: Decimal,
        *,
        reason: str = "",
    ) -> PaperOrder | None:
        """Move open protective paper exits beyond entry without loosening risk."""
        if lock_profit_pct <= 0:
            return None
        target_lots = [
            lot
            for lot in self.lots
            if lot.signal_id == signal_id and lot.remaining_quantity > 0 and lot.exit_orders
        ]
        if not target_lots:
            return None

        amended_exits: list[ExitOrder] = []
        for lot in target_lots:
            updated_exit_orders, lot_amendments = _profit_lock_exit_amendments(lot, lock_profit_pct)
            if not lot_amendments:
                continue
            lot.exit_orders = updated_exit_orders
            for amendment in lot_amendments:
                if amendment.kind == "trailing_stop":
                    _sync_trailing_water_mark(lot, amendment.trigger_price)
            amended_exits.extend(lot_amendments)

        if not amended_exits:
            return None

        first_lot = target_lots[0]
        lock_price = _profit_lock_price(first_lot, lock_profit_pct)
        order = PaperOrder(
            order_id=f"paper-lock-profit-{_order_fragment(signal_id)}-{len(self.orders) + 1}",
            signal_id=signal_id,
            mode="paper",
            exchange="paper",
            symbol=first_lot.symbol,
            side="amend",
            notional=Decimal("0"),
            price=lock_price,
            exit_orders=amended_exits,
            exit_kind="bracket_profit_lock",
            status="amended",
        )
        self.orders.append(order)
        self._refresh_active_exits(first_lot.symbol)
        return order

    def _fill_quantity(self, signal: CryptoSignal, notional: Decimal, fill_price: Decimal) -> Decimal:
        return signal.base_amount if signal.base_amount is not None else notional / fill_price

    def _fill_price(self, side: str, mark_price: Decimal | None) -> Decimal | None:
        if mark_price is None:
            return None
        if self.costs.slippage_bps == 0:
            return mark_price
        direction = Decimal("1") if side == "buy" else Decimal("-1")
        return _money(mark_price * (Decimal("1") + direction * self.costs.slippage_bps / Decimal("10000")))

    def _fill_fee(self, quantity: Decimal, fill_price: Decimal | None) -> Decimal:
        if fill_price is None or self.costs.fee_bps == 0:
            return Decimal("0")
        return _money(abs(quantity * fill_price) * self.costs.fee_bps / Decimal("10000"))

    def _apply_fill(
        self,
        signal: CryptoSignal,
        quantity: Decimal,
        fill_price: Decimal,
        fee: Decimal,
        exit_orders: list[ExitOrder],
    ) -> None:
        position = self.positions.setdefault(signal.symbol, PaperPosition(symbol=signal.symbol))
        if signal.side == "buy" and signal.reduce_only:
            self._buy_quantity(signal.symbol, quantity, fill_price, fee)
        elif signal.side == "buy":
            position.fees_paid += fee
            position.buy(quantity, fill_price)
            self.lots.append(
                PaperLot(
                    signal_id=signal.signal_id,
                    symbol=signal.symbol,
                    direction="long",
                    original_quantity=quantity,
                    remaining_quantity=quantity,
                    entry_price=fill_price,
                    exit_orders=exit_orders,
                    trailing_stop_pct=signal.trailing_stop_pct,
                    trailing_stop_amount=signal.trailing_stop_amount,
                    trailing_stop_price=signal.trailing_stop_price,
                    trailing_step_pct=signal.trailing_step_pct,
                    trailing_step_amount=signal.trailing_step_amount,
                    trailing_activation_pct=signal.trailing_activation_pct,
                    trailing_activation_price=signal.trailing_activation_price,
                    trail_after_take_profit=signal.trail_after_take_profit,
                    trailing_activated=_trailing_starts_activated(
                        signal.trailing_activation_pct,
                        signal.trailing_activation_price,
                        signal.trail_after_take_profit,
                    ),
                    high_water_mark=signal.price
                    if _has_trailing_distance(signal.trailing_stop_pct, signal.trailing_stop_amount)
                    and _trailing_starts_activated(
                        signal.trailing_activation_pct,
                        signal.trailing_activation_price,
                        signal.trail_after_take_profit,
                    )
                    else None,
                    breakeven_trigger_pct=signal.breakeven_trigger_pct,
                    breakeven_after_take_profit=signal.breakeven_after_take_profit,
                    max_hold_marks=signal.max_hold_marks,
                    entry_fee_remaining=fee,
                )
            )
        elif signal.side == "sell" and signal.reduce_only:
            self._sell_quantity(signal.symbol, quantity, fill_price, fee)
        elif signal.side == "sell" and exit_orders:
            position.fees_paid += fee
            position.sell_short(quantity, fill_price)
            self.lots.append(
                PaperLot(
                    signal_id=signal.signal_id,
                    symbol=signal.symbol,
                    direction="short",
                    original_quantity=quantity,
                    remaining_quantity=quantity,
                    entry_price=fill_price,
                    exit_orders=exit_orders,
                    trailing_stop_pct=signal.trailing_stop_pct,
                    trailing_stop_amount=signal.trailing_stop_amount,
                    trailing_stop_price=signal.trailing_stop_price,
                    trailing_step_pct=signal.trailing_step_pct,
                    trailing_step_amount=signal.trailing_step_amount,
                    trailing_activation_pct=signal.trailing_activation_pct,
                    trailing_activation_price=signal.trailing_activation_price,
                    trail_after_take_profit=signal.trail_after_take_profit,
                    trailing_activated=_trailing_starts_activated(
                        signal.trailing_activation_pct,
                        signal.trailing_activation_price,
                        signal.trail_after_take_profit,
                    ),
                    low_water_mark=signal.price
                    if _has_trailing_distance(signal.trailing_stop_pct, signal.trailing_stop_amount)
                    and _trailing_starts_activated(
                        signal.trailing_activation_pct,
                        signal.trailing_activation_price,
                        signal.trail_after_take_profit,
                    )
                    else None,
                    breakeven_trigger_pct=signal.breakeven_trigger_pct,
                    breakeven_after_take_profit=signal.breakeven_after_take_profit,
                    max_hold_marks=signal.max_hold_marks,
                    entry_fee_remaining=fee,
                )
            )
        elif signal.side == "sell":
            self._sell_quantity(signal.symbol, quantity, fill_price, fee)

    def _replay_order(self, order: PaperOrder) -> None:
        self.orders.append(order)
        if order.exit_kind == "bracket_cancel":
            self._replay_bracket_cancel(order)
            return
        if order.exit_kind == "bracket_stop_amend":
            self._replay_bracket_stop_amend(order)
            return
        if order.exit_kind in {"bracket_trailing_stop_amend", "bracket_trailing_stop_mark_amend"}:
            self._replay_bracket_trailing_stop_amend(order)
            return
        if order.exit_kind == "bracket_take_profit_amend":
            self._replay_bracket_take_profit_amend(order)
            return
        if order.exit_kind == "bracket_breakeven":
            self._replay_bracket_breakeven(order)
            return
        if order.exit_kind == "bracket_profit_lock":
            self._replay_bracket_profit_lock(order)
            return
        if order.price is None:
            return
        quantity = order.notional / order.price
        position = self.positions.setdefault(order.symbol, PaperPosition(symbol=order.symbol))
        if order.side == "buy" and order.reduce_only:
            self._buy_quantity(order.symbol, quantity, order.price, order.fee)
        elif order.side == "buy":
            position.fees_paid += order.fee
            position.buy(quantity, order.price)
            self.lots.append(
                PaperLot(
                    signal_id=order.signal_id,
                    symbol=order.symbol,
                    direction="long",
                    original_quantity=quantity,
                    remaining_quantity=quantity,
                    entry_price=order.price,
                    exit_orders=order.exit_orders,
                    trailing_stop_pct=order.trailing_stop_pct,
                    trailing_stop_amount=order.trailing_stop_amount,
                    trailing_stop_price=order.trailing_stop_price,
                    trailing_step_pct=order.trailing_step_pct,
                    trailing_step_amount=order.trailing_step_amount,
                    trailing_activation_pct=order.trailing_activation_pct,
                    trailing_activation_price=order.trailing_activation_price,
                    trail_after_take_profit=order.trail_after_take_profit,
                    trailing_activated=_trailing_starts_activated(
                        order.trailing_activation_pct,
                        order.trailing_activation_price,
                        order.trail_after_take_profit,
                    ),
                    high_water_mark=order.price
                    if _has_trailing_distance(order.trailing_stop_pct, order.trailing_stop_amount)
                    and _trailing_starts_activated(
                        order.trailing_activation_pct,
                        order.trailing_activation_price,
                        order.trail_after_take_profit,
                    )
                    else None,
                    breakeven_trigger_pct=order.breakeven_trigger_pct,
                    breakeven_after_take_profit=order.breakeven_after_take_profit,
                    max_hold_marks=order.max_hold_marks,
                    entry_fee_remaining=order.fee,
                )
            )
        elif order.side == "sell" and order.reduce_only:
            self._sell_quantity(order.symbol, quantity, order.price, order.fee)
        elif order.side == "sell" and order.exit_orders:
            position.fees_paid += order.fee
            position.sell_short(quantity, order.price)
            self.lots.append(
                PaperLot(
                    signal_id=order.signal_id,
                    symbol=order.symbol,
                    direction="short",
                    original_quantity=quantity,
                    remaining_quantity=quantity,
                    entry_price=order.price,
                    exit_orders=order.exit_orders,
                    trailing_stop_pct=order.trailing_stop_pct,
                    trailing_stop_amount=order.trailing_stop_amount,
                    trailing_stop_price=order.trailing_stop_price,
                    trailing_step_pct=order.trailing_step_pct,
                    trailing_step_amount=order.trailing_step_amount,
                    trailing_activation_pct=order.trailing_activation_pct,
                    trailing_activation_price=order.trailing_activation_price,
                    trail_after_take_profit=order.trail_after_take_profit,
                    trailing_activated=_trailing_starts_activated(
                        order.trailing_activation_pct,
                        order.trailing_activation_price,
                        order.trail_after_take_profit,
                    ),
                    low_water_mark=order.price
                    if _has_trailing_distance(order.trailing_stop_pct, order.trailing_stop_amount)
                    and _trailing_starts_activated(
                        order.trailing_activation_pct,
                        order.trailing_activation_price,
                        order.trail_after_take_profit,
                    )
                    else None,
                    breakeven_trigger_pct=order.breakeven_trigger_pct,
                    breakeven_after_take_profit=order.breakeven_after_take_profit,
                    max_hold_marks=order.max_hold_marks,
                    entry_fee_remaining=order.fee,
                )
            )
        elif order.side == "sell":
            self._sell_quantity(order.symbol, quantity, order.price, order.fee)
        self._refresh_active_exits(order.symbol)

    def _replay_bracket_cancel(self, order: PaperOrder) -> None:
        for lot in self.lots:
            if lot.signal_id == order.signal_id and lot.remaining_quantity > 0:
                lot.exit_orders = []
        self._refresh_active_exits(order.symbol)

    def _replay_bracket_stop_amend(self, order: PaperOrder) -> None:
        if not order.exit_orders:
            return
        for lot in self.lots:
            if lot.signal_id == order.signal_id and lot.remaining_quantity > 0:
                amended_stop = _protective_stop_amendment(lot, order.exit_orders[0].trigger_price)
                if amended_stop is not None:
                    lot.exit_orders = _replace_or_append_stop(lot.exit_orders, amended_stop)
        self._refresh_active_exits(order.symbol)

    def _replay_bracket_trailing_stop_amend(self, order: PaperOrder) -> None:
        if not order.exit_orders:
            return
        for lot in self.lots:
            if lot.signal_id == order.signal_id and lot.remaining_quantity > 0:
                amended_trail = _protective_trailing_amendment(lot, order.exit_orders[0].trigger_price)
                if amended_trail is not None:
                    lot.exit_orders = _replace_or_append_trailing_stop(lot.exit_orders, amended_trail)
                    _sync_trailing_water_mark(lot, amended_trail.trigger_price)
        self._refresh_active_exits(order.symbol)

    def _replay_bracket_take_profit_amend(self, order: PaperOrder) -> None:
        if not order.exit_orders:
            return
        target_index = order.amend_target_index or 0
        for lot in self.lots:
            if lot.signal_id == order.signal_id and lot.remaining_quantity > 0:
                amended_target = _take_profit_amendment(
                    lot,
                    order.exit_orders[0].trigger_price,
                    target_index=target_index,
                )
                if amended_target is not None:
                    lot.exit_orders = _replace_take_profit(
                        lot.exit_orders,
                        amended_target,
                        target_index=target_index,
                    )
        self._refresh_active_exits(order.symbol)

    def _replay_bracket_breakeven(self, order: PaperOrder) -> None:
        for lot in self.lots:
            if lot.signal_id == order.signal_id and lot.remaining_quantity > 0:
                updated_exit_orders, amendments = _breakeven_exit_amendments(lot)
                if amendments:
                    lot.exit_orders = updated_exit_orders
                    lot.breakeven_applied = True
                    for amendment in amendments:
                        if amendment.kind == "trailing_stop":
                            _sync_trailing_water_mark(lot, amendment.trigger_price)
        self._refresh_active_exits(order.symbol)

    def _replay_bracket_profit_lock(self, order: PaperOrder) -> None:
        if order.price is None:
            return
        for lot in self.lots:
            if lot.signal_id == order.signal_id and lot.remaining_quantity > 0:
                updated_exit_orders, amendments = _protective_exit_price_amendments(lot, order.price)
                if amendments:
                    lot.exit_orders = updated_exit_orders
                    for amendment in amendments:
                        if amendment.kind == "trailing_stop":
                            _sync_trailing_water_mark(lot, amendment.trigger_price)
        self._refresh_active_exits(order.symbol)

    def _triggered_exit(self, lot: PaperLot, price: Decimal) -> ExitOrder | None:
        for exit_order in lot.exit_orders:
            if lot.direction == "long" and exit_order.kind == "stop_loss" and price <= exit_order.trigger_price:
                return exit_order
            if lot.direction == "short" and exit_order.kind == "stop_loss" and price >= exit_order.trigger_price:
                return exit_order
            if (
                lot.direction == "long"
                and exit_order.kind == "trailing_stop"
                and lot.trailing_activated
                and price <= exit_order.trigger_price
            ):
                return exit_order
            if (
                lot.direction == "short"
                and exit_order.kind == "trailing_stop"
                and lot.trailing_activated
                and price >= exit_order.trigger_price
            ):
                return exit_order
        for exit_order in lot.exit_orders:
            if lot.direction == "long" and exit_order.kind == "take_profit" and price >= exit_order.trigger_price:
                return exit_order
            if lot.direction == "short" and exit_order.kind == "take_profit" and price <= exit_order.trigger_price:
                return exit_order
        if lot.max_hold_marks is not None and lot.marks_seen >= lot.max_hold_marks:
            return ExitOrder(
                kind="time_exit",
                trigger_price=_money(price),
                close_pct=Decimal("100"),
                oca_group=lot.exit_orders[0].oca_group if lot.exit_orders else f"oca-{_order_fragment(lot.signal_id)}",
            )
        return None

    def _update_trailing_stop(self, lot: PaperLot, price: Decimal) -> None:
        if not _has_trailing_distance(lot.trailing_stop_pct, lot.trailing_stop_amount):
            return
        if lot.trail_after_take_profit and not lot.take_profit_filled:
            return
        if lot.direction == "short":
            self._update_short_trailing_stop(lot, price)
            return
        activated_now = False
        if not lot.trailing_activated:
            activation_price = _trailing_activation_price(lot)
            if price < activation_price:
                return
            lot.trailing_activated = True
            activated_now = True
            lot.high_water_mark = price
        elif lot.high_water_mark is None:
            lot.high_water_mark = lot.entry_price
        if price <= lot.high_water_mark and not activated_now:
            return
        lot.high_water_mark = price
        trigger = _money(price - _trailing_distance(lot, price))
        current_trigger = _current_trailing_trigger(lot)
        if current_trigger is not None and trigger <= current_trigger:
            return
        if current_trigger is not None and not _trailing_step_reached(lot, current_trigger, trigger):
            return
        lot.exit_orders = [
            ExitOrder(
                kind=exit_order.kind,
                trigger_price=trigger,
                close_pct=exit_order.close_pct,
                oca_group=exit_order.oca_group,
                status="open",
            )
            if exit_order.kind == "trailing_stop"
            else exit_order
            for exit_order in lot.exit_orders
        ]

    def _release_trailing_after_take_profit(self, lot: PaperLot, price: Decimal) -> None:
        if not lot.trail_after_take_profit or not _has_trailing_distance(lot.trailing_stop_pct, lot.trailing_stop_amount):
            return
        if not any(exit_order.kind == "trailing_stop" for exit_order in lot.exit_orders):
            return
        if lot.direction == "long":
            if _has_trailing_activation(lot) and price < _trailing_activation_price(lot):
                lot.exit_orders = _set_trailing_status(lot.exit_orders, "pending_activation")
                return
            lot.trailing_activated = True
            lot.high_water_mark = price
            trigger = _money(price - _trailing_distance(lot, price))
        else:
            if _has_trailing_activation(lot) and price > _trailing_activation_price(lot):
                lot.exit_orders = _set_trailing_status(lot.exit_orders, "pending_activation")
                return
            lot.trailing_activated = True
            lot.low_water_mark = price
            trigger = _money(price + _trailing_distance(lot, price))
        lot.exit_orders = _replace_or_append_trailing_stop(
            lot.exit_orders,
            ExitOrder(
                kind="trailing_stop",
                trigger_price=trigger,
                close_pct=next(
                    exit_order.close_pct for exit_order in lot.exit_orders if exit_order.kind == "trailing_stop"
                ),
                oca_group=next(
                    exit_order.oca_group for exit_order in lot.exit_orders if exit_order.kind == "trailing_stop"
                ),
                status="open",
            ),
        )

    def _update_short_trailing_stop(self, lot: PaperLot, price: Decimal) -> None:
        activated_now = False
        if not lot.trailing_activated:
            activation_price = _trailing_activation_price(lot)
            if price > activation_price:
                return
            lot.trailing_activated = True
            activated_now = True
            lot.low_water_mark = price
        elif lot.low_water_mark is None:
            lot.low_water_mark = lot.entry_price
        if price >= lot.low_water_mark and not activated_now:
            return
        lot.low_water_mark = price
        trigger = _money(price + _trailing_distance(lot, price))
        current_trigger = _current_trailing_trigger(lot)
        if current_trigger is not None and trigger >= current_trigger:
            return
        if current_trigger is not None and not _trailing_step_reached(lot, current_trigger, trigger):
            return
        lot.exit_orders = [
            ExitOrder(
                kind=exit_order.kind,
                trigger_price=trigger,
                close_pct=exit_order.close_pct,
                oca_group=exit_order.oca_group,
                status="open",
            )
            if exit_order.kind == "trailing_stop"
            else exit_order
            for exit_order in lot.exit_orders
        ]

    def _apply_breakeven(self, lot: PaperLot, price: Decimal) -> None:
        if lot.breakeven_trigger_pct is None or lot.breakeven_applied:
            return
        if lot.direction == "long":
            trigger_price = lot.entry_price * (Decimal("1") + lot.breakeven_trigger_pct / Decimal("100"))
            if price < trigger_price:
                return
        else:
            trigger_price = lot.entry_price * (Decimal("1") - lot.breakeven_trigger_pct / Decimal("100"))
            if price > trigger_price:
                return
        breakeven_price = _money(lot.entry_price)
        lot.exit_orders = [
            ExitOrder(
                kind=exit_order.kind,
                trigger_price=max(exit_order.trigger_price, breakeven_price)
                if lot.direction == "long"
                else min(exit_order.trigger_price, breakeven_price),
                close_pct=exit_order.close_pct,
                oca_group=exit_order.oca_group,
                status=exit_order.status,
            )
            if exit_order.kind in {"stop_loss", "trailing_stop"}
            else exit_order
            for exit_order in lot.exit_orders
        ]
        lot.breakeven_applied = True

    def _apply_take_profit_breakeven(self, lot: PaperLot) -> bool:
        if not lot.breakeven_after_take_profit or lot.breakeven_applied:
            return False
        updated_exit_orders, amendments = _breakeven_exit_amendments(lot)
        if not amendments:
            return False
        lot.exit_orders = updated_exit_orders
        lot.breakeven_applied = True
        for amendment in amendments:
            if amendment.kind == "trailing_stop":
                _sync_trailing_water_mark(lot, amendment.trigger_price)
        return True

    def _exit_quantity(self, lot: PaperLot, exit_order: ExitOrder) -> Decimal:
        if exit_order.kind not in {"take_profit", "trailing_stop"}:
            return lot.remaining_quantity
        target_quantity = lot.original_quantity * exit_order.close_pct / Decimal("100")
        return min(target_quantity, lot.remaining_quantity)

    def _close_lot(
        self,
        lot: PaperLot,
        price: Decimal,
        quantity: Decimal | None = None,
        exit_fee: Decimal = Decimal("0"),
    ) -> None:
        position = self.positions.get(lot.symbol)
        if position is None:
            lot.remaining_quantity = Decimal("0")
            return
        open_quantity = position.quantity if lot.direction == "long" else abs(position.quantity)
        exit_quantity = min(quantity or lot.remaining_quantity, lot.remaining_quantity, open_quantity)
        entry_fee = _allocated_entry_fee(lot, exit_quantity)
        position.fees_paid += exit_fee
        if lot.direction == "long":
            position.realized_pnl += (price - lot.entry_price) * exit_quantity - entry_fee - exit_fee
            position.quantity -= exit_quantity
        else:
            position.realized_pnl += (lot.entry_price - price) * exit_quantity - entry_fee - exit_fee
            position.quantity += exit_quantity
        lot.entry_fee_remaining -= entry_fee
        lot.remaining_quantity -= exit_quantity
        if position.quantity == 0:
            position.quantity = Decimal("0")
            position.avg_entry = Decimal("0")
        else:
            self._refresh_position_average(lot.symbol)

    def _canceled_sibling_exits(self, lot: PaperLot, triggered_exit: ExitOrder) -> list[ExitOrder]:
        if lot.remaining_quantity > 0:
            return []
        return [
            ExitOrder(
                kind=exit_order.kind,
                trigger_price=exit_order.trigger_price,
                close_pct=exit_order.close_pct,
                oca_group=exit_order.oca_group,
                status="canceled",
            )
            for exit_order in lot.exit_orders
            if exit_order is not triggered_exit and exit_order.status not in {"canceled", "filled"}
        ]

    def _sell_quantity(
        self,
        symbol: str,
        quantity: Decimal,
        price: Decimal,
        exit_fee: Decimal = Decimal("0"),
    ) -> None:
        position = self.positions.setdefault(symbol, PaperPosition(symbol=symbol))
        position.fees_paid += exit_fee
        remaining = quantity
        remaining_fee = exit_fee
        for lot in self.lots:
            if remaining <= 0 or lot.symbol != symbol or lot.direction != "long" or lot.remaining_quantity <= 0:
                continue
            reduction = min(lot.remaining_quantity, remaining)
            fee_share = _proportional_fee(exit_fee, reduction, quantity)
            entry_fee = _allocated_entry_fee(lot, reduction)
            position.realized_pnl += (price - lot.entry_price) * reduction - entry_fee - fee_share
            position.quantity -= reduction
            lot.entry_fee_remaining -= entry_fee
            lot.remaining_quantity -= reduction
            remaining -= reduction
            remaining_fee -= fee_share
        if remaining > 0:
            position.sell(remaining, price)
            position.realized_pnl -= remaining_fee
        self.lots = [lot for lot in self.lots if lot.remaining_quantity > 0]
        if position.quantity == 0:
            position.quantity = Decimal("0")
            position.avg_entry = Decimal("0")
        else:
            self._refresh_position_average(symbol)

    def _buy_quantity(
        self,
        symbol: str,
        quantity: Decimal,
        price: Decimal,
        exit_fee: Decimal = Decimal("0"),
    ) -> None:
        position = self.positions.setdefault(symbol, PaperPosition(symbol=symbol))
        position.fees_paid += exit_fee
        remaining = quantity
        remaining_fee = exit_fee
        for lot in self.lots:
            if remaining <= 0 or lot.symbol != symbol or lot.direction != "short" or lot.remaining_quantity <= 0:
                continue
            reduction = min(lot.remaining_quantity, remaining)
            fee_share = _proportional_fee(exit_fee, reduction, quantity)
            entry_fee = _allocated_entry_fee(lot, reduction)
            position.realized_pnl += (lot.entry_price - price) * reduction - entry_fee - fee_share
            position.quantity += reduction
            lot.entry_fee_remaining -= entry_fee
            lot.remaining_quantity -= reduction
            remaining -= reduction
            remaining_fee -= fee_share
        if remaining > 0:
            position.realized_pnl -= remaining_fee
        self.lots = [lot for lot in self.lots if lot.remaining_quantity > 0]
        if position.quantity == 0:
            position.quantity = Decimal("0")
            position.avg_entry = Decimal("0")
        else:
            self._refresh_position_average(symbol)

    def _refresh_position_average(self, symbol: str) -> None:
        position = self.positions.get(symbol)
        if position is None or position.quantity == 0:
            return
        direction = "long" if position.quantity > 0 else "short"
        open_lots = [
            lot
            for lot in self.lots
            if lot.symbol == symbol and lot.direction == direction and lot.remaining_quantity > 0
        ]
        lot_quantity = sum((lot.remaining_quantity for lot in open_lots), Decimal("0"))
        if lot_quantity == abs(position.quantity) and lot_quantity > 0:
            position.avg_entry = (
                sum((lot.entry_price * lot.remaining_quantity for lot in open_lots), Decimal("0"))
                / lot_quantity
            )

    def _refresh_active_exits(self, symbol: str) -> None:
        exits = [
            exit_order
            for lot in self.lots
            if lot.symbol == symbol and lot.remaining_quantity > 0
            for exit_order in lot.exit_orders
        ]
        if exits:
            self.active_exits[symbol] = exits
        else:
            self.active_exits.pop(symbol, None)


def build_exit_orders(signal: CryptoSignal) -> list[ExitOrder]:
    if signal.price is None or signal.side not in {"buy", "sell"}:
        return []
    if signal.reduce_only:
        return []
    if signal.side == "sell" and not _has_exit_plan(signal):
        return []

    exits: list[ExitOrder] = []
    oca_group = f"oca-{_order_fragment(signal.signal_id)}"
    if signal.stop_loss_price is not None:
        exits.append(ExitOrder(kind="stop_loss", trigger_price=_money(signal.stop_loss_price), oca_group=oca_group))
    elif signal.stop_loss_pct is not None:
        stop_direction = Decimal("-1") if signal.side == "buy" else Decimal("1")
        trigger = signal.price * (Decimal("1") + stop_direction * signal.stop_loss_pct / Decimal("100"))
        exits.append(ExitOrder(kind="stop_loss", trigger_price=_money(trigger), oca_group=oca_group))
    if signal.take_profit_targets:
        for target in signal.take_profit_targets:
            if target.trigger_price is not None:
                trigger = target.trigger_price
            else:
                profit_direction = Decimal("1") if signal.side == "buy" else Decimal("-1")
                trigger = signal.price * (Decimal("1") + profit_direction * (target.pct or Decimal("0")) / Decimal("100"))
            exits.append(
                ExitOrder(
                    kind="take_profit",
                    trigger_price=_money(trigger),
                    close_pct=target.close_pct,
                    oca_group=oca_group,
                )
            )
    if signal.trailing_stop_pct is not None or signal.trailing_stop_amount is not None:
        if signal.trailing_stop_price is not None:
            trigger = signal.trailing_stop_price
        elif signal.trailing_stop_amount is not None:
            trail_direction = Decimal("-1") if signal.side == "buy" else Decimal("1")
            trigger = signal.price + trail_direction * signal.trailing_stop_amount
        else:
            trail_direction = Decimal("-1") if signal.side == "buy" else Decimal("1")
            trigger = signal.price * (Decimal("1") + trail_direction * signal.trailing_stop_pct / Decimal("100"))
        status = (
            "pending_take_profit"
            if signal.trail_after_take_profit
            else
            "pending_activation"
            if signal.trailing_activation_pct is not None or signal.trailing_activation_price is not None
            else "open"
        )
        exits.append(
            ExitOrder(
                kind="trailing_stop",
                trigger_price=_money(trigger),
                close_pct=signal.trailing_stop_close_pct,
                oca_group=oca_group,
                status=status,
            )
        )
    if signal.max_hold_marks is not None:
        exits.append(
            ExitOrder(
                kind="time_exit",
                trigger_price=_money(signal.price),
                oca_group=oca_group,
                status="waiting",
            )
        )
    return exits


def _has_exit_plan(signal: CryptoSignal) -> bool:
    return (
        signal.stop_loss_pct is not None
        or signal.stop_loss_price is not None
        or bool(signal.take_profit_targets)
        or signal.take_profit_price is not None
        or signal.trailing_stop_pct is not None
        or signal.trailing_stop_amount is not None
        or signal.trailing_stop_price is not None
        or signal.trailing_activation_price is not None
        or signal.breakeven_trigger_pct is not None
        or signal.max_hold_marks is not None
    )


def _protective_stop_amendment(lot: PaperLot, trigger_price: Decimal) -> ExitOrder | None:
    existing_stop = next((exit_order for exit_order in lot.exit_orders if exit_order.kind == "stop_loss"), None)
    if existing_stop is not None:
        if lot.direction == "long" and trigger_price <= existing_stop.trigger_price:
            return None
        if lot.direction == "short" and trigger_price >= existing_stop.trigger_price:
            return None
        oca_group = existing_stop.oca_group
    else:
        first_exit = lot.exit_orders[0] if lot.exit_orders else None
        oca_group = first_exit.oca_group if first_exit else f"oca-{_order_fragment(lot.signal_id)}"
    return ExitOrder(kind="stop_loss", trigger_price=trigger_price, oca_group=oca_group)


def _replace_or_append_stop(exit_orders: list[ExitOrder], amended_stop: ExitOrder) -> list[ExitOrder]:
    replaced = False
    updated: list[ExitOrder] = []
    for exit_order in exit_orders:
        if exit_order.kind == "stop_loss":
            updated.append(amended_stop)
            replaced = True
        else:
            updated.append(exit_order)
    if not replaced:
        updated.insert(0, amended_stop)
    return updated


def _protective_trailing_amendment(lot: PaperLot, trigger_price: Decimal) -> ExitOrder | None:
    existing_trail = next((exit_order for exit_order in lot.exit_orders if exit_order.kind == "trailing_stop"), None)
    if existing_trail is None:
        return None
    if lot.direction == "long" and trigger_price <= existing_trail.trigger_price:
        return None
    if lot.direction == "short" and trigger_price >= existing_trail.trigger_price:
        return None
    return ExitOrder(
        kind="trailing_stop",
        trigger_price=trigger_price,
        close_pct=existing_trail.close_pct,
        oca_group=existing_trail.oca_group,
        status="open",
    )


def _replace_or_append_trailing_stop(exit_orders: list[ExitOrder], amended_trail: ExitOrder) -> list[ExitOrder]:
    replaced = False
    updated: list[ExitOrder] = []
    for exit_order in exit_orders:
        if exit_order.kind == "trailing_stop":
            updated.append(amended_trail)
            replaced = True
        else:
            updated.append(exit_order)
    if not replaced:
        updated.append(amended_trail)
    return updated


def _take_profit_amendment(lot: PaperLot, trigger_price: Decimal, *, target_index: int) -> ExitOrder | None:
    targets = [exit_order for exit_order in lot.exit_orders if exit_order.kind == "take_profit"]
    if target_index < 0 or target_index >= len(targets):
        return None
    existing_target = targets[target_index]
    if lot.direction == "long":
        if trigger_price <= lot.entry_price or trigger_price <= existing_target.trigger_price:
            return None
    elif trigger_price >= lot.entry_price or trigger_price >= existing_target.trigger_price:
        return None
    return ExitOrder(
        kind="take_profit",
        trigger_price=trigger_price,
        close_pct=existing_target.close_pct,
        oca_group=existing_target.oca_group,
        status="open",
    )


def _replace_take_profit(
    exit_orders: list[ExitOrder],
    amended_target: ExitOrder,
    *,
    target_index: int,
) -> list[ExitOrder]:
    seen_targets = 0
    updated: list[ExitOrder] = []
    for exit_order in exit_orders:
        if exit_order.kind != "take_profit":
            updated.append(exit_order)
            continue
        if seen_targets == target_index:
            updated.append(amended_target)
        else:
            updated.append(exit_order)
        seen_targets += 1
    return updated


def _breakeven_exit_amendments(lot: PaperLot) -> tuple[list[ExitOrder], list[ExitOrder]]:
    breakeven_price = _money(lot.entry_price)
    amendments: list[ExitOrder] = []
    updated: list[ExitOrder] = []
    for exit_order in lot.exit_orders:
        if exit_order.kind not in {"stop_loss", "trailing_stop"}:
            updated.append(exit_order)
            continue
        if lot.direction == "long" and breakeven_price <= exit_order.trigger_price:
            updated.append(exit_order)
            continue
        if lot.direction == "short" and breakeven_price >= exit_order.trigger_price:
            updated.append(exit_order)
            continue
        amendment = ExitOrder(
            kind=exit_order.kind,
            trigger_price=breakeven_price,
            close_pct=exit_order.close_pct,
            oca_group=exit_order.oca_group,
            status="open",
        )
        updated.append(amendment)
        amendments.append(amendment)
    return updated, amendments


def _profit_lock_exit_amendments(
    lot: PaperLot,
    lock_profit_pct: Decimal,
) -> tuple[list[ExitOrder], list[ExitOrder]]:
    return _protective_exit_price_amendments(lot, _profit_lock_price(lot, lock_profit_pct))


def _protective_exit_price_amendments(
    lot: PaperLot,
    trigger_price: Decimal,
) -> tuple[list[ExitOrder], list[ExitOrder]]:
    trigger_price = _money(trigger_price)
    amendments: list[ExitOrder] = []
    updated: list[ExitOrder] = []
    for exit_order in lot.exit_orders:
        if exit_order.kind not in {"stop_loss", "trailing_stop"}:
            updated.append(exit_order)
            continue
        if lot.direction == "long" and trigger_price <= exit_order.trigger_price:
            updated.append(exit_order)
            continue
        if lot.direction == "short" and trigger_price >= exit_order.trigger_price:
            updated.append(exit_order)
            continue
        amendment = ExitOrder(
            kind=exit_order.kind,
            trigger_price=trigger_price,
            close_pct=exit_order.close_pct,
            oca_group=exit_order.oca_group,
            status="open",
        )
        updated.append(amendment)
        amendments.append(amendment)
    return updated, amendments


def _profit_lock_price(lot: PaperLot, lock_profit_pct: Decimal) -> Decimal:
    direction = Decimal("1") if lot.direction == "long" else Decimal("-1")
    return _money(lot.entry_price * (Decimal("1") + direction * lock_profit_pct / Decimal("100")))


def _sync_trailing_water_mark(lot: PaperLot, trigger_price: Decimal) -> None:
    lot.trailing_activated = True
    if not _has_trailing_distance(lot.trailing_stop_pct, lot.trailing_stop_amount):
        return
    if lot.direction == "long":
        implied_high = (
            trigger_price + lot.trailing_stop_amount
            if lot.trailing_stop_amount is not None
            else trigger_price / (Decimal("1") - lot.trailing_stop_pct / Decimal("100"))
        )
        lot.high_water_mark = max(lot.high_water_mark or lot.entry_price, implied_high)
        return
    implied_low = (
        trigger_price - lot.trailing_stop_amount
        if lot.trailing_stop_amount is not None
        else trigger_price / (Decimal("1") + lot.trailing_stop_pct / Decimal("100"))
    )
    lot.low_water_mark = min(lot.low_water_mark or lot.entry_price, implied_low)


def _has_trailing_distance(trailing_stop_pct: Decimal | None, trailing_stop_amount: Decimal | None) -> bool:
    return trailing_stop_pct is not None or trailing_stop_amount is not None


def _bracket_close_quantity(
    total_remaining: Decimal,
    *,
    close_pct: Decimal | None,
    base_amount: Decimal | None,
) -> Decimal | None:
    if total_remaining <= 0:
        return None
    if close_pct is not None:
        if close_pct <= 0 or close_pct > 100:
            return None
        return min(total_remaining, total_remaining * close_pct / Decimal("100"))
    if base_amount is not None:
        if base_amount <= 0:
            return None
        return min(total_remaining, base_amount)
    return total_remaining


def _target_preview_lots(exchange: PaperExchange, signal_id: str) -> list[PaperLot]:
    return [
        lot
        for lot in exchange.lots
        if lot.signal_id == signal_id and lot.remaining_quantity > 0 and lot.exit_orders
    ]


def _preview_lots_for_signal(exchange: PaperExchange, signal_id: str, symbol: str) -> list[PaperLot]:
    return [
        lot
        for lot in exchange.lots
        if lot.signal_id == signal_id or lot.symbol != symbol
    ]


def _nearest_protective_exit(lot: PaperLot) -> ExitOrder | None:
    protective_exits = [
        exit_order
        for exit_order in lot.exit_orders
        if exit_order.kind in {"stop_loss", "trailing_stop"} and exit_order.status == "open"
    ]
    if lot.direction == "long":
        return max(protective_exits, key=lambda item: item.trigger_price, default=None)
    return min(protective_exits, key=lambda item: item.trigger_price, default=None)


def _trailing_starts_activated(
    trailing_activation_pct: Decimal | None,
    trailing_activation_price: Decimal | None,
    trail_after_take_profit: bool = False,
) -> bool:
    if trail_after_take_profit:
        return False
    return trailing_activation_pct is None and trailing_activation_price is None


def _has_trailing_activation(lot: PaperLot) -> bool:
    return lot.trailing_activation_pct is not None or lot.trailing_activation_price is not None


def _set_trailing_status(exit_orders: list[ExitOrder], status: str) -> list[ExitOrder]:
    return [
        ExitOrder(
            kind=exit_order.kind,
            trigger_price=exit_order.trigger_price,
            close_pct=exit_order.close_pct,
            oca_group=exit_order.oca_group,
            status=status,
        )
        if exit_order.kind == "trailing_stop"
        else exit_order
        for exit_order in exit_orders
    ]


def _trailing_activation_price(lot: PaperLot) -> Decimal:
    if lot.trailing_activation_price is not None:
        return lot.trailing_activation_price
    if lot.direction == "long":
        return lot.entry_price * (Decimal("1") + (lot.trailing_activation_pct or Decimal("0")) / Decimal("100"))
    return lot.entry_price * (Decimal("1") - (lot.trailing_activation_pct or Decimal("0")) / Decimal("100"))


def _trailing_distance(lot: PaperLot, price: Decimal) -> Decimal:
    if lot.trailing_stop_amount is not None:
        return lot.trailing_stop_amount
    if lot.trailing_stop_pct is None:
        return Decimal("0")
    return price * lot.trailing_stop_pct / Decimal("100")


def _current_trailing_trigger(lot: PaperLot) -> Decimal | None:
    trailing_exit = next((exit_order for exit_order in lot.exit_orders if exit_order.kind == "trailing_stop"), None)
    return trailing_exit.trigger_price if trailing_exit is not None else None


def _candidate_trailing_trigger(lot: PaperLot, mark_price: Decimal) -> Decimal | None:
    if not _has_trailing_distance(lot.trailing_stop_pct, lot.trailing_stop_amount):
        return None
    if lot.trail_after_take_profit and not lot.take_profit_filled:
        return None
    if not lot.trailing_activated and _has_trailing_activation(lot):
        activation_price = _trailing_activation_price(lot)
        if lot.direction == "long" and mark_price < activation_price:
            return None
        if lot.direction == "short" and mark_price > activation_price:
            return None
    if lot.direction == "long":
        water_mark = max(lot.high_water_mark or lot.entry_price, mark_price)
        return _money(water_mark - _trailing_distance(lot, water_mark))
    water_mark = min(lot.low_water_mark or lot.entry_price, mark_price)
    return _money(water_mark + _trailing_distance(lot, water_mark))


def _trailing_step_reached(lot: PaperLot, current_trigger: Decimal, next_trigger: Decimal) -> bool:
    step = _trailing_step(lot, current_trigger)
    if step <= 0:
        return True
    improvement = next_trigger - current_trigger if lot.direction == "long" else current_trigger - next_trigger
    return improvement >= step


def _trailing_step(lot: PaperLot, current_trigger: Decimal) -> Decimal:
    if lot.trailing_step_amount is not None:
        return lot.trailing_step_amount
    if lot.trailing_step_pct is not None:
        return current_trigger * lot.trailing_step_pct / Decimal("100")
    return Decimal("0")


def _filled_exit(exit_order: ExitOrder) -> ExitOrder:
    return ExitOrder(
        kind=exit_order.kind,
        trigger_price=exit_order.trigger_price,
        close_pct=exit_order.close_pct,
        oca_group=exit_order.oca_group,
        status="filled",
    )


def _allocated_entry_fee(lot: PaperLot, exit_quantity: Decimal) -> Decimal:
    if lot.entry_fee_remaining <= 0 or lot.remaining_quantity <= 0:
        return Decimal("0")
    return min(lot.entry_fee_remaining, lot.entry_fee_remaining * exit_quantity / lot.remaining_quantity)


def _proportional_fee(total_fee: Decimal, quantity: Decimal, total_quantity: Decimal) -> Decimal:
    if total_fee <= 0 or total_quantity <= 0:
        return Decimal("0")
    return total_fee * quantity / total_quantity


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


def _fixed8(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.00000001'), rounding=ROUND_HALF_UP):f}"


def _order_fragment(symbol: str) -> str:
    return symbol.replace("/", "-").lower()


def _paper_order_from_dict(payload: dict) -> PaperOrder:
    return PaperOrder(
        order_id=str(payload["order_id"]),
        signal_id=str(payload["signal_id"]),
        mode=str(payload["mode"]),
        exchange=str(payload["exchange"]),
        symbol=str(payload["symbol"]),
        side=str(payload["side"]),
        notional=Decimal(str(payload["notional"])),
        price=Decimal(str(payload["price"])) if payload.get("price") is not None else None,
        exit_orders=[_exit_order_from_dict(exit_order) for exit_order in payload.get("exit_orders", [])],
        trailing_stop_pct=Decimal(str(payload["trailing_stop_pct"]))
        if payload.get("trailing_stop_pct") is not None
        else None,
        trailing_stop_amount=Decimal(str(payload["trailing_stop_amount"]))
        if payload.get("trailing_stop_amount") is not None
        else None,
        trailing_stop_price=Decimal(str(payload["trailing_stop_price"]))
        if payload.get("trailing_stop_price") is not None
        else None,
        trailing_step_pct=Decimal(str(payload["trailing_step_pct"]))
        if payload.get("trailing_step_pct") is not None
        else None,
        trailing_step_amount=Decimal(str(payload["trailing_step_amount"]))
        if payload.get("trailing_step_amount") is not None
        else None,
        trailing_activation_pct=Decimal(str(payload["trailing_activation_pct"]))
        if payload.get("trailing_activation_pct") is not None
        else None,
        trailing_activation_price=Decimal(str(payload["trailing_activation_price"]))
        if payload.get("trailing_activation_price") is not None
        else None,
        trail_after_take_profit=_bool_from_payload(payload.get("trail_after_take_profit")),
        breakeven_trigger_pct=Decimal(str(payload["breakeven_trigger_pct"]))
        if payload.get("breakeven_trigger_pct") is not None
        else None,
        breakeven_after_take_profit=_bool_from_payload(payload.get("breakeven_after_take_profit")),
        max_hold_marks=int(payload["max_hold_marks"]) if payload.get("max_hold_marks") is not None else None,
        exit_kind=payload.get("exit_kind"),
        amend_target_index=int(payload["amend_target_index"])
        if payload.get("amend_target_index") is not None
        else None,
        canceled_exit_orders=[
            _exit_order_from_dict(exit_order, default_status="canceled")
            for exit_order in payload.get("canceled_exit_orders", [])
        ],
        reduce_only=bool(payload.get("reduce_only", False)),
        fee=Decimal(str(payload.get("fee") or "0")),
        fee_bps=Decimal(str(payload.get("fee_bps") or "0")),
        slippage_bps=Decimal(str(payload.get("slippage_bps") or "0")),
        status=str(payload.get("status") or "accepted"),
    )


def _exit_order_from_dict(payload: dict, *, default_status: str = "open") -> ExitOrder:
    return ExitOrder(
        kind=str(payload["kind"]),
        trigger_price=Decimal(str(payload["trigger_price"])),
        close_pct=Decimal(str(payload.get("close_pct") or "100")),
        oca_group=payload.get("oca_group"),
        status=str(payload.get("status") or default_status),
    )


def _bool_from_payload(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return False
    return str(value).strip().lower() not in {"0", "false", "no", "off"}
