from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .brackets import active_exit_payload
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
    final_mark_price: Decimal | None = None
    final_unrealized_pnl: Decimal = Decimal("0")
    final_total_pnl: Decimal = Decimal("0")
    final_close_requested: bool = False
    final_close_triggers: list[dict] = field(default_factory=list)

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
            "final_mark_price": str(self.final_mark_price) if self.final_mark_price is not None else None,
            "final_unrealized_pnl": str(self.final_unrealized_pnl),
            "final_total_pnl": str(self.final_total_pnl),
            "final_close_requested": self.final_close_requested,
            "final_close_triggers": self.final_close_triggers,
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
    close_final_positions: bool = False,
) -> BacktestSummary:
    """Replay one normalized signal and a mark-price path against an isolated paper engine."""
    return _run_backtest_path(
        engine,
        signal,
        [(None, [price]) for price in prices],
        costs=costs,
        close_final_positions=close_final_positions,
    )


def run_signal_candle_backtest(
    engine: TradingEngine,
    signal: CryptoSignal,
    candles: list[dict[str, Decimal]],
    *,
    costs: ExecutionCostConfig | None = None,
    close_final_positions: bool = False,
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
    return _run_backtest_path(engine, signal, path, costs=costs, close_final_positions=close_final_positions)


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
            summary = run_signal_candle_backtest(
                engine,
                signal,
                scenario["candles"],
                costs=costs,
                close_final_positions=bool(scenario.get("close_final_positions", False)),
            )
        else:
            summary = run_signal_backtest(
                engine,
                signal,
                scenario["prices"],
                costs=costs,
                close_final_positions=bool(scenario.get("close_final_positions", False)),
            )
        results.append(StressScenarioResult(name=name, summary=summary))
    return StressBacktestSummary(scenarios=results)


def _run_backtest_path(
    engine: TradingEngine,
    signal: CryptoSignal,
    path: list[tuple[str | None, list[Decimal]]],
    *,
    costs: ExecutionCostConfig | None,
    close_final_positions: bool,
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
    final_mark_price: Decimal | None = None
    final_close_triggers: list[dict] = []
    if result.status == "accepted":
        for label, prices in path:
            candle_triggered: list[dict] = []
            realized_pnl_delta = Decimal("0")
            last_update = None
            for price in prices:
                final_mark_price = price
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
        if close_final_positions and final_mark_price is not None:
            final_close_triggers = _close_open_lots_at_mark(sandbox, final_mark_price)
    final_unrealized_pnl = _unrealized_pnl(sandbox.exchange.lots, final_mark_price)
    return BacktestSummary(
        status=result.status,
        accepted=result.status == "accepted",
        marks=marks,
        final_daily_pnl=sandbox.account_state.daily_pnl,
        final_open_notional=sandbox.account_state.open_notional,
        final_positions=sandbox.exchange.list_positions(),
        total_triggers=sum(len(mark.triggered) for mark in marks) + len(final_close_triggers),
        max_drawdown=_max_drawdown([mark.daily_pnl for mark in marks]),
        max_runup=_max_runup([mark.daily_pnl for mark in marks]),
        fee_bps=costs.fee_bps,
        slippage_bps=costs.slippage_bps,
        final_mark_price=final_mark_price,
        final_unrealized_pnl=final_unrealized_pnl,
        final_total_pnl=sandbox.account_state.daily_pnl + final_unrealized_pnl,
        final_close_requested=close_final_positions,
        final_close_triggers=final_close_triggers,
    )


def _close_open_lots_at_mark(sandbox: TradingEngine, price: Decimal) -> list[dict]:
    triggers: list[dict] = []
    for lot in list(sandbox.exchange.lots):
        if lot.remaining_quantity <= 0:
            continue
        realized_before = _position_realized_pnl(sandbox.exchange, lot.symbol)
        order = sandbox.exchange.close_bracket(lot.signal_id, price, reason="backtest final close")
        if order is None:
            continue
        realized_delta = _position_realized_pnl(sandbox.exchange, lot.symbol) - realized_before
        sandbox.account_state.daily_pnl += realized_delta
        sandbox.account_state.open_notional = sandbox.exchange.open_notional()
        triggers.append(
            {
                "symbol": lot.symbol,
                "kind": "final_close",
                "price": str(order.price),
                "quantity": str(order.notional / order.price) if order.price else "0",
                "realized_pnl_delta": str(realized_delta),
            }
        )
    return triggers


def _unrealized_pnl(lots: list, mark_price: Decimal | None) -> Decimal:
    if mark_price is None:
        return Decimal("0")
    total = Decimal("0")
    for lot in lots:
        if lot.remaining_quantity <= 0:
            continue
        if lot.direction == "long":
            total += (mark_price - lot.entry_price) * lot.remaining_quantity
        else:
            total += (lot.entry_price - mark_price) * lot.remaining_quantity
    return total


def _position_realized_pnl(exchange: PaperExchange, symbol: str) -> Decimal:
    position = exchange.positions.get(symbol)
    return position.realized_pnl if position else Decimal("0")


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
        active_exit_payload(
            lot,
            exit_order,
            bool_style="native",
            include_entry=False,
            include_close_pct=False,
            include_oca_group=False,
        )
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
