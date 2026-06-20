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
    max_drawdown: Decimal = Decimal("0")
    max_runup: Decimal = Decimal("0")
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
            "risk_summary": {
                "max_drawdown": str(self.max_drawdown),
                "max_runup": str(self.max_runup),
            },
            "costs": {
                "fee_bps": str(self.fee_bps),
                "slippage_bps": str(self.slippage_bps),
            },
        }


@dataclass(frozen=True)
class StressScenarioResult:
    name: str
    summary: BacktestSummary

    def to_dict(self) -> dict:
        payload = self.summary.to_dict()
        payload["name"] = self.name
        return payload


@dataclass(frozen=True)
class StressBacktestSummary:
    scenarios: list[StressScenarioResult]

    def to_dict(self) -> dict:
        accepted = [scenario for scenario in self.scenarios if scenario.summary.accepted]
        worst_final_pnl = min((scenario.summary.final_daily_pnl for scenario in accepted), default=Decimal("0"))
        worst_drawdown = max((scenario.summary.max_drawdown for scenario in accepted), default=Decimal("0"))
        total_triggers = sum(scenario.summary.total_triggers for scenario in accepted)
        return {
            "scenario_count": len(self.scenarios),
            "accepted_count": len(accepted),
            "rejected_count": len(self.scenarios) - len(accepted),
            "worst_final_daily_pnl": str(worst_final_pnl),
            "worst_max_drawdown": str(worst_drawdown),
            "total_triggers": total_triggers,
            "scenarios": [scenario.to_dict() for scenario in self.scenarios],
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


def run_signal_stress_backtest(
    engine: TradingEngine,
    signal: CryptoSignal,
    scenarios: list[dict],
) -> StressBacktestSummary:
    """Replay one signal across named stress paths and cost assumptions."""
    results: list[StressScenarioResult] = []
    for index, scenario in enumerate(scenarios, start=1):
        name = str(scenario.get("name") or f"scenario-{index}")
        costs = scenario.get("costs")
        if "candles" in scenario:
            summary = run_signal_candle_backtest(engine, signal, scenario["candles"], costs=costs)
        else:
            summary = run_signal_backtest(engine, signal, scenario["prices"], costs=costs)
        results.append(StressScenarioResult(name=name, summary=summary))
    return StressBacktestSummary(scenarios=results)


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
            symbol_open_notional=engine.account_state.symbol_open_notional,
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
        max_drawdown=_max_drawdown([mark.daily_pnl for mark in marks]),
        max_runup=_max_runup([mark.daily_pnl for mark in marks]),
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
            "trail_after_take_profit": lot.trail_after_take_profit if exit_order.kind == "trailing_stop" else None,
            "take_profit_filled": lot.take_profit_filled if exit_order.kind == "trailing_stop" else None,
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


def _max_drawdown(values: list[Decimal]) -> Decimal:
    peak = Decimal("0")
    drawdown = Decimal("0")
    for value in values:
        peak = max(peak, value)
        drawdown = max(drawdown, peak - value)
    return drawdown


def _max_runup(values: list[Decimal]) -> Decimal:
    trough = Decimal("0")
    runup = Decimal("0")
    for value in values:
        trough = min(trough, value)
        runup = max(runup, value - trough)
    return runup
