from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .signals import CryptoSignal


@dataclass(frozen=True)
class RiskConfig:
    max_order_notional: Decimal = Decimal("1000")
    max_open_notional: Decimal = Decimal("0")
    max_symbol_open_notional: Decimal = Decimal("0")
    max_position_equity_pct: Decimal = Decimal("0")
    max_risk_per_trade_pct: Decimal = Decimal("0")
    max_entry_volatility_pct: Decimal = Decimal("0")
    max_leverage: Decimal = Decimal("1")
    max_daily_loss: Decimal = Decimal("500")
    max_consecutive_losses: int = 0
    require_stop_loss: bool = True
    max_stop_loss_pct: Decimal = Decimal("0")
    max_trailing_stop_pct: Decimal = Decimal("0")
    min_reward_risk_ratio: Decimal = Decimal("0")
    min_total_reward_risk_ratio: Decimal = Decimal("0")
    max_take_profit_targets: int = 0
    max_slippage_bps: int = 100
    allowed_exchanges: set[str] = field(default_factory=lambda: {"paper"})
    allowed_symbols: set[str] = field(default_factory=set)
    blocked_symbols: set[str] = field(default_factory=set)


@dataclass
class AccountState:
    equity: Decimal = Decimal("10000")
    daily_pnl: Decimal = Decimal("0")
    open_notional: Decimal = Decimal("0")
    symbol_open_notional: Decimal = Decimal("0")
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
        trailing_stop_pct = None
    else:
        stop_loss_pct = _stop_loss_pct(signal, reasons)
        take_profit_pct = _take_profit_pct(signal, reasons)
        trailing_stop_pct = _trailing_stop_pct(signal, reasons)
        _validate_take_profit_targets(signal, reasons)
    order_notional = _order_notional(signal, reasons, account_state, stop_loss_pct)
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
        opens_position
        and order_notional is not None
        and config.max_symbol_open_notional > 0
        and account_state.symbol_open_notional + order_notional > config.max_symbol_open_notional
    ):
        reasons.append("max_symbol_open_notional_exceeded")
    if (
        order_notional is not None
        and config.max_position_equity_pct > 0
        and account_state.equity > 0
        and order_notional > account_state.equity * config.max_position_equity_pct / Decimal("100")
    ):
        reasons.append("max_position_equity_pct_exceeded")
    if (
        signal.risk_pct is not None
        and config.max_risk_per_trade_pct > 0
        and signal.risk_pct > config.max_risk_per_trade_pct
    ):
        reasons.append("max_risk_per_trade_pct_exceeded")
    if (
        signal.volatility_pct is not None
        and config.max_entry_volatility_pct > 0
        and signal.volatility_pct > config.max_entry_volatility_pct
    ):
        reasons.append("max_entry_volatility_pct_exceeded")
    if config.max_leverage > 0 and signal.leverage > config.max_leverage:
        reasons.append("max_leverage_exceeded")
    if config.max_daily_loss > 0 and account_state.daily_pnl <= -config.max_daily_loss:
        reasons.append("daily_loss_limit_exceeded")
    if config.max_consecutive_losses > 0 and account_state.consecutive_losses >= config.max_consecutive_losses:
        reasons.append("consecutive_loss_limit_exceeded")
    if stop_loss_pct is not None and config.max_stop_loss_pct > 0 and stop_loss_pct > config.max_stop_loss_pct:
        reasons.append("max_stop_loss_pct_exceeded")
    if (
        trailing_stop_pct is not None
        and config.max_trailing_stop_pct > 0
        and trailing_stop_pct > config.max_trailing_stop_pct
    ):
        reasons.append("max_trailing_stop_pct_exceeded")
    if (
        signal.trailing_activation_pct is not None
        and signal.trailing_stop_pct is None
        and signal.trailing_stop_amount is None
    ):
        reasons.append("trailing_stop_required_for_activation")
    if (
        signal.trailing_activation_price is not None
        and signal.trailing_stop_pct is None
        and signal.trailing_stop_amount is None
    ):
        reasons.append("trailing_stop_required_for_activation")
    if signal.trailing_activation_pct is not None and signal.trailing_activation_price is not None:
        reasons.append("duplicate_trailing_activation")
    if signal.trailing_stop_price is not None and signal.trailing_stop_pct is None and signal.trailing_stop_amount is None:
        reasons.append("trailing_stop_pct_required_for_price")
    if signal.trailing_stop_close_pct > 100:
        reasons.append("invalid_trailing_stop_close_pct")
    if (
        signal.trailing_stop_close_pct != 100
        and signal.trailing_stop_pct is None
        and signal.trailing_stop_amount is None
    ):
        reasons.append("trailing_stop_required_for_close_pct")
    if (
        (signal.trailing_step_pct is not None or signal.trailing_step_amount is not None)
        and signal.trailing_stop_pct is None
        and signal.trailing_stop_amount is None
    ):
        reasons.append("trailing_stop_required_for_step")
    _validate_trailing_activation_price(signal, reasons)
    if (
        signal.breakeven_trigger_pct is not None
        and stop_loss_pct is None
        and trailing_stop_pct is None
    ):
        reasons.append("breakeven_requires_protective_exit")
    if signal.breakeven_after_take_profit and stop_loss_pct is None and trailing_stop_pct is None:
        reasons.append("breakeven_requires_protective_exit")
    if signal.breakeven_after_take_profit and not signal.take_profit_targets:
        reasons.append("breakeven_after_take_profit_requires_take_profit")
    if (
        stop_loss_pct is not None
        and take_profit_pct is not None
        and config.min_reward_risk_ratio > 0
        and take_profit_pct / stop_loss_pct < config.min_reward_risk_ratio
    ):
        reasons.append("min_reward_risk_ratio_not_met")
    total_reward_risk_ratio = _total_reward_risk_ratio(signal, stop_loss_pct, reasons)
    if (
        total_reward_risk_ratio is not None
        and config.min_total_reward_risk_ratio > 0
        and total_reward_risk_ratio < config.min_total_reward_risk_ratio
    ):
        reasons.append("min_total_reward_risk_ratio_not_met")
    if config.max_take_profit_targets > 0 and len(signal.take_profit_targets) > config.max_take_profit_targets:
        reasons.append("max_take_profit_targets_exceeded")
    if signal.max_slippage_bps > config.max_slippage_bps:
        reasons.append("max_slippage_exceeded")

    return RiskDecision(approved=not reasons, reason_codes=reasons, order_notional=order_notional)


def _order_notional(
    signal: CryptoSignal,
    reasons: list[str],
    account_state: AccountState,
    stop_loss_pct: Decimal | None,
) -> Decimal | None:
    if signal.quote_amount is not None:
        return signal.quote_amount
    if signal.base_amount is not None:
        if signal.price is None:
            reasons.append("price_required_for_base_amount")
            return None
        return signal.base_amount * signal.price
    risk_budget = signal.risk_amount
    if risk_budget is None and signal.risk_pct is not None:
        risk_budget = account_state.equity * signal.risk_pct / Decimal("100")
    if risk_budget is not None:
        if stop_loss_pct is None:
            reasons.append("risk_sizing_requires_stop_loss")
            return None
        if stop_loss_pct <= 0:
            reasons.append("invalid_stop_loss_price")
            return None
        return risk_budget / (stop_loss_pct / Decimal("100"))
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
        or signal.trailing_stop_amount is not None
        or signal.trailing_stop_price is not None
        or signal.trailing_activation_price is not None
        or signal.breakeven_trigger_pct is not None
        or signal.breakeven_after_take_profit
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


def _trailing_stop_pct(signal: CryptoSignal, reasons: list[str]) -> Decimal | None:
    if signal.trailing_stop_price is None:
        if signal.trailing_stop_pct is not None:
            return signal.trailing_stop_pct
        if signal.trailing_stop_amount is None:
            return None
        if signal.price is None:
            reasons.append("price_required_for_trailing_stop_amount")
            return None
        return signal.trailing_stop_amount / signal.price * Decimal("100")
    if signal.price is None:
        reasons.append("price_required_for_trailing_stop_price")
        return signal.trailing_stop_pct
    if signal.side == "buy":
        if signal.trailing_stop_price >= signal.price:
            reasons.append("invalid_trailing_stop_price")
            return signal.trailing_stop_pct
        if signal.trailing_stop_amount is not None:
            return signal.trailing_stop_amount / signal.price * Decimal("100")
        return (signal.price - signal.trailing_stop_price) / signal.price * Decimal("100")
    if signal.trailing_stop_price <= signal.price:
        reasons.append("invalid_trailing_stop_price")
        return signal.trailing_stop_pct
    if signal.trailing_stop_amount is not None:
        return signal.trailing_stop_amount / signal.price * Decimal("100")
    return (signal.trailing_stop_price - signal.price) / signal.price * Decimal("100")


def _validate_trailing_activation_price(signal: CryptoSignal, reasons: list[str]) -> None:
    if signal.trailing_activation_price is None:
        return
    if signal.price is None:
        reasons.append("price_required_for_trailing_activation_price")
        return
    if signal.side == "buy" and signal.trailing_activation_price <= signal.price:
        reasons.append("invalid_trailing_activation_price")
    if signal.side == "sell" and signal.trailing_activation_price >= signal.price:
        reasons.append("invalid_trailing_activation_price")


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


def _total_reward_risk_ratio(
    signal: CryptoSignal,
    stop_loss_pct: Decimal | None,
    reasons: list[str],
) -> Decimal | None:
    if stop_loss_pct is None or stop_loss_pct <= 0 or not signal.take_profit_targets:
        return None
    total_reward_pct = Decimal("0")
    for target in signal.take_profit_targets:
        target_pct = target.pct
        if target_pct is None and target.trigger_price is not None:
            if signal.price is None:
                _append_reason(reasons, "price_required_for_take_profit_price")
                return None
            if signal.side == "buy":
                target_pct = (target.trigger_price - signal.price) / signal.price * Decimal("100")
            else:
                target_pct = (signal.price - target.trigger_price) / signal.price * Decimal("100")
        if target_pct is None or target_pct <= 0:
            continue
        total_reward_pct += target_pct * target.close_pct / Decimal("100")
    if total_reward_pct <= 0:
        return None
    return total_reward_pct / stop_loss_pct


def _append_reason(reasons: list[str], reason: str) -> None:
    if reason not in reasons:
        reasons.append(reason)
