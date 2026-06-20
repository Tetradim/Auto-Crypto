from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .execution import ExecutionResult, PaperExchange
from .idempotency import InMemoryIdempotencyStore
from .risk import AccountState, RiskConfig, evaluate_signal
from .signals import CryptoSignal


@dataclass(frozen=True)
class MarketPriceUpdate:
    symbol: str
    price: Decimal
    triggered: list[dict]
    realized_pnl_delta: Decimal
    daily_pnl: Decimal
    consecutive_losses: int
    open_notional: Decimal


class TradingEngine:
    """Coordinates validation, risk, duplicate detection, and paper execution."""

    def __init__(
        self,
        *,
        exchange: PaperExchange | None = None,
        risk_config: RiskConfig | None = None,
        account_state: AccountState | None = None,
        idempotency: InMemoryIdempotencyStore | None = None,
    ) -> None:
        self.exchange = exchange or PaperExchange()
        self.risk_config = risk_config or RiskConfig()
        self.account_state = account_state or AccountState()
        self.idempotency = idempotency or InMemoryIdempotencyStore()
        self.halted = False
        self.halt_reason = ""

    def halt(self, reason: str = "") -> None:
        self.halted = True
        self.halt_reason = reason

    def resume(self) -> None:
        self.halted = False
        self.halt_reason = ""

    def process_signal(self, signal: CryptoSignal) -> ExecutionResult:
        self.account_state.open_notional = self.exchange.open_notional()
        self.account_state.symbol_open_notional = self.exchange.symbol_open_notional(signal.symbol)
        if self.halted:
            decision = evaluate_signal(signal, self.risk_config, self.account_state)
            return ExecutionResult(status="halted", decision=decision, reason=self.halt_reason or "trading_halted")

        decision = evaluate_signal(signal, self.risk_config, self.account_state)
        if not decision.approved:
            return ExecutionResult(status="rejected", decision=decision, reason="risk_rejected")

        if not self.idempotency.claim(signal.signal_id):
            return ExecutionResult(status="duplicate", decision=decision, reason="duplicate_signal")

        order = self.exchange.submit(signal, decision)
        self.account_state.open_notional = self.exchange.open_notional()
        return ExecutionResult(status="accepted", decision=decision, order=order)

    def mark_price(self, symbol: str, price: Decimal) -> MarketPriceUpdate:
        realized_before = _position_realized_pnl(self.exchange, symbol)
        triggered = self.exchange.update_price(symbol, price)
        realized_pnl_delta = Decimal("0")
        if triggered:
            realized_pnl_delta = _position_realized_pnl(self.exchange, symbol) - realized_before
            self.account_state.daily_pnl += realized_pnl_delta
            if realized_pnl_delta < 0:
                self.account_state.consecutive_losses += 1
            elif realized_pnl_delta > 0:
                self.account_state.consecutive_losses = 0
        self.account_state.open_notional = self.exchange.open_notional()
        self.account_state.symbol_open_notional = self.exchange.symbol_open_notional(symbol)
        return MarketPriceUpdate(
            symbol=symbol,
            price=price,
            triggered=triggered,
            realized_pnl_delta=realized_pnl_delta,
            daily_pnl=self.account_state.daily_pnl,
            consecutive_losses=self.account_state.consecutive_losses,
            open_notional=self.account_state.open_notional,
        )


def _position_realized_pnl(exchange: PaperExchange, symbol: str) -> Decimal:
    position = exchange.positions.get(symbol)
    return position.realized_pnl if position else Decimal("0")
