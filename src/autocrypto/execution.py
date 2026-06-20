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
    trailing_activation_pct: Decimal | None = None
    trailing_activated: bool = True
    high_water_mark: Decimal | None = None
    low_water_mark: Decimal | None = None
    breakeven_trigger_pct: Decimal | None = None
    breakeven_applied: bool = False


@dataclass
class PaperPosition:
    symbol: str
    quantity: Decimal = Decimal("0")
    avg_entry: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")

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
        return {
            "symbol": self.symbol,
            "quantity": _fixed8(self.quantity),
            "avg_entry": _fixed8(self.avg_entry),
            "realized_pnl": _fixed8(self.realized_pnl),
        }


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
    trailing_activation_pct: Decimal | None = None
    breakeven_trigger_pct: Decimal | None = None
    exit_kind: str | None = None
    canceled_exit_orders: list[ExitOrder] = field(default_factory=list)
    reduce_only: bool = False
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
            "trailing_activation_pct": str(self.trailing_activation_pct)
            if self.trailing_activation_pct is not None
            else None,
            "breakeven_trigger_pct": str(self.breakeven_trigger_pct)
            if self.breakeven_trigger_pct is not None
            else None,
            "exit_kind": self.exit_kind,
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

    def __init__(self) -> None:
        self.orders: list[PaperOrder] = []
        self.positions: dict[str, PaperPosition] = {}
        self.lots: list[PaperLot] = []
        self.active_exits: dict[str, list[ExitOrder]] = {}

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
        if signal.price is not None:
            quantity = self._fill_quantity(signal, decision.order_notional)
        order = PaperOrder(
            order_id=f"paper-{signal.signal_id}",
            signal_id=signal.signal_id,
            mode="paper",
            exchange=signal.exchange,
            symbol=signal.symbol,
            side=signal.side,
            notional=decision.order_notional,
            price=signal.price,
            exit_orders=exit_orders,
            trailing_stop_pct=signal.trailing_stop_pct,
            trailing_activation_pct=signal.trailing_activation_pct,
            breakeven_trigger_pct=signal.breakeven_trigger_pct,
            reduce_only=signal.reduce_only,
        )
        self.orders.append(order)
        if quantity is not None:
            self._apply_fill(signal, quantity, exit_orders)
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
            while lot.remaining_quantity > 0:
                exit_order = self._triggered_exit(lot, price)
                if exit_order is None:
                    break

                exit_quantity = self._exit_quantity(lot, exit_order)
                notional = exit_quantity * price
                self._close_lot(lot, price, exit_quantity)
                if exit_order.kind == "take_profit" and lot.remaining_quantity > 0:
                    lot.exit_orders = [order for order in lot.exit_orders if order is not exit_order]
                canceled_exit_orders = self._canceled_sibling_exits(lot, exit_order)
                if lot.remaining_quantity <= 0:
                    lot.exit_orders = []
                order_number = len(self.orders) + 1
                exit_side = "sell" if lot.direction == "long" else "buy"
                order = PaperOrder(
                    order_id=f"paper-exit-{_order_fragment(lot.signal_id)}-{order_number}",
                    signal_id=f"exit-{_order_fragment(lot.signal_id)}-{order_number}",
                    mode="paper",
                    exchange="paper",
                    symbol=symbol,
                    side=exit_side,
                    notional=notional,
                    price=price,
                    exit_orders=[_filled_exit(exit_order)],
                    exit_kind=exit_order.kind,
                    canceled_exit_orders=canceled_exit_orders,
                    reduce_only=True,
                )
                self.orders.append(order)
                triggered.append(
                    {
                        "symbol": symbol,
                        "kind": exit_order.kind,
                        "price": _fixed8(price),
                        "quantity": _fixed8(exit_quantity),
                    }
                )
                if exit_order.kind != "take_profit":
                    break

        self.lots = [lot for lot in self.lots if lot.remaining_quantity > 0]
        self._refresh_active_exits(symbol)
        return triggered

    def preview_price(self, symbol: str, price: Decimal) -> list[dict]:
        """Return paper exits that would trigger at price without mutating state."""
        return deepcopy(self).update_price(symbol, price)

    def preview_bracket(self, signal_id: str, price: Decimal) -> list[dict]:
        """Return exits that would trigger for one paper bracket without mutating state."""
        sandbox = deepcopy(self)
        target_lots = [
            lot
            for lot in sandbox.lots
            if lot.signal_id == signal_id and lot.remaining_quantity > 0 and lot.exit_orders
        ]
        if not target_lots:
            return []
        symbol = target_lots[0].symbol
        sandbox.lots = [
            lot
            for lot in sandbox.lots
            if lot.signal_id == signal_id or lot.symbol != symbol
        ]
        return sandbox.update_price(symbol, price)

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
                if exit_order.status == "open"
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

    def _fill_quantity(self, signal: CryptoSignal, notional: Decimal) -> Decimal:
        if signal.price is None:
            return Decimal("0")
        return signal.base_amount if signal.base_amount is not None else notional / signal.price

    def _apply_fill(self, signal: CryptoSignal, quantity: Decimal, exit_orders: list[ExitOrder]) -> None:
        if signal.price is None:
            return
        position = self.positions.setdefault(signal.symbol, PaperPosition(symbol=signal.symbol))
        if signal.side == "buy" and signal.reduce_only:
            self._buy_quantity(signal.symbol, quantity, signal.price)
        elif signal.side == "buy":
            position.buy(quantity, signal.price)
            self.lots.append(
                PaperLot(
                    signal_id=signal.signal_id,
                    symbol=signal.symbol,
                    direction="long",
                    original_quantity=quantity,
                    remaining_quantity=quantity,
                    entry_price=signal.price,
                    exit_orders=exit_orders,
                    trailing_stop_pct=signal.trailing_stop_pct,
                    trailing_activation_pct=signal.trailing_activation_pct,
                    trailing_activated=signal.trailing_activation_pct is None,
                    high_water_mark=signal.price
                    if signal.trailing_stop_pct is not None and signal.trailing_activation_pct is None
                    else None,
                    breakeven_trigger_pct=signal.breakeven_trigger_pct,
                )
            )
        elif signal.side == "sell" and signal.reduce_only:
            self._sell_quantity(signal.symbol, quantity, signal.price)
        elif signal.side == "sell" and exit_orders:
            position.sell_short(quantity, signal.price)
            self.lots.append(
                PaperLot(
                    signal_id=signal.signal_id,
                    symbol=signal.symbol,
                    direction="short",
                    original_quantity=quantity,
                    remaining_quantity=quantity,
                    entry_price=signal.price,
                    exit_orders=exit_orders,
                    trailing_stop_pct=signal.trailing_stop_pct,
                    trailing_activation_pct=signal.trailing_activation_pct,
                    trailing_activated=signal.trailing_activation_pct is None,
                    low_water_mark=signal.price
                    if signal.trailing_stop_pct is not None and signal.trailing_activation_pct is None
                    else None,
                    breakeven_trigger_pct=signal.breakeven_trigger_pct,
                )
            )
        elif signal.side == "sell":
            self._sell_quantity(signal.symbol, quantity, signal.price)

    def _replay_order(self, order: PaperOrder) -> None:
        self.orders.append(order)
        if order.exit_kind == "bracket_cancel":
            self._replay_bracket_cancel(order)
            return
        if order.exit_kind == "bracket_stop_amend":
            self._replay_bracket_stop_amend(order)
            return
        if order.price is None:
            return
        quantity = order.notional / order.price
        position = self.positions.setdefault(order.symbol, PaperPosition(symbol=order.symbol))
        if order.side == "buy" and order.reduce_only:
            self._buy_quantity(order.symbol, quantity, order.price)
        elif order.side == "buy":
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
                    trailing_activation_pct=order.trailing_activation_pct,
                    trailing_activated=order.trailing_activation_pct is None,
                    high_water_mark=order.price
                    if order.trailing_stop_pct is not None and order.trailing_activation_pct is None
                    else None,
                    breakeven_trigger_pct=order.breakeven_trigger_pct,
                )
            )
        elif order.side == "sell" and order.reduce_only:
            self._sell_quantity(order.symbol, quantity, order.price)
        elif order.side == "sell" and order.exit_orders:
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
                    trailing_activation_pct=order.trailing_activation_pct,
                    trailing_activated=order.trailing_activation_pct is None,
                    low_water_mark=order.price
                    if order.trailing_stop_pct is not None and order.trailing_activation_pct is None
                    else None,
                    breakeven_trigger_pct=order.breakeven_trigger_pct,
                )
            )
        elif order.side == "sell":
            self._sell_quantity(order.symbol, quantity, order.price)
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
        return None

    def _update_trailing_stop(self, lot: PaperLot, price: Decimal) -> None:
        if lot.trailing_stop_pct is None:
            return
        if lot.direction == "short":
            self._update_short_trailing_stop(lot, price)
            return
        activated_now = False
        if not lot.trailing_activated:
            activation_price = lot.entry_price * (Decimal("1") + (lot.trailing_activation_pct or Decimal("0")) / Decimal("100"))
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
        trigger = _money(price * (Decimal("1") - lot.trailing_stop_pct / Decimal("100")))
        lot.exit_orders = [
            ExitOrder(
                kind=exit_order.kind,
                trigger_price=max(exit_order.trigger_price, trigger),
                close_pct=exit_order.close_pct,
                oca_group=exit_order.oca_group,
                status="open",
            )
            if exit_order.kind == "trailing_stop"
            else exit_order
            for exit_order in lot.exit_orders
        ]

    def _update_short_trailing_stop(self, lot: PaperLot, price: Decimal) -> None:
        activated_now = False
        if not lot.trailing_activated:
            activation_price = lot.entry_price * (Decimal("1") - (lot.trailing_activation_pct or Decimal("0")) / Decimal("100"))
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
        trigger = _money(price * (Decimal("1") + lot.trailing_stop_pct / Decimal("100")))
        lot.exit_orders = [
            ExitOrder(
                kind=exit_order.kind,
                trigger_price=min(exit_order.trigger_price, trigger),
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

    def _exit_quantity(self, lot: PaperLot, exit_order: ExitOrder) -> Decimal:
        if exit_order.kind != "take_profit":
            return lot.remaining_quantity
        target_quantity = lot.original_quantity * exit_order.close_pct / Decimal("100")
        return min(target_quantity, lot.remaining_quantity)

    def _close_lot(self, lot: PaperLot, price: Decimal, quantity: Decimal | None = None) -> None:
        position = self.positions.get(lot.symbol)
        if position is None:
            lot.remaining_quantity = Decimal("0")
            return
        open_quantity = position.quantity if lot.direction == "long" else abs(position.quantity)
        exit_quantity = min(quantity or lot.remaining_quantity, lot.remaining_quantity, open_quantity)
        if lot.direction == "long":
            position.realized_pnl += (price - lot.entry_price) * exit_quantity
            position.quantity -= exit_quantity
        else:
            position.realized_pnl += (lot.entry_price - price) * exit_quantity
            position.quantity += exit_quantity
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
            if exit_order is not triggered_exit and exit_order.status == "open"
        ]

    def _sell_quantity(self, symbol: str, quantity: Decimal, price: Decimal) -> None:
        position = self.positions.setdefault(symbol, PaperPosition(symbol=symbol))
        remaining = quantity
        for lot in self.lots:
            if remaining <= 0 or lot.symbol != symbol or lot.direction != "long" or lot.remaining_quantity <= 0:
                continue
            reduction = min(lot.remaining_quantity, remaining)
            position.realized_pnl += (price - lot.entry_price) * reduction
            position.quantity -= reduction
            lot.remaining_quantity -= reduction
            remaining -= reduction
        if remaining > 0:
            position.sell(remaining, price)
        self.lots = [lot for lot in self.lots if lot.remaining_quantity > 0]
        if position.quantity == 0:
            position.quantity = Decimal("0")
            position.avg_entry = Decimal("0")
        else:
            self._refresh_position_average(symbol)

    def _buy_quantity(self, symbol: str, quantity: Decimal, price: Decimal) -> None:
        position = self.positions.setdefault(symbol, PaperPosition(symbol=symbol))
        remaining = quantity
        for lot in self.lots:
            if remaining <= 0 or lot.symbol != symbol or lot.direction != "short" or lot.remaining_quantity <= 0:
                continue
            reduction = min(lot.remaining_quantity, remaining)
            position.realized_pnl += (lot.entry_price - price) * reduction
            position.quantity += reduction
            lot.remaining_quantity -= reduction
            remaining -= reduction
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
    if signal.trailing_stop_pct is not None:
        trail_direction = Decimal("-1") if signal.side == "buy" else Decimal("1")
        trigger = signal.price * (Decimal("1") + trail_direction * signal.trailing_stop_pct / Decimal("100"))
        status = "pending_activation" if signal.trailing_activation_pct is not None else "open"
        exits.append(ExitOrder(kind="trailing_stop", trigger_price=_money(trigger), oca_group=oca_group, status=status))
    return exits


def _has_exit_plan(signal: CryptoSignal) -> bool:
    return (
        signal.stop_loss_pct is not None
        or signal.stop_loss_price is not None
        or bool(signal.take_profit_targets)
        or signal.take_profit_price is not None
        or signal.trailing_stop_pct is not None
        or signal.breakeven_trigger_pct is not None
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


def _filled_exit(exit_order: ExitOrder) -> ExitOrder:
    return ExitOrder(
        kind=exit_order.kind,
        trigger_price=exit_order.trigger_price,
        close_pct=exit_order.close_pct,
        oca_group=exit_order.oca_group,
        status="filled",
    )


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
        trailing_activation_pct=Decimal(str(payload["trailing_activation_pct"]))
        if payload.get("trailing_activation_pct") is not None
        else None,
        breakeven_trigger_pct=Decimal(str(payload["breakeven_trigger_pct"]))
        if payload.get("breakeven_trigger_pct") is not None
        else None,
        exit_kind=payload.get("exit_kind"),
        canceled_exit_orders=[
            _exit_order_from_dict(exit_order, default_status="canceled")
            for exit_order in payload.get("canceled_exit_orders", [])
        ],
        reduce_only=bool(payload.get("reduce_only", False)),
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
