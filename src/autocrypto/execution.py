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

    def submit(self, signal: CryptoSignal, decision: RiskDecision) -> PaperOrder:
        if decision.order_notional is None:
            raise ValueError("approved order requires notional")
        if signal.price is not None:
            self._apply_fill(signal, decision.order_notional)
        order = PaperOrder(
            order_id=f"paper-{signal.signal_id}",
            signal_id=signal.signal_id,
            mode="paper",
            exchange=signal.exchange,
            symbol=signal.symbol,
            side=signal.side,
            notional=decision.order_notional,
            price=signal.price,
            exit_orders=build_exit_orders(signal),
        )
        self.orders.append(order)
        return order

    def list_positions(self) -> list[dict]:
        return [
            position.to_dict()
            for position in self.positions.values()
            if position.quantity > 0 or position.realized_pnl != 0
        ]

    def _apply_fill(self, signal: CryptoSignal, notional: Decimal) -> None:
        if signal.price is None:
            return
        quantity = signal.base_amount if signal.base_amount is not None else notional / signal.price
        position = self.positions.setdefault(signal.symbol, PaperPosition(symbol=signal.symbol))
        if signal.side == "buy":
            position.buy(quantity, signal.price)
        elif signal.side == "sell":
            position.sell(quantity, signal.price)


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
