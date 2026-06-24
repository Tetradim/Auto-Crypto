from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class MarketStatePolicy:
    max_normal_volatility_pct: Decimal = Decimal("6")
    max_spread_bps: Decimal = Decimal("25")
    min_depth_notional: Decimal = Decimal("50000")
    funding_window_minutes: int = 15
    min_liquidation_buffer_pct: Decimal = Decimal("5")
    data_stale_after_seconds: int = 30


@dataclass(frozen=True)
class MarketStateSnapshot:
    volatility_pct: Decimal = Decimal("0")
    spread_bps: Decimal = Decimal("0")
    depth_notional: Decimal = Decimal("0")
    funding_rate_bps: Decimal = Decimal("0")
    minutes_to_funding: int | None = None
    liquidation_buffer_pct: Decimal | None = None
    data_stale_seconds: int = 0
    exchange_status: str = "ok"


@dataclass(frozen=True)
class MarketState:
    name: str
    reason_codes: list[str] = field(default_factory=list)
    no_new_entries: bool = False
    approval_required: bool = False
    size_multiplier: Decimal = Decimal("1")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "reason_codes": self.reason_codes,
            "no_new_entries": self.no_new_entries,
            "approval_required": self.approval_required,
            "size_multiplier": _plain(self.size_multiplier),
        }


def evaluate_market_state(
    snapshot: MarketStateSnapshot,
    policy: MarketStatePolicy | None = None,
) -> MarketState:
    policy = policy or MarketStatePolicy()
    halt_reasons: list[str] = []
    stress_reasons: list[str] = []

    if snapshot.exchange_status.strip().lower() not in {"ok", "healthy", "normal"}:
        halt_reasons.append("exchange_degraded")
    if snapshot.data_stale_seconds > policy.data_stale_after_seconds:
        halt_reasons.append("market_data_stale")
    if (
        snapshot.liquidation_buffer_pct is not None
        and snapshot.liquidation_buffer_pct < policy.min_liquidation_buffer_pct
    ):
        halt_reasons.append("liquidation_buffer_danger")

    if halt_reasons:
        return MarketState(
            name="halted",
            reason_codes=halt_reasons,
            no_new_entries=True,
            approval_required=True,
            size_multiplier=Decimal("0"),
        )

    if snapshot.volatility_pct > policy.max_normal_volatility_pct:
        stress_reasons.append("high_volatility")
    if snapshot.spread_bps > policy.max_spread_bps:
        stress_reasons.append("wide_spread")
    if snapshot.depth_notional < policy.min_depth_notional:
        stress_reasons.append("thin_liquidity")
    if (
        snapshot.minutes_to_funding is not None
        and snapshot.minutes_to_funding <= policy.funding_window_minutes
    ):
        stress_reasons.append("funding_window")

    if not stress_reasons:
        return MarketState(name="normal")

    sizing_stress_count = sum(
        reason in {"high_volatility", "wide_spread", "thin_liquidity"} for reason in stress_reasons
    )
    size_multiplier = Decimal("0.5") if sizing_stress_count == 1 else Decimal("0.25")
    if sizing_stress_count == 0:
        size_multiplier = Decimal("1")

    return MarketState(
        name="stressed",
        reason_codes=stress_reasons,
        no_new_entries=False,
        approval_required=True,
        size_multiplier=size_multiplier,
    )


def _plain(value: Decimal) -> str:
    return format(value, "f")
