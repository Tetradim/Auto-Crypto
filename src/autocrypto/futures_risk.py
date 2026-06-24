from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Any


PRICE_QUANT = Decimal("0.01")
PCT_QUANT = Decimal("0.01")


@dataclass(frozen=True)
class FuturesRiskConfig:
    max_leverage: Decimal = Decimal("10")
    min_liquidation_buffer_pct: Decimal = Decimal("5")
    max_adverse_funding_rate_bps: Decimal = Decimal("10")
    funding_window_minutes: int = 15


@dataclass(frozen=True)
class FuturesTradeContext:
    symbol: str
    side: str
    entry_price: Decimal
    stop_loss_price: Decimal
    notional: Decimal
    leverage: Decimal
    maintenance_margin_pct: Decimal = Decimal("0.5")
    funding_rate_bps: Decimal = Decimal("0")
    minutes_to_funding: int | None = None


@dataclass(frozen=True)
class FuturesRiskAssessment:
    approved: bool
    reason_codes: list[str]
    liquidation_price: Decimal
    liquidation_buffer_pct: Decimal
    stop_loss_before_liquidation: bool
    estimated_loss_to_stop: Decimal
    estimated_funding_cost: Decimal

    def to_dict(self) -> dict[str, Any]:
        return {
            "approved": self.approved,
            "reason_codes": self.reason_codes,
            "liquidation_price": _plain(self.liquidation_price),
            "liquidation_buffer_pct": _plain(self.liquidation_buffer_pct),
            "stop_loss_before_liquidation": self.stop_loss_before_liquidation,
            "estimated_loss_to_stop": _plain(self.estimated_loss_to_stop),
            "estimated_funding_cost": _plain(self.estimated_funding_cost),
        }


def assess_futures_trade(
    context: FuturesTradeContext,
    config: FuturesRiskConfig | None = None,
) -> FuturesRiskAssessment:
    config = config or FuturesRiskConfig()
    side = _normalized_side(context.side)
    liquidation_price = estimate_isolated_liquidation_price(context)
    liquidation_buffer_pct = _liquidation_buffer_pct(side, context.entry_price, liquidation_price)
    stop_loss_before_liquidation = _stop_loss_before_liquidation(side, context.stop_loss_price, liquidation_price)
    estimated_loss_to_stop = _estimated_stop_loss(side, context)
    adverse_funding = _adverse_funding_rate(side, context.funding_rate_bps)
    estimated_funding_cost = (context.notional * adverse_funding / Decimal("10000")).quantize(
        PRICE_QUANT,
        rounding=ROUND_HALF_UP,
    )

    reasons: list[str] = []
    if context.leverage > config.max_leverage:
        reasons.append("max_leverage_exceeded")
    if not stop_loss_before_liquidation:
        reasons.append("stop_loss_beyond_liquidation")
    if liquidation_buffer_pct < config.min_liquidation_buffer_pct:
        reasons.append("liquidation_buffer_too_narrow")
    if adverse_funding > config.max_adverse_funding_rate_bps:
        reasons.append("funding_rate_too_adverse")
        if (
            context.minutes_to_funding is not None
            and context.minutes_to_funding <= config.funding_window_minutes
        ):
            reasons.append("funding_window_risk")

    return FuturesRiskAssessment(
        approved=not reasons,
        reason_codes=reasons,
        liquidation_price=liquidation_price,
        liquidation_buffer_pct=liquidation_buffer_pct,
        stop_loss_before_liquidation=stop_loss_before_liquidation,
        estimated_loss_to_stop=estimated_loss_to_stop,
        estimated_funding_cost=estimated_funding_cost,
    )


def estimate_isolated_liquidation_price(context: FuturesTradeContext) -> Decimal:
    if context.entry_price <= 0:
        raise ValueError("entry_price must be positive")
    if context.leverage <= 0:
        raise ValueError("leverage must be positive")
    maintenance = context.maintenance_margin_pct / Decimal("100")
    leverage_margin = Decimal("1") / context.leverage
    side = _normalized_side(context.side)
    if side == "buy":
        raw = context.entry_price * (Decimal("1") - leverage_margin + maintenance)
    else:
        raw = context.entry_price * (Decimal("1") + leverage_margin - maintenance)
    return raw.quantize(PRICE_QUANT, rounding=ROUND_HALF_UP)


def _liquidation_buffer_pct(side: str, entry_price: Decimal, liquidation_price: Decimal) -> Decimal:
    if side == "buy":
        distance = entry_price - liquidation_price
    else:
        distance = liquidation_price - entry_price
    return (distance * Decimal("100") / entry_price).quantize(PCT_QUANT, rounding=ROUND_HALF_UP)


def _stop_loss_before_liquidation(side: str, stop_loss_price: Decimal, liquidation_price: Decimal) -> bool:
    if side == "buy":
        return stop_loss_price > liquidation_price
    return stop_loss_price < liquidation_price


def _estimated_stop_loss(side: str, context: FuturesTradeContext) -> Decimal:
    if side == "buy":
        distance = context.entry_price - context.stop_loss_price
    else:
        distance = context.stop_loss_price - context.entry_price
    if distance <= 0:
        return Decimal("0.00")
    return (context.notional * distance / context.entry_price).quantize(PRICE_QUANT, rounding=ROUND_HALF_UP)


def _adverse_funding_rate(side: str, funding_rate_bps: Decimal) -> Decimal:
    if side == "buy":
        return max(funding_rate_bps, Decimal("0"))
    return max(-funding_rate_bps, Decimal("0"))


def _normalized_side(side: str) -> str:
    normalized = side.strip().lower()
    if normalized in {"buy", "long"}:
        return "buy"
    if normalized in {"sell", "short"}:
        return "sell"
    raise ValueError("side must be buy/long or sell/short")


def _plain(value: Decimal) -> str:
    return format(value, "f")
