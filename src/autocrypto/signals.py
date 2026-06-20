from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any


class SignalValidationError(ValueError):
    """Raised when an incoming alert cannot be converted to a safe signal."""


@dataclass(frozen=True)
class TakeProfitTarget:
    pct: Decimal | None = None
    trigger_price: Decimal | None = None
    close_pct: Decimal = Decimal("100")


@dataclass(frozen=True)
class CryptoSignal:
    signal_id: str
    source: str
    symbol: str
    side: str
    exchange: str = "paper"
    market_type: str = "spot"
    quote_amount: Decimal | None = None
    base_amount: Decimal | None = None
    risk_amount: Decimal | None = None
    risk_pct: Decimal | None = None
    price: Decimal | None = None
    stop_loss_pct: Decimal | None = None
    stop_loss_price: Decimal | None = None
    take_profit_pct: Decimal | None = None
    take_profit_price: Decimal | None = None
    take_profit_targets: tuple[TakeProfitTarget, ...] = ()
    trailing_stop_pct: Decimal | None = None
    trailing_stop_amount: Decimal | None = None
    trailing_stop_price: Decimal | None = None
    trailing_step_pct: Decimal | None = None
    trailing_step_amount: Decimal | None = None
    trailing_activation_pct: Decimal | None = None
    trailing_activation_price: Decimal | None = None
    breakeven_trigger_pct: Decimal | None = None
    breakeven_after_take_profit: bool = False
    leverage: Decimal = Decimal("1")
    max_slippage_bps: int = 100
    reduce_only: bool = False
    strategy_id: str = "manual"
    raw_payload: dict[str, Any] = field(default_factory=dict)


SIDE_ALIASES = {
    "buy": "buy",
    "long": "buy",
    "entry": "buy",
    "open_long": "buy",
    "sell": "sell",
    "short": "sell",
    "close": "sell",
    "close_long": "sell",
    "close_short": "buy",
}

QUOTE_SUFFIXES = ("USDT", "USDC", "USD", "BTC", "ETH", "EUR")
FORBIDDEN_ACTIONS = {"withdraw", "transfer", "internal_transfer", "deposit"}


def normalize_signal(payload: dict[str, Any], *, source: str) -> CryptoSignal:
    if not isinstance(payload, dict):
        raise SignalValidationError("signal payload must be an object")
    bracket = _bracket_payload(payload)

    raw_side = payload.get("side") or payload.get("action")
    side = _normalize_side(raw_side)
    reduce_only = _bool(payload.get("reduce_only"), default=False) or _is_close_side(raw_side)
    symbol = normalize_symbol(payload.get("symbol") or payload.get("ticker") or payload.get("pair"))
    quote_amount = _optional_positive_decimal(payload.get("quote_amount") or payload.get("notional"))
    base_amount = _optional_positive_decimal(payload.get("base_amount") or payload.get("quantity") or payload.get("qty"))
    risk_amount = _optional_positive_decimal(payload.get("risk_amount") or payload.get("risk_quote_amount"))
    risk_pct = _optional_positive_decimal(payload.get("risk_pct") or payload.get("risk_percent"))

    if quote_amount is None and base_amount is None and risk_amount is None and risk_pct is None:
        raise SignalValidationError("signal requires quote_amount, base_amount, risk_amount, or risk_pct")

    price = _optional_positive_decimal(payload.get("price") or payload.get("entry_price") or payload.get("limit_price"))
    stop_loss_pct = _optional_positive_decimal(_field(payload, bracket, "stop_loss_pct"))
    stop_loss_price = _optional_positive_decimal(_field(payload, bracket, "stop_loss_price", "stop_price"))
    take_profit_pct = _optional_positive_decimal(_field(payload, bracket, "take_profit_pct"))
    take_profit_price = _optional_positive_decimal(
        _field(payload, bracket, "take_profit_price", "target_price")
    )
    take_profit_targets = _take_profit_targets(_field(payload, bracket, "take_profit_targets"), take_profit_pct, take_profit_price)
    take_profit_targets = _sort_take_profit_targets(take_profit_targets, side=side, entry_price=price)
    if take_profit_pct is None and take_profit_targets:
        take_profit_pct = take_profit_targets[0].pct
    trailing_stop_pct = _optional_positive_decimal(_field(payload, bracket, "trailing_stop_pct"))
    trailing_stop_amount = _optional_positive_decimal(
        _field(payload, bracket, "trailing_stop_amount", "trail_amount")
    )
    trailing_stop_price = _optional_positive_decimal(
        _field(payload, bracket, "trailing_stop_price", "trail_price")
    )
    trailing_step_pct = _optional_positive_decimal(_field(payload, bracket, "trailing_step_pct", "trail_step_pct"))
    trailing_step_amount = _optional_positive_decimal(
        _field(payload, bracket, "trailing_step_amount", "trail_step_amount")
    )
    trailing_activation_pct = _optional_positive_decimal(
        _field(payload, bracket, "trailing_activation_pct", "trail_activation_pct")
    )
    trailing_activation_price = _optional_positive_decimal(
        _field(payload, bracket, "trailing_activation_price", "trail_activation_price", "activation_price")
    )
    breakeven_trigger_pct = _optional_positive_decimal(_field(payload, bracket, "breakeven_trigger_pct"))
    breakeven_after_take_profit = _bool(
        _field(payload, bracket, "breakeven_after_take_profit", "move_stop_to_breakeven_after_tp"),
        default=False,
    )
    leverage = _optional_positive_decimal(payload.get("leverage")) or Decimal("1")
    max_slippage_bps = _non_negative_int(payload.get("max_slippage_bps"), default=100)
    exchange = str(payload.get("exchange") or payload.get("venue") or "paper").strip().lower()
    market_type = str(payload.get("market_type") or "spot").strip().lower()
    strategy_id = str(payload.get("strategy_id") or payload.get("strategy") or "manual").strip()

    canonical = {
        "source": source,
        "symbol": symbol,
        "side": side,
        "exchange": exchange,
        "market_type": market_type,
        "quote_amount": str(quote_amount) if quote_amount is not None else None,
        "base_amount": str(base_amount) if base_amount is not None else None,
        "risk_amount": str(risk_amount) if risk_amount is not None else None,
        "risk_pct": str(risk_pct) if risk_pct is not None else None,
        "price": str(price) if price is not None else None,
        "stop_loss_pct": str(stop_loss_pct) if stop_loss_pct is not None else None,
        "stop_loss_price": str(stop_loss_price) if stop_loss_price is not None else None,
        "take_profit_pct": str(take_profit_pct) if take_profit_pct is not None else None,
        "take_profit_price": str(take_profit_price) if take_profit_price is not None else None,
        "take_profit_targets": [
            {
                "pct": str(target.pct) if target.pct is not None else None,
                "trigger_price": str(target.trigger_price) if target.trigger_price is not None else None,
                "close_pct": str(target.close_pct),
            }
            for target in take_profit_targets
        ],
        "trailing_stop_pct": str(trailing_stop_pct) if trailing_stop_pct is not None else None,
        "trailing_stop_amount": str(trailing_stop_amount) if trailing_stop_amount is not None else None,
        "trailing_stop_price": str(trailing_stop_price) if trailing_stop_price is not None else None,
        "trailing_step_pct": str(trailing_step_pct) if trailing_step_pct is not None else None,
        "trailing_step_amount": str(trailing_step_amount) if trailing_step_amount is not None else None,
        "trailing_activation_pct": str(trailing_activation_pct) if trailing_activation_pct is not None else None,
        "trailing_activation_price": str(trailing_activation_price)
        if trailing_activation_price is not None
        else None,
        "breakeven_trigger_pct": str(breakeven_trigger_pct) if breakeven_trigger_pct is not None else None,
        "breakeven_after_take_profit": breakeven_after_take_profit,
        "leverage": str(leverage),
        "max_slippage_bps": max_slippage_bps,
        "reduce_only": reduce_only,
        "strategy_id": strategy_id,
    }
    signal_id = str(payload.get("signal_id") or _fingerprint(canonical))

    return CryptoSignal(
        signal_id=signal_id,
        source=source,
        symbol=symbol,
        side=side,
        exchange=exchange,
        market_type=market_type,
        quote_amount=quote_amount,
        base_amount=base_amount,
        risk_amount=risk_amount,
        risk_pct=risk_pct,
        price=price,
        stop_loss_pct=stop_loss_pct,
        stop_loss_price=stop_loss_price,
        take_profit_pct=take_profit_pct,
        take_profit_price=take_profit_price,
        take_profit_targets=take_profit_targets,
        trailing_stop_pct=trailing_stop_pct,
        trailing_stop_amount=trailing_stop_amount,
        trailing_stop_price=trailing_stop_price,
        trailing_step_pct=trailing_step_pct,
        trailing_step_amount=trailing_step_amount,
        trailing_activation_pct=trailing_activation_pct,
        trailing_activation_price=trailing_activation_price,
        breakeven_trigger_pct=breakeven_trigger_pct,
        breakeven_after_take_profit=breakeven_after_take_profit,
        leverage=leverage,
        max_slippage_bps=max_slippage_bps,
        reduce_only=reduce_only,
        strategy_id=strategy_id,
        raw_payload=dict(payload),
    )


def _bracket_payload(payload: dict[str, Any]) -> dict[str, Any]:
    bracket = payload.get("bracket") or payload.get("bracket_order") or payload.get("exit_plan") or {}
    if bracket in (None, ""):
        return {}
    if not isinstance(bracket, dict):
        raise SignalValidationError("bracket must be an object")
    return bracket


def _field(payload: dict[str, Any], bracket: dict[str, Any], *names: str) -> Any:
    for name in names:
        if payload.get(name) not in (None, ""):
            return payload.get(name)
    for name in names:
        if bracket.get(name) not in (None, ""):
            return bracket.get(name)
    return None


def _normalize_side(value: Any) -> str:
    if value is None:
        raise SignalValidationError("signal requires side")
    side = str(value).strip().lower()
    if side in FORBIDDEN_ACTIONS:
        raise SignalValidationError(f"forbidden action: {side}")
    if side not in SIDE_ALIASES:
        raise SignalValidationError(f"unsupported side: {side}")
    return SIDE_ALIASES[side]


def _is_close_side(value: Any) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() in {"close", "close_long", "close_short"}


def normalize_symbol(value: Any) -> str:
    if value is None:
        raise SignalValidationError("signal requires symbol")
    symbol = str(value).strip().upper().replace("-", "/").replace("_", "/")
    if not symbol:
        raise SignalValidationError("signal requires symbol")
    if "/" in symbol:
        base, quote = [part.strip() for part in symbol.split("/", 1)]
        if not base or not quote:
            raise SignalValidationError(f"invalid symbol: {value}")
        return f"{base}/{quote}"
    for suffix in QUOTE_SUFFIXES:
        if symbol.endswith(suffix) and len(symbol) > len(suffix):
            return f"{symbol[:-len(suffix)]}/{suffix}"
    raise SignalValidationError(f"symbol must include a quote asset: {value}")


def _optional_positive_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise SignalValidationError(f"invalid decimal value: {value}") from exc
    if parsed <= 0:
        raise SignalValidationError(f"value must be positive: {value}")
    return parsed


def _non_negative_int(value: Any, *, default: int) -> int:
    if value is None or value == "":
        return default
    parsed = int(value)
    if parsed < 0:
        raise SignalValidationError(f"value must be non-negative: {value}")
    return parsed


def _bool(value: Any, *, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def _take_profit_targets(
    value: Any,
    fallback_pct: Decimal | None,
    fallback_price: Decimal | None,
) -> tuple[TakeProfitTarget, ...]:
    if value is None or value == "":
        if fallback_pct is None and fallback_price is None:
            return ()
        return (TakeProfitTarget(pct=fallback_pct, trigger_price=fallback_price),)
    if not isinstance(value, list):
        raise SignalValidationError("take_profit_targets must be a list")

    targets: list[TakeProfitTarget] = []
    total_close_pct = Decimal("0")
    for item in value:
        if not isinstance(item, dict):
            raise SignalValidationError("take_profit_targets entries must be objects")
        pct = _optional_positive_decimal(item.get("pct") or item.get("take_profit_pct"))
        trigger_price = _optional_positive_decimal(
            item.get("trigger_price") or item.get("price") or item.get("take_profit_price")
        )
        close_pct = _optional_positive_decimal(item.get("close_pct") or item.get("size_pct")) or Decimal("100")
        if pct is None and trigger_price is None:
            raise SignalValidationError("take_profit_targets entries require pct or trigger_price")
        if close_pct > Decimal("100"):
            raise SignalValidationError("take_profit_targets close_pct cannot exceed 100")
        targets.append(TakeProfitTarget(pct=pct, trigger_price=trigger_price, close_pct=close_pct))
        total_close_pct += close_pct

    if not targets:
        return ()
    if total_close_pct > Decimal("100"):
        raise SignalValidationError("take_profit_targets close_pct total cannot exceed 100")
    return tuple(targets)


def _sort_take_profit_targets(
    targets: tuple[TakeProfitTarget, ...],
    *,
    side: str,
    entry_price: Decimal | None,
) -> tuple[TakeProfitTarget, ...]:
    def sort_key(target: TakeProfitTarget) -> Decimal:
        if entry_price is not None and target.pct is not None:
            direction = Decimal("1") if side == "buy" else Decimal("-1")
            trigger_price = entry_price * (Decimal("1") + direction * target.pct / Decimal("100"))
            return trigger_price if side == "buy" else -trigger_price
        if target.trigger_price is not None:
            return target.trigger_price if side == "buy" else -target.trigger_price
        if target.pct is not None:
            return target.pct if side == "buy" else -target.pct
        return Decimal("0")

    return tuple(sorted(targets, key=sort_key))


def _fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:24]
