from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .engine import TradingEngine
from .risk import AccountState
from .signals import CryptoSignal


@dataclass(frozen=True)
class BacktestMark:
    price: Decimal
    triggered: list[dict]
    active_exits: list[dict]
    open_notional: Decimal
    realized_pnl_delta: Decimal
    daily_pnl: Decimal


@dataclass(frozen=True)
class BacktestSummary:
    status: str
    accepted: bool
    marks: list[BacktestMark]
    final_daily_pnl: Decimal
    final_open_notional: Decimal
    final_positions: list[dict]
    total_triggers: int

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "accepted": self.accepted,
            "marks": [
                {
                    "price": str(mark.price),
                    "triggered": mark.triggered,
                    "active_exits": mark.active_exits,
                    "open_notional": str(mark.open_notional),
                    "realized_pnl_delta": str(mark.realized_pnl_delta),
                    "daily_pnl": str(mark.daily_pnl),
                }
                for mark in self.marks
            ],
            "final_daily_pnl": str(self.final_daily_pnl),
            "final_open_notional": str(self.final_open_notional),
            "final_positions": self.final_positions,
            "total_triggers": self.total_triggers,
        }


def run_signal_backtest(engine: TradingEngine, signal: CryptoSignal, prices: list[Decimal]) -> BacktestSummary:
    """Replay one normalized signal and a mark-price path against an isolated paper engine."""
    sandbox = TradingEngine(
        risk_config=engine.risk_config,
        account_state=AccountState(
            equity=engine.account_state.equity,
            daily_pnl=engine.account_state.daily_pnl,
            open_notional=engine.account_state.open_notional,
            consecutive_losses=engine.account_state.consecutive_losses,
        ),
    )
    result = sandbox.process_signal(signal)
    marks: list[BacktestMark] = []
    if result.status == "accepted":
        for price in prices:
            update = sandbox.mark_price(signal.symbol, price)
            marks.append(
                BacktestMark(
                    price=price,
                    triggered=update.triggered,
                    active_exits=_active_exits_snapshot(sandbox.exchange.lots),
                    open_notional=update.open_notional,
                    realized_pnl_delta=update.realized_pnl_delta,
                    daily_pnl=update.daily_pnl,
                )
            )
    return BacktestSummary(
        status=result.status,
        accepted=result.status == "accepted",
        marks=marks,
        final_daily_pnl=sandbox.account_state.daily_pnl,
        final_open_notional=sandbox.account_state.open_notional,
        final_positions=sandbox.exchange.list_positions(),
        total_triggers=sum(len(mark.triggered) for mark in marks),
    )


def _active_exits_snapshot(lots: list) -> list[dict]:
    return [
        {
            "signal_id": lot.signal_id,
            "symbol": lot.symbol,
            "direction": lot.direction,
            "kind": exit_order.kind,
            "trigger_price": str(exit_order.trigger_price),
            "status": exit_order.status,
            "trailing_activated": lot.trailing_activated if exit_order.kind == "trailing_stop" else None,
            "high_water_mark": str(lot.high_water_mark) if exit_order.kind == "trailing_stop" and lot.high_water_mark else None,
            "low_water_mark": str(lot.low_water_mark) if exit_order.kind == "trailing_stop" and lot.low_water_mark else None,
            "remaining_quantity": str(lot.remaining_quantity),
        }
        for lot in sorted(lots, key=lambda item: (item.symbol, item.signal_id))
        if lot.remaining_quantity > 0
        for exit_order in lot.exit_orders
    ]
