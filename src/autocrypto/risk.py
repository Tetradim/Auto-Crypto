from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .signals import CryptoSignal


@dataclass(frozen=True)
class RiskConfig:
    max_order_notional: Decimal = Decimal("1000")
    max_open_notional: Decimal = Decimal("0")
    max_position_equity_pct: Decimal = Decimal("0")
    max_leverage: Decimal = Decimal("1")
    max_daily_loss: Decimal = Decimal("500")
    max_consecutive_losses: int = 0
    require_stop_loss: bool = True
    max_stop_loss_pct: Decimal = Decimal("0")
    max_trailing_stop_pct: Decimal = Decimal("0")
    min_reward_risk_ratio: Decimal = Decimal("0")
    max_slippage_bps: int = 100
    allowed_exchanges: set[str] = field(default_factory=lambda: {"paper"})
    allowed_symbols: set[str] = field(default_factory=set)
    blocked_symbols: set[str] = field(default_factory=set)


@dataclass
class AccountState:
    equity: Decimal = Decimal("10000")
    daily_pnl: Decimal = Decimal("0")
    open_notional: Decimal = Decimal("0")
    consecutive_losses: int = 0


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
    opens_position = _opens_position(signal)
    if signal.reduce_only:
        stop_loss_pct = None
        take_profit_pct = None
    else:
        stop_loss_pct = _stop_loss_pct(signal, reasons)
        take_profit_pct = _take_profit_pct(signal, reasons)
        _validate_take_profit_targets(signal, reasons)
    if config.require_stop_loss and opens_position and stop_loss_pct is None:
        reasons.append("stop_loss_required")
    if order_notional is not None and config.max_order_notional > 0 and order_notional > config.max_order_notional:
        reasons.append("max_order_notional_exceeded")
    if (
        opens_position
        and order_notional is not None
        and config.max_open_notional > 0
        and account_state.open_notional + order_notional > config.max_open_notional
    ):
        reasons.append("max_open_notional_exceeded")
    if (
        order_notional is not None
        and config.max_position_equity_pct > 0
        and account_state.equity > 0
        and order_notional > account_state.equity * config.max_position_equity_pct / Decimal("100")
    ):
        reasons.append("max_position_equity_pct_exceeded")
    if config.max_leverage > 0 and signal.leverage > config.max_leverage:
        reasons.append("max_leverage_exceeded")
    if config.max_daily_loss > 0 and account_state.daily_pnl <= -config.max_daily_loss:
        reasons.append("daily_loss_limit_exceeded")
    if config.max_consecutive_losses > 0 and account_state.consecutive_losses >= config.max_consecutive_losses:
        reasons.append("consecutive_loss_limit_exceeded")
    if stop_loss_pct is not None and config.max_stop_loss_pct > 0 and stop_loss_pct > config.max_stop_loss_pct:
        reasons.append("max_stop_loss_pct_exceeded")
    if (
        signal.trailing_stop_pct is not None
        and config.max_trailing_stop_pct > 0
        and signal.trailing_stop_pct > config.max_trailing_stop_pct
    ):
        reasons.append("max_trailing_stop_pct_exceeded")
    if signal.trailing_activation_pct is not None and signal.trailing_stop_pct is None:
        reasons.append("trailing_stop_required_for_activation")
    if (
        stop_loss_pct is not None
        and take_profit_pct is not None
        and config.min_reward_risk_ratio > 0
        and take_profit_pct / stop_loss_pct < config.min_reward_risk_ratio
    ):
        reasons.append("min_reward_risk_ratio_not_met")
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


def _opens_position(signal: CryptoSignal) -> bool:
    if signal.reduce_only:
        return False
    if signal.side == "buy":
        return True
    return signal.side == "sell" and (
        signal.stop_loss_pct is not None
        or signal.stop_loss_price is not None
        or bool(signal.take_profit_targets)
        or signal.take_profit_price is not None
        or signal.trailing_stop_pct is not None
        or signal.breakeven_trigger_pct is not None
    )


def _stop_loss_pct(signal: CryptoSignal, reasons: list[str]) -> Decimal | None:
    if signal.stop_loss_pct is not None:
        return signal.stop_loss_pct
    if signal.stop_loss_price is None:
        return None
    if signal.price is None:
        reasons.append("price_required_for_stop_loss_price")
        return None
    if signal.side == "buy":
        if signal.stop_loss_price >= signal.price:
            reasons.append("invalid_stop_loss_price")
            return None
        return (signal.price - signal.stop_loss_price) / signal.price * Decimal("100")
    if signal.stop_loss_price <= signal.price:
        reasons.append("invalid_stop_loss_price")
        return None
    return (signal.stop_loss_price - signal.price) / signal.price * Decimal("100")


def _take_profit_pct(signal: CryptoSignal, reasons: list[str]) -> Decimal | None:
    if signal.take_profit_pct is not None:
        return signal.take_profit_pct
    target_price = signal.take_profit_price
    if target_price is None and signal.take_profit_targets:
        target_price = signal.take_profit_targets[0].trigger_price
    if target_price is None:
        return None
    if signal.price is None:
        reasons.append("price_required_for_take_profit_price")
        return None
    if signal.side == "buy":
        if target_price <= signal.price:
            reasons.append("invalid_take_profit_price")
            return None
        return (target_price - signal.price) / signal.price * Decimal("100")
    if target_price >= signal.price:
        reasons.append("invalid_take_profit_price")
        return None
    return (signal.price - target_price) / signal.price * Decimal("100")


def _validate_take_profit_targets(signal: CryptoSignal, reasons: list[str]) -> None:
    if not signal.take_profit_targets:
        return
    if signal.price is None:
        _append_reason(reasons, "price_required_for_take_profit_price")
        return
    for target in signal.take_profit_targets:
        if target.trigger_price is None:
            continue
        if signal.side == "buy" and target.trigger_price <= signal.price:
            _append_reason(reasons, "invalid_take_profit_price")
        if signal.side == "sell" and target.trigger_price >= signal.price:
            _append_reason(reasons, "invalid_take_profit_price")


def _append_reason(reasons: list[str], reason: str) -> None:
    if reason not in reasons:
        reasons.append(reason)
