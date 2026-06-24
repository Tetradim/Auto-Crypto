from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from .advisory_risk import AdvisoryRiskInput, score_advisory_risk
from .engine import TradingEngine
from .futures_risk import FuturesRiskConfig, FuturesTradeContext, assess_futures_trade
from .market_state import MarketStatePolicy, MarketStateSnapshot, evaluate_market_state
from .order_recorder import cooldown_state_key
from .protections import ProtectionState, evaluate_protections, protection_state_from_dict
from .repository import SQLiteRepository
from .risk import evaluate_signal
from .scalper import reentry_cooldown_remaining
from .signals import CryptoSignal


PROTECTIONS_STATE_KEY = "runtime:protections"
RUNTIME_CONFIG_KEY = "runtime:config"
FUTURES_MARKET_TYPES = {"swap", "future", "futures", "perp", "perpetual"}


def runtime_config(repository: SQLiteRepository | None) -> dict[str, Any]:
    defaults = {"reentry_cooldown_seconds": 0}
    if repository is None:
        return defaults
    payload = repository.get_runtime_state(RUNTIME_CONFIG_KEY) or {}
    return runtime_config_from_payload(payload, existing=defaults)


def runtime_config_from_payload(payload: dict[str, Any], *, existing: dict[str, Any]) -> dict[str, Any]:
    config = dict(existing)
    if "reentry_cooldown_seconds" in payload:
        config["reentry_cooldown_seconds"] = _non_negative_int(
            payload.get("reentry_cooldown_seconds"),
            default=0,
        )
    return config


def protection_state(repository: SQLiteRepository | None) -> ProtectionState:
    if repository is None:
        return ProtectionState()
    return protection_state_from_dict(repository.get_runtime_state(PROTECTIONS_STATE_KEY))


def save_protection_state(repository: SQLiteRepository, state: ProtectionState) -> None:
    repository.set_runtime_state(PROTECTIONS_STATE_KEY, state.to_dict())


def runtime_control_summary(
    signal: CryptoSignal,
    *,
    engine: TradingEngine,
    repository: SQLiteRepository | None,
) -> dict[str, Any]:
    reason_codes: list[str] = []
    approval_required = False
    protection_decision = evaluate_protections(signal, protection_state(repository))
    if not protection_decision.allowed:
        _extend_unique(reason_codes, protection_decision.reason_codes)

    cooldown_payload = reentry_cooldown_payload(signal, repository)
    if cooldown_payload["active"]:
        _append_unique(reason_codes, "reentry_cooldown_active")

    market_state_payload: dict[str, Any] | None = None
    market_state = market_state_for_signal(signal)
    if market_state is not None:
        market_state_payload = market_state.to_dict()
        if market_state.no_new_entries and not signal.reduce_only:
            _append_unique(reason_codes, "market_state_no_new_entries")
            _extend_unique(reason_codes, [f"market_state:{reason}" for reason in market_state.reason_codes])
        elif market_state.approval_required and not signal.reduce_only:
            approval_required = True

    futures_risk_payload: dict[str, Any] | None = None
    risk_decision = evaluate_signal(signal, engine.risk_config, engine.account_state)
    if signal.market_type.strip().lower() in FUTURES_MARKET_TYPES and not signal.reduce_only:
        futures_context_reasons = futures_context_rejection_reasons(signal, risk_decision.order_notional)
        if futures_context_reasons:
            futures_risk_payload = rejected_futures_risk_payload(futures_context_reasons)
            _append_unique(reason_codes, "futures_risk_rejected")
            _extend_unique(reason_codes, futures_context_reasons)
        else:
            futures_context = futures_context_for_signal(signal, risk_decision.order_notional)
            if futures_context is not None:
                futures_risk = assess_futures_trade(
                    futures_context,
                    futures_risk_config_from_payload(signal.raw_payload),
                )
                futures_risk_payload = futures_risk.to_dict()
                if not futures_risk.approved:
                    _append_unique(reason_codes, "futures_risk_rejected")
                    _extend_unique(reason_codes, futures_risk.reason_codes)

    return {
        "reason_codes": reason_codes,
        "approval_required": approval_required,
        "protections": protection_decision.to_dict(),
        "reentry_cooldown": cooldown_payload,
        "market_state": market_state_payload,
        "futures_risk": futures_risk_payload,
        "advisory_risk": advisory_risk_payload(signal, market_state_payload, futures_risk_payload),
    }


def reentry_cooldown_payload(
    signal: CryptoSignal,
    repository: SQLiteRepository | None,
) -> dict[str, Any]:
    config = runtime_config(repository)
    cooldown_seconds = int(config.get("reentry_cooldown_seconds") or 0)
    payload = {
        "active": False,
        "remaining_seconds": 0,
        "cooldown_seconds": cooldown_seconds,
        "last_exit_at": None,
        "source_key": None,
    }
    if repository is None or cooldown_seconds <= 0 or signal.reduce_only:
        return payload

    now = datetime.now(timezone.utc)
    candidates: list[tuple[int, str, dict[str, Any]]] = []
    for key in (cooldown_state_key(signal.symbol, "*"), cooldown_state_key(signal.symbol, signal.strategy_id)):
        state = repository.get_runtime_state(key)
        if not state:
            continue
        last_exit_at = optional_datetime(state.get("last_exit_at"))
        remaining = reentry_cooldown_remaining(last_exit_at, cooldown_seconds=cooldown_seconds, now=now)
        candidates.append((remaining, key, state))
    if not candidates:
        return payload

    remaining, key, state = max(candidates, key=lambda item: item[0])
    payload.update(
        {
            "active": remaining > 0,
            "remaining_seconds": remaining,
            "last_exit_at": state.get("last_exit_at"),
            "source_key": key,
        }
    )
    return payload


def market_state_for_signal(signal: CryptoSignal) -> Any | None:
    snapshot_payload = signal.raw_payload.get("market_state")
    if not isinstance(snapshot_payload, dict):
        return None
    policy_payload = signal.raw_payload.get("market_state_policy")
    if not isinstance(policy_payload, dict):
        policy_payload = {}
    defaults = MarketStatePolicy()
    snapshot = MarketStateSnapshot(
        volatility_pct=decimal_value(snapshot_payload.get("volatility_pct"), default=Decimal("0")),
        spread_bps=decimal_value(snapshot_payload.get("spread_bps"), default=Decimal("0")),
        depth_notional=decimal_value(snapshot_payload.get("depth_notional"), default=Decimal("0")),
        funding_rate_bps=decimal_value(snapshot_payload.get("funding_rate_bps"), default=Decimal("0")),
        minutes_to_funding=optional_int(snapshot_payload.get("minutes_to_funding")),
        liquidation_buffer_pct=optional_positive_decimal(snapshot_payload.get("liquidation_buffer_pct")),
        data_stale_seconds=optional_int(snapshot_payload.get("data_stale_seconds")) or 0,
        exchange_status=str(snapshot_payload.get("exchange_status") or "ok"),
    )
    policy = MarketStatePolicy(
        max_normal_volatility_pct=decimal_value(
            policy_payload.get("max_normal_volatility_pct"),
            default=defaults.max_normal_volatility_pct,
        ),
        max_spread_bps=decimal_value(policy_payload.get("max_spread_bps"), default=defaults.max_spread_bps),
        min_depth_notional=decimal_value(
            policy_payload.get("min_depth_notional"),
            default=defaults.min_depth_notional,
        ),
        funding_window_minutes=optional_int(policy_payload.get("funding_window_minutes"))
        or defaults.funding_window_minutes,
        min_liquidation_buffer_pct=decimal_value(
            policy_payload.get("min_liquidation_buffer_pct"),
            default=defaults.min_liquidation_buffer_pct,
        ),
        data_stale_after_seconds=optional_int(policy_payload.get("data_stale_after_seconds"))
        or defaults.data_stale_after_seconds,
    )
    return evaluate_market_state(snapshot, policy)


def futures_context_rejection_reasons(
    signal: CryptoSignal,
    order_notional: Decimal | None,
) -> list[str]:
    reasons: list[str] = []
    if signal.price is None:
        reasons.append("futures_price_required")
    if order_notional is None:
        reasons.append("futures_notional_required")
    if signal_stop_loss_price(signal) is None:
        reasons.append("futures_stop_loss_required")
    return reasons


def rejected_futures_risk_payload(reason_codes: list[str]) -> dict[str, Any]:
    return {
        "approved": False,
        "reason_codes": reason_codes,
        "liquidation_price": None,
        "liquidation_buffer_pct": None,
        "stop_loss_before_liquidation": None,
        "estimated_loss_to_stop": None,
        "estimated_funding_cost": None,
    }


def futures_context_for_signal(
    signal: CryptoSignal,
    order_notional: Decimal | None,
) -> FuturesTradeContext | None:
    if signal.market_type.strip().lower() not in FUTURES_MARKET_TYPES:
        return None
    if signal.price is None or order_notional is None:
        return None
    stop_loss_price = signal_stop_loss_price(signal)
    if stop_loss_price is None:
        return None
    return FuturesTradeContext(
        symbol=signal.symbol,
        side=signal.side,
        entry_price=signal.price,
        stop_loss_price=stop_loss_price,
        notional=order_notional,
        leverage=signal.leverage,
        maintenance_margin_pct=decimal_value(
            signal.raw_payload.get("maintenance_margin_pct"),
            default=Decimal("0.5"),
        ),
        funding_rate_bps=decimal_value(signal.raw_payload.get("funding_rate_bps"), default=Decimal("0")),
        minutes_to_funding=optional_int(signal.raw_payload.get("minutes_to_funding")),
    )


def futures_risk_config_from_payload(payload: dict[str, Any]) -> FuturesRiskConfig:
    config_payload = payload.get("futures_risk_config") or payload.get("futures_risk") or {}
    if not isinstance(config_payload, dict):
        config_payload = {}
    defaults = FuturesRiskConfig()
    return FuturesRiskConfig(
        max_leverage=decimal_value(config_payload.get("max_leverage"), default=defaults.max_leverage),
        min_liquidation_buffer_pct=decimal_value(
            config_payload.get("min_liquidation_buffer_pct"),
            default=defaults.min_liquidation_buffer_pct,
        ),
        max_adverse_funding_rate_bps=decimal_value(
            config_payload.get("max_adverse_funding_rate_bps") or config_payload.get("max_funding_rate_bps"),
            default=defaults.max_adverse_funding_rate_bps,
        ),
        funding_window_minutes=optional_int(config_payload.get("funding_window_minutes"))
        or defaults.funding_window_minutes,
    )


def signal_stop_loss_price(signal: CryptoSignal) -> Decimal | None:
    if signal.stop_loss_price is not None:
        return signal.stop_loss_price
    if signal.price is None or signal.stop_loss_pct is None:
        return None
    if signal.side == "buy":
        return signal.price * (Decimal("1") - signal.stop_loss_pct / Decimal("100"))
    return signal.price * (Decimal("1") + signal.stop_loss_pct / Decimal("100"))


def advisory_risk_payload(
    signal: CryptoSignal,
    market_state_payload: dict[str, Any] | None,
    futures_risk_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    market_snapshot = signal.raw_payload.get("market_state")
    if not isinstance(market_snapshot, dict):
        market_snapshot = {}
    liquidation_buffer_pct = None
    if futures_risk_payload and futures_risk_payload.get("liquidation_buffer_pct") is not None:
        liquidation_buffer_pct = decimal_value(futures_risk_payload.get("liquidation_buffer_pct"), default=Decimal("0"))
    elif market_snapshot.get("liquidation_buffer_pct") not in (None, ""):
        liquidation_buffer_pct = decimal_value(market_snapshot.get("liquidation_buffer_pct"), default=Decimal("0"))
    score = score_advisory_risk(
        AdvisoryRiskInput(
            side=signal.side,
            leverage=signal.leverage,
            liquidation_buffer_pct=liquidation_buffer_pct,
            funding_rate_bps=decimal_value(
                signal.raw_payload.get("funding_rate_bps") or market_snapshot.get("funding_rate_bps"),
                default=Decimal("0"),
            ),
            volatility_pct=decimal_value(market_snapshot.get("volatility_pct"), default=Decimal("0")),
            spread_bps=decimal_value(market_snapshot.get("spread_bps"), default=Decimal("0")),
            market_state=str((market_state_payload or {}).get("name") or "normal"),
            exchange_status=str(market_snapshot.get("exchange_status") or "ok"),
        )
    )
    return score.to_dict()


def optional_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid datetime: {value}") from exc


def optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid integer: {value}") from exc


def optional_positive_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid decimal: {value}") from exc
    if parsed <= 0:
        raise ValueError("decimal value must be positive")
    return parsed


def decimal_value(value: Any, *, default: Decimal) -> Decimal:
    if value is None or value == "":
        return default
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid decimal: {value}") from exc


def _non_negative_int(value: Any, *, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid integer: {value}") from exc
    if parsed < 0:
        raise ValueError("integer value must be non-negative")
    return parsed


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _extend_unique(values: list[str], additions: list[str]) -> None:
    for value in additions:
        _append_unique(values, value)
