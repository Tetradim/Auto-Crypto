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
        }


def run_signal_backtest(engine: TradingEngine, signal: CryptoSignal, prices: list[Decimal]) -> BacktestSummary:
    """Replay one normalized signal and a mark-price path against an isolated paper engine."""
    return _run_backtest_path(engine, signal, [(None, [price]) for price in prices])


def run_signal_candle_backtest(
    engine: TradingEngine,
    signal: CryptoSignal,
    candles: list[dict[str, Decimal]],
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
    return _run_backtest_path(engine, signal, path)


def _run_backtest_path(
    engine: TradingEngine,
    signal: CryptoSignal,
    path: list[tuple[str | None, list[Decimal]]],
) -> BacktestSummary:
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
            "trailing_activated": lot.trailing_activated if exit_order.kind == "trailing_stop" else None,
            "high_water_mark": str(lot.high_water_mark) if exit_order.kind == "trailing_stop" and lot.high_water_mark else None,
            "low_water_mark": str(lot.low_water_mark) if exit_order.kind == "trailing_stop" and lot.low_water_mark else None,
            "remaining_quantity": str(lot.remaining_quantity),
        }
        for lot in sorted(lots, key=lambda item: (item.symbol, item.signal_id))
        if lot.remaining_quantity > 0
        for exit_order in lot.exit_orders
    ]
