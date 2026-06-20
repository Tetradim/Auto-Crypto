from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any


class SignalValidationError(ValueError):
    """Raised when an incoming alert cannot be converted to a safe signal."""


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
    price: Decimal | None = None
    stop_loss_pct: Decimal | None = None
    take_profit_pct: Decimal | None = None
    trailing_stop_pct: Decimal | None = None
    leverage: Decimal = Decimal("1")
    max_slippage_bps: int = 100
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
}

QUOTE_SUFFIXES = ("USDT", "USDC", "USD", "BTC", "ETH", "EUR")
FORBIDDEN_ACTIONS = {"withdraw", "transfer", "internal_transfer", "deposit"}


def normalize_signal(payload: dict[str, Any], *, source: str) -> CryptoSignal:
    if not isinstance(payload, dict):
        raise SignalValidationError("signal payload must be an object")

    side = _normalize_side(payload.get("side") or payload.get("action"))
    symbol = normalize_symbol(payload.get("symbol") or payload.get("ticker") or payload.get("pair"))
    quote_amount = _optional_positive_decimal(payload.get("quote_amount") or payload.get("notional"))
    base_amount = _optional_positive_decimal(payload.get("base_amount") or payload.get("quantity") or payload.get("qty"))

    if quote_amount is None and base_amount is None:
        raise SignalValidationError("signal requires quote_amount or base_amount")

    price = _optional_positive_decimal(payload.get("price") or payload.get("entry_price") or payload.get("limit_price"))
    stop_loss_pct = _optional_positive_decimal(payload.get("stop_loss_pct"))
    take_profit_pct = _optional_positive_decimal(payload.get("take_profit_pct"))
    trailing_stop_pct = _optional_positive_decimal(payload.get("trailing_stop_pct"))
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
        "price": str(price) if price is not None else None,
        "stop_loss_pct": str(stop_loss_pct) if stop_loss_pct is not None else None,
        "take_profit_pct": str(take_profit_pct) if take_profit_pct is not None else None,
        "trailing_stop_pct": str(trailing_stop_pct) if trailing_stop_pct is not None else None,
        "leverage": str(leverage),
        "max_slippage_bps": max_slippage_bps,
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
        price=price,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        trailing_stop_pct=trailing_stop_pct,
        leverage=leverage,
        max_slippage_bps=max_slippage_bps,
        strategy_id=strategy_id,
        raw_payload=dict(payload),
    )


def _normalize_side(value: Any) -> str:
    if value is None:
        raise SignalValidationError("signal requires side")
    side = str(value).strip().lower()
    if side in FORBIDDEN_ACTIONS:
        raise SignalValidationError(f"forbidden action: {side}")
    if side not in SIDE_ALIASES:
        raise SignalValidationError(f"unsupported side: {side}")
    return SIDE_ALIASES[side]


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


def _fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:24]
