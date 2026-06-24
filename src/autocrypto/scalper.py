from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any


@dataclass(frozen=True)
class PriceBand:
    lower: Decimal
    upper: Decimal

    def __post_init__(self) -> None:
        if self.lower <= 0 or self.upper <= 0:
            raise ValueError("price band values must be positive")
        if self.lower >= self.upper:
            raise ValueError("price band lower must be below upper")

    @property
    def width(self) -> Decimal:
        return self.upper - self.lower

    def to_dict(self) -> dict[str, str]:
        return {"lower": _plain(self.lower), "upper": _plain(self.upper)}


@dataclass(frozen=True)
class ScalperBracketConfig:
    threshold: Decimal = Decimal("2")
    min_drift: Decimal = Decimal("0.50")
    spread: Decimal = Decimal("0.80")
    buffer: Decimal = Decimal("0.10")
    cooldown_seconds: int = 0
    lookback: int = 10
    price_increment: Decimal = Decimal("0.01")

    def __post_init__(self) -> None:
        if self.threshold < 0:
            raise ValueError("threshold must be non-negative")
        if self.min_drift < 0:
            raise ValueError("min_drift must be non-negative")
        if self.spread <= 0:
            raise ValueError("spread must be positive")
        if self.buffer < 0:
            raise ValueError("buffer must be non-negative")
        if self.cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must be non-negative")
        if self.lookback < 2:
            raise ValueError("lookback must be at least 2")
        if self.price_increment <= 0:
            raise ValueError("price_increment must be positive")


@dataclass(frozen=True)
class RebracketRuntimeState:
    recent_prices: tuple[Decimal, ...] = field(default_factory=tuple)
    last_rebracket_at: datetime | None = None
    previous_band: PriceBand | None = None


@dataclass(frozen=True)
class RebracketDecision:
    symbol: str
    should_rebracket: bool
    reason: str
    price: Decimal
    previous_band: PriceBand
    new_band: PriceBand | None = None
    direction: str | None = None
    recent_prices: tuple[Decimal, ...] = field(default_factory=tuple)
    cooldown_remaining_seconds: int = 0
    decided_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "should_rebracket": self.should_rebracket,
            "reason": self.reason,
            "direction": self.direction,
            "price": _plain(self.price),
            "previous_band": self.previous_band.to_dict(),
            "new_band": self.new_band.to_dict() if self.new_band else None,
            "recent_prices": [_plain(price) for price in self.recent_prices],
            "cooldown_remaining_seconds": self.cooldown_remaining_seconds,
            "decided_at": self.decided_at.isoformat() if self.decided_at else None,
        }


def plan_rebracket(
    *,
    symbol: str,
    price: Decimal,
    band: PriceBand,
    config: ScalperBracketConfig | None = None,
    state: RebracketRuntimeState | None = None,
    now: datetime | None = None,
    position_open: bool = False,
) -> RebracketDecision:
    config = config or ScalperBracketConfig()
    state = state or RebracketRuntimeState()
    now = _aware_utc(now or datetime.now(timezone.utc))

    if position_open:
        return RebracketDecision(
            symbol=symbol,
            should_rebracket=False,
            reason="position_open",
            price=price,
            previous_band=band,
            recent_prices=state.recent_prices,
            decided_at=now,
        )

    cooldown_remaining = reentry_cooldown_remaining(
        state.last_rebracket_at,
        cooldown_seconds=config.cooldown_seconds,
        now=now,
    )
    if cooldown_remaining > 0:
        return RebracketDecision(
            symbol=symbol,
            should_rebracket=False,
            reason="cooldown_active",
            price=price,
            previous_band=band,
            recent_prices=state.recent_prices,
            cooldown_remaining_seconds=cooldown_remaining,
            decided_at=now,
        )

    recent_prices = (*state.recent_prices, price)[-config.lookback :]
    upward_drift = price - band.lower
    downward_drift = band.upper - price

    if upward_drift > config.threshold and upward_drift > config.min_drift:
        anchor = min(recent_prices)
        new_lower = _quantize(anchor - config.buffer, config.price_increment)
        direction = "up"
    elif downward_drift > config.threshold and downward_drift > config.min_drift:
        anchor = max(recent_prices)
        new_lower = _quantize(anchor - config.buffer, config.price_increment)
        direction = "down"
    else:
        return RebracketDecision(
            symbol=symbol,
            should_rebracket=False,
            reason="drift_below_threshold",
            price=price,
            previous_band=band,
            recent_prices=recent_prices,
            decided_at=now,
        )

    if new_lower <= 0:
        return RebracketDecision(
            symbol=symbol,
            should_rebracket=False,
            reason="invalid_new_band",
            price=price,
            previous_band=band,
            recent_prices=recent_prices,
            decided_at=now,
        )

    new_band = PriceBand(
        lower=new_lower,
        upper=_quantize(new_lower + config.spread, config.price_increment),
    )
    return RebracketDecision(
        symbol=symbol,
        should_rebracket=True,
        reason="drift_threshold_exceeded",
        price=price,
        previous_band=band,
        new_band=new_band,
        direction=direction,
        recent_prices=recent_prices,
        decided_at=now,
    )


def scalper_signal_payload(
    symbol: str,
    side: str,
    band: PriceBand,
    *,
    quote_amount: Decimal | None = None,
    base_amount: Decimal | None = None,
    risk_amount: Decimal | None = None,
    risk_pct: Decimal | None = None,
    stop_distance: Decimal | None = None,
    exchange: str = "paper",
    market_type: str = "swap",
    strategy_id: str = "sentinel_pulse_scalper",
) -> dict[str, Any]:
    normalized_side = side.strip().lower()
    if normalized_side in {"long", "buy"}:
        signal_side = "buy"
        entry_price = band.lower
        take_profit_price = band.upper
        stop_loss_price = entry_price - (stop_distance or band.width)
    elif normalized_side in {"short", "sell"}:
        signal_side = "sell"
        entry_price = band.upper
        take_profit_price = band.lower
        stop_loss_price = entry_price + (stop_distance or band.width)
    else:
        raise ValueError("side must be buy/long or sell/short")

    if stop_loss_price <= 0:
        raise ValueError("stop loss price must be positive")

    payload: dict[str, Any] = {
        "symbol": symbol,
        "side": signal_side,
        "exchange": exchange,
        "market_type": market_type,
        "price": _plain(entry_price),
        "stop_loss_price": _plain(stop_loss_price),
        "take_profit_price": _plain(take_profit_price),
        "strategy_id": strategy_id,
    }
    _put_optional_decimal(payload, "quote_amount", quote_amount)
    _put_optional_decimal(payload, "base_amount", base_amount)
    _put_optional_decimal(payload, "risk_amount", risk_amount)
    _put_optional_decimal(payload, "risk_pct", risk_pct)
    return payload


def reentry_cooldown_remaining(
    last_exit_at: datetime | None,
    *,
    cooldown_seconds: int,
    now: datetime | None = None,
) -> int:
    if cooldown_seconds <= 0 or last_exit_at is None:
        return 0
    current = _aware_utc(now or datetime.now(timezone.utc))
    previous = _aware_utc(last_exit_at)
    elapsed = (current - previous).total_seconds()
    return max(0, int(cooldown_seconds - elapsed))


def _put_optional_decimal(payload: dict[str, Any], key: str, value: Decimal | None) -> None:
    if value is not None:
        payload[key] = _plain(value)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _quantize(value: Decimal, increment: Decimal) -> Decimal:
    return value.quantize(increment, rounding=ROUND_HALF_UP)


def _plain(value: Decimal) -> str:
    return format(value, "f")
