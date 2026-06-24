from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class AdvisoryRiskInput:
    side: str = "buy"
    leverage: Decimal = Decimal("1")
    liquidation_buffer_pct: Decimal | None = None
    funding_rate_bps: Decimal = Decimal("0")
    volatility_pct: Decimal = Decimal("0")
    spread_bps: Decimal = Decimal("0")
    market_state: str = "normal"
    exchange_status: str = "ok"


@dataclass(frozen=True)
class AdvisoryRiskScore:
    score: int
    level: str
    reason_codes: list[str] = field(default_factory=list)
    hard_gate: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "level": self.level,
            "reason_codes": self.reason_codes,
            "hard_gate": self.hard_gate,
        }


def score_advisory_risk(risk_input: AdvisoryRiskInput) -> AdvisoryRiskScore:
    score = 0
    reasons: list[str] = []

    if risk_input.leverage >= Decimal("10"):
        score += 20
        reasons.append("high_leverage")
    elif risk_input.leverage >= Decimal("5"):
        score += 10
        reasons.append("elevated_leverage")

    if risk_input.liquidation_buffer_pct is not None:
        if risk_input.liquidation_buffer_pct < Decimal("5"):
            score += 20
            reasons.append("narrow_liquidation_buffer")
        elif risk_input.liquidation_buffer_pct < Decimal("10"):
            score += 10
            reasons.append("reduced_liquidation_buffer")

    adverse_funding = _adverse_funding_rate(risk_input.side, risk_input.funding_rate_bps)
    if adverse_funding > Decimal("10"):
        score += 15
        reasons.append("adverse_funding")
    elif adverse_funding > Decimal("5"):
        score += 8
        reasons.append("elevated_funding")

    if risk_input.volatility_pct > Decimal("6"):
        score += 15
        reasons.append("high_volatility")
    elif risk_input.volatility_pct > Decimal("3"):
        score += 8
        reasons.append("elevated_volatility")

    if risk_input.spread_bps > Decimal("25"):
        score += 10
        reasons.append("wide_spread")
    elif risk_input.spread_bps > Decimal("10"):
        score += 5
        reasons.append("elevated_spread")

    market_state = risk_input.market_state.strip().lower()
    if market_state == "halted":
        score += 20
        reasons.append("market_state_halted")
    elif market_state == "stressed":
        score += 10
        reasons.append("market_state_stressed")

    if risk_input.exchange_status.strip().lower() not in {"ok", "healthy", "normal"}:
        score += 20
        reasons.append("exchange_degraded")

    score = min(score, 100)
    return AdvisoryRiskScore(score=score, level=_risk_level(score), reason_codes=reasons, hard_gate=False)


def _risk_level(score: int) -> str:
    if score >= 75:
        return "extreme"
    if score >= 50:
        return "high"
    if score >= 25:
        return "medium"
    return "low"


def _adverse_funding_rate(side: str, funding_rate_bps: Decimal) -> Decimal:
    normalized = side.strip().lower()
    if normalized in {"sell", "short"}:
        return max(-funding_rate_bps, Decimal("0"))
    return max(funding_rate_bps, Decimal("0"))
