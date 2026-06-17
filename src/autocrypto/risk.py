from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .signals import CryptoSignal


@dataclass(frozen=True)
class RiskConfig:
    max_order_notional: Decimal = Decimal("1000")
    max_leverage: Decimal = Decimal("1")
    max_daily_loss: Decimal = Decimal("500")
    require_stop_loss: bool = True
    max_slippage_bps: int = 100
    allowed_exchanges: set[str] = field(default_factory=lambda: {"paper"})
    allowed_symbols: set[str] = field(default_factory=set)
    blocked_symbols: set[str] = field(default_factory=set)


@dataclass
class AccountState:
    equity: Decimal = Decimal("10000")
    daily_pnl: Decimal = Decimal("0")
    open_notional: Decimal = Decimal("0")


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reason_codes: list[str]
    order_notional: Decimal | None = None


def evaluate_signal(
    signal: CryptoSignal,
    config: RiskConfig,
    account_state: AccountState,
) -> RiskDecision:
    reasons: list[str] = []
    order_notional = _order_notional(signal, reasons)

    if config.allowed_symbols and signal.symbol not in config.allowed_symbols:
        reasons.append("symbol_not_allowed")
    if signal.symbol in config.blocked_symbols:
        reasons.append("symbol_blocked")
    if config.allowed_exchanges and signal.exchange not in config.allowed_exchanges:
        reasons.append("exchange_not_allowed")
    if config.require_stop_loss and signal.side == "buy" and signal.stop_loss_pct is None:
        reasons.append("stop_loss_required")
    if order_notional is not None and config.max_order_notional > 0 and order_notional > config.max_order_notional:
        reasons.append("max_order_notional_exceeded")
    if config.max_leverage > 0 and signal.leverage > config.max_leverage:
        reasons.append("max_leverage_exceeded")
    if config.max_daily_loss > 0 and account_state.daily_pnl <= -config.max_daily_loss:
        reasons.append("daily_loss_limit_exceeded")
    if signal.max_slippage_bps > config.max_slippage_bps:
        reasons.append("max_slippage_exceeded")

    return RiskDecision(approved=not reasons, reason_codes=reasons, order_notional=order_notional)


def _order_notional(signal: CryptoSignal, reasons: list[str]) -> Decimal | None:
    if signal.quote_amount is not None:
        return signal.quote_amount
    if signal.base_amount is not None:
        if signal.price is None:
            reasons.append("price_required_for_base_amount")
            return None
        return signal.base_amount * signal.price
    reasons.append("order_size_required")
    return None
