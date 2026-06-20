from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .engine import TradingEngine
from .execution import ExecutionCostConfig, PaperExchange
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
    label: str | None = None
    mfe: Decimal | None = None
    mae: Decimal | None = None


@dataclass(frozen=True)
class BacktestSummary:
    status: str
    accepted: bool
    marks: list[BacktestMark]
    final_daily_pnl: Decimal
    final_open_notional: Decimal
    final_positions: list[dict]
    total_triggers: int
    fee_bps: Decimal = Decimal("0")
    slippage_bps: Decimal = Decimal("0")

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
                    "label": mark.label,
                    "mfe": str(mark.mfe) if mark.mfe is not None else None,
                    "mae": str(mark.mae) if mark.mae is not None else None,
                }
                for mark in self.marks
            ],
            "final_daily_pnl": str(self.final_daily_pnl),
            "final_open_notional": str(self.final_open_notional),
            "final_positions": self.final_positions,
            "total_triggers": self.total_triggers,
            "costs": {
                "fee_bps": str(self.fee_bps),
                "slippage_bps": str(self.slippage_bps),
            },
        }


def run_signal_backtest(
    engine: TradingEngine,
    signal: CryptoSignal,
    prices: list[Decimal],
    *,
    costs: ExecutionCostConfig | None = None,
) -> BacktestSummary:
    """Replay one normalized signal and a mark-price path against an isolated paper engine."""
    return _run_backtest_path(engine, signal, [(None, [price]) for price in prices], costs=costs)


def run_signal_candle_backtest(
    engine: TradingEngine,
    signal: CryptoSignal,
    candles: list[dict[str, Decimal]],
    *,
    costs: ExecutionCostConfig | None = None,
) -> BacktestSummary:
    """Replay OHLC ranges with adverse-first intrabar sequencing.

    If both a protective exit and a target could trigger inside the same candle,
    the paper path marks the adverse side first to keep backtests conservative.
    """
    path: list[tuple[str | None, list[Decimal]]] = []
    for index, candle in enumerate(candles, start=1):
        label = str(candle.get("label") or index)
        if signal.side == "buy":
            prices = [candle["low"], candle["high"], candle["close"]]
        else:
            prices = [candle["high"], candle["low"], candle["close"]]
        path.append((label, prices))
    return _run_backtest_path(engine, signal, path, costs=costs)


def _run_backtest_path(
    engine: TradingEngine,
    signal: CryptoSignal,
    path: list[tuple[str | None, list[Decimal]]],
    *,
    costs: ExecutionCostConfig | None,
) -> BacktestSummary:
    costs = costs or ExecutionCostConfig()
    sandbox = TradingEngine(
        exchange=PaperExchange(costs=costs),
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
    mfe: Decimal | None = None
    mae: Decimal | None = None
    if result.status == "accepted":
        for label, prices in path:
            candle_triggered: list[dict] = []
            realized_pnl_delta = Decimal("0")
            last_update = None
            for price in prices:
                mfe, mae = _update_excursion(signal, price, mfe=mfe, mae=mae)
                last_update = sandbox.mark_price(signal.symbol, price)
                candle_triggered.extend(last_update.triggered)
                realized_pnl_delta += last_update.realized_pnl_delta
            if last_update is None:
                continue
            marks.append(
                BacktestMark(
                    price=prices[-1],
                    triggered=candle_triggered,
                    active_exits=_active_exits_snapshot(sandbox.exchange.lots),
                    open_notional=last_update.open_notional,
                    realized_pnl_delta=realized_pnl_delta,
                    daily_pnl=last_update.daily_pnl,
                    label=label,
                    mfe=mfe,
                    mae=mae,
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
        fee_bps=costs.fee_bps,
        slippage_bps=costs.slippage_bps,
    )


def _update_excursion(
    signal: CryptoSignal,
    price: Decimal,
    *,
    mfe: Decimal | None,
    mae: Decimal | None,
) -> tuple[Decimal | None, Decimal | None]:
    if signal.price is None:
        return mfe, mae
    if signal.side == "buy":
        move_pct = (price - signal.price) / signal.price * Decimal("100")
    else:
        move_pct = (signal.price - price) / signal.price * Decimal("100")
    return (
        move_pct if mfe is None else max(mfe, move_pct),
        move_pct if mae is None else min(mae, move_pct),
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
            "initial_trailing_stop_price": str(lot.trailing_stop_price)
            if exit_order.kind == "trailing_stop" and lot.trailing_stop_price
            else None,
            "trailing_stop_pct": str(lot.trailing_stop_pct)
            if exit_order.kind == "trailing_stop" and lot.trailing_stop_pct
            else None,
            "trailing_stop_amount": str(lot.trailing_stop_amount)
            if exit_order.kind == "trailing_stop" and lot.trailing_stop_amount
            else None,
            "trailing_step_pct": str(lot.trailing_step_pct)
            if exit_order.kind == "trailing_stop" and lot.trailing_step_pct
            else None,
            "trailing_step_amount": str(lot.trailing_step_amount)
            if exit_order.kind == "trailing_stop" and lot.trailing_step_amount
            else None,
            "trailing_activation_pct": str(lot.trailing_activation_pct)
            if exit_order.kind == "trailing_stop" and lot.trailing_activation_pct
            else None,
            "trailing_activation_price": str(lot.trailing_activation_price)
            if exit_order.kind == "trailing_stop" and lot.trailing_activation_price
            else None,
            "trailing_activated": lot.trailing_activated if exit_order.kind == "trailing_stop" else None,
            "high_water_mark": str(lot.high_water_mark) if exit_order.kind == "trailing_stop" and lot.high_water_mark else None,
            "low_water_mark": str(lot.low_water_mark) if exit_order.kind == "trailing_stop" and lot.low_water_mark else None,
            "breakeven_after_take_profit": lot.breakeven_after_take_profit,
            "breakeven_applied": lot.breakeven_applied,
            "max_hold_marks": lot.max_hold_marks if exit_order.kind == "time_exit" else None,
            "marks_seen": lot.marks_seen if exit_order.kind == "time_exit" else None,
            "marks_remaining": max(lot.max_hold_marks - lot.marks_seen, 0)
            if exit_order.kind == "time_exit" and lot.max_hold_marks is not None
            else None,
            "remaining_quantity": str(lot.remaining_quantity),
        }
        for lot in sorted(lots, key=lambda item: (item.symbol, item.signal_id))
        if lot.remaining_quantity > 0
        for exit_order in lot.exit_orders
    ]
