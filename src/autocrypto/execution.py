from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP

from .risk import RiskDecision
from .signals import CryptoSignal


MONEY = Decimal("0.01")


@dataclass(frozen=True)
class ExitOrder:
    kind: str
    trigger_price: Decimal


@dataclass
class PaperLot:
    signal_id: str
    symbol: str
    remaining_quantity: Decimal
    entry_price: Decimal
    exit_orders: list[ExitOrder] = field(default_factory=list)


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
        sell_quantity = min(quantity, self.quantity)
        self.realized_pnl += (price - self.avg_entry) * sell_quantity
        self.quantity -= sell_quantity
        if self.quantity <= 0:
            self.quantity = Decimal("0")
            self.avg_entry = Decimal("0")

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
                {"kind": exit_order.kind, "trigger_price": str(exit_order.trigger_price)}
                for exit_order in self.exit_orders
            ],
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
            self._apply_fill(signal, quantity)
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
        )
        self.orders.append(order)
        if signal.side == "buy" and exit_orders and quantity is not None and signal.price is not None:
            self.lots.append(
                PaperLot(
                    signal_id=signal.signal_id,
                    symbol=signal.symbol,
                    remaining_quantity=quantity,
                    entry_price=signal.price,
                    exit_orders=exit_orders,
                )
            )
        elif signal.side == "sell" and quantity is not None:
            self._reduce_lots(signal.symbol, quantity)
        self._refresh_active_exits(signal.symbol)
        return order

    def list_positions(self) -> list[dict]:
        return [
            position.to_dict()
            for position in self.positions.values()
            if position.quantity > 0 or position.realized_pnl != 0
        ]

    def update_price(self, symbol: str, price: Decimal) -> list[dict]:
        position = self.positions.get(symbol)
        if position is None or position.quantity <= 0:
            return []

        triggered: list[dict] = []
        for lot in list(self.lots):
            if lot.symbol != symbol or lot.remaining_quantity <= 0:
                continue
            exit_order = self._triggered_exit(lot, price)
            if exit_order is None:
                continue

            exit_quantity = lot.remaining_quantity
            notional = exit_quantity * price
            self._close_lot(lot, price)
            order_number = len(self.orders) + 1
            order = PaperOrder(
                order_id=f"paper-exit-{_order_fragment(lot.signal_id)}-{order_number}",
                signal_id=f"exit-{_order_fragment(lot.signal_id)}-{order_number}",
                mode="paper",
                exchange="paper",
                symbol=symbol,
                side="sell",
                notional=notional,
                price=price,
            )
            self.orders.append(order)
            triggered.append({"symbol": symbol, "kind": exit_order.kind, "price": _fixed8(price)})

        self.lots = [lot for lot in self.lots if lot.remaining_quantity > 0]
        self._refresh_active_exits(symbol)
        return triggered

    def _fill_quantity(self, signal: CryptoSignal, notional: Decimal) -> Decimal:
        if signal.price is None:
            return Decimal("0")
        return signal.base_amount if signal.base_amount is not None else notional / signal.price

    def _apply_fill(self, signal: CryptoSignal, quantity: Decimal) -> None:
        if signal.price is None:
            return
        position = self.positions.setdefault(signal.symbol, PaperPosition(symbol=signal.symbol))
        if signal.side == "buy":
            position.buy(quantity, signal.price)
        elif signal.side == "sell":
            position.sell(quantity, signal.price)

    def _replay_order(self, order: PaperOrder) -> None:
        self.orders.append(order)
        if order.price is None:
            return
        quantity = order.notional / order.price
        position = self.positions.setdefault(order.symbol, PaperPosition(symbol=order.symbol))
        if order.side == "buy":
            position.buy(quantity, order.price)
            if order.exit_orders:
                self.lots.append(
                    PaperLot(
                        signal_id=order.signal_id,
                        symbol=order.symbol,
                        remaining_quantity=quantity,
                        entry_price=order.price,
                        exit_orders=order.exit_orders,
                    )
                )
        elif order.side == "sell":
            position.sell(quantity, order.price)
            self._reduce_lots(order.symbol, quantity)
        self._refresh_active_exits(order.symbol)

    def _triggered_exit(self, lot: PaperLot, price: Decimal) -> ExitOrder | None:
        for exit_order in lot.exit_orders:
            if exit_order.kind == "stop_loss" and price <= exit_order.trigger_price:
                return exit_order
        for exit_order in lot.exit_orders:
            if exit_order.kind == "take_profit" and price >= exit_order.trigger_price:
                return exit_order
        return None

    def _close_lot(self, lot: PaperLot, price: Decimal) -> None:
        position = self.positions.get(lot.symbol)
        if position is None:
            lot.remaining_quantity = Decimal("0")
            return
        exit_quantity = min(lot.remaining_quantity, position.quantity)
        position.realized_pnl += (price - lot.entry_price) * exit_quantity
        position.quantity -= exit_quantity
        lot.remaining_quantity -= exit_quantity
        if position.quantity <= 0:
            position.quantity = Decimal("0")
            position.avg_entry = Decimal("0")
        else:
            self._refresh_position_average(lot.symbol)

    def _reduce_lots(self, symbol: str, quantity: Decimal) -> None:
        remaining = quantity
        for lot in self.lots:
            if remaining <= 0 or lot.symbol != symbol or lot.remaining_quantity <= 0:
                continue
            reduction = min(lot.remaining_quantity, remaining)
            lot.remaining_quantity -= reduction
            remaining -= reduction
        self.lots = [lot for lot in self.lots if lot.remaining_quantity > 0]

    def _refresh_position_average(self, symbol: str) -> None:
        position = self.positions.get(symbol)
        if position is None or position.quantity <= 0:
            return
        open_lots = [lot for lot in self.lots if lot.symbol == symbol and lot.remaining_quantity > 0]
        lot_quantity = sum((lot.remaining_quantity for lot in open_lots), Decimal("0"))
        if lot_quantity == position.quantity and lot_quantity > 0:
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
    if signal.price is None or signal.side != "buy":
        return []

    exits: list[ExitOrder] = []
    if signal.stop_loss_pct is not None:
        trigger = signal.price * (Decimal("1") - signal.stop_loss_pct / Decimal("100"))
        exits.append(ExitOrder(kind="stop_loss", trigger_price=_money(trigger)))
    if signal.take_profit_pct is not None:
        trigger = signal.price * (Decimal("1") + signal.take_profit_pct / Decimal("100"))
        exits.append(ExitOrder(kind="take_profit", trigger_price=_money(trigger)))
    return exits


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
        exit_orders=[
            ExitOrder(kind=str(exit_order["kind"]), trigger_price=Decimal(str(exit_order["trigger_price"])))
            for exit_order in payload.get("exit_orders", [])
        ],
        status=str(payload.get("status") or "accepted"),
    )
