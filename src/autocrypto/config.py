from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

from .risk import RiskConfig


LIVE_TRADING_CONFIRMATION = "ENABLE LIVE CRYPTO TRADING"
LIVE_TRADING_CONFIRMATION_ENV = "AUTO_CRYPTO_LIVE_TRADING_CONFIRMATION"
WEBHOOK_SECRET_ENV = "AUTO_CRYPTO_WEBHOOK_SECRET"
REQUIRE_APPROVAL_ENV = "AUTO_CRYPTO_REQUIRE_APPROVAL"


@dataclass(frozen=True)
class AppSettings:
    db_path: Path | None
    webhook_secret: str | None
    webhook_tolerance_seconds: int | None
    require_approval: bool
    risk: RiskConfig


def load_settings() -> AppSettings:
    load_dotenv(dotenv_path=Path.cwd() / ".env")

    db_path_raw = _empty_to_none(os.getenv("AUTO_CRYPTO_DB_PATH"))
    webhook_secret = _empty_to_none(os.getenv(WEBHOOK_SECRET_ENV))
    tolerance_raw = _empty_to_none(os.getenv("AUTO_CRYPTO_WEBHOOK_TOLERANCE_SECONDS"))
    require_approval = _bool(os.getenv(REQUIRE_APPROVAL_ENV, "false"))

    settings = AppSettings(
        db_path=Path(db_path_raw) if db_path_raw else None,
        webhook_secret=webhook_secret,
        webhook_tolerance_seconds=_positive_int_or_none(tolerance_raw),
        require_approval=require_approval,
        risk=RiskConfig(
            max_order_notional=Decimal(os.getenv("AUTO_CRYPTO_MAX_ORDER_NOTIONAL", "1000")),
            max_open_notional=Decimal(os.getenv("AUTO_CRYPTO_MAX_OPEN_NOTIONAL", "0")),
            max_symbol_open_notional=Decimal(os.getenv("AUTO_CRYPTO_MAX_SYMBOL_OPEN_NOTIONAL", "0")),
            max_open_risk_amount=Decimal(os.getenv("AUTO_CRYPTO_MAX_OPEN_RISK_AMOUNT", "0")),
            max_open_risk_equity_pct=Decimal(os.getenv("AUTO_CRYPTO_MAX_OPEN_RISK_EQUITY_PCT", "0")),
            max_position_equity_pct=Decimal(os.getenv("AUTO_CRYPTO_MAX_POSITION_EQUITY_PCT", "0")),
            max_risk_amount=Decimal(os.getenv("AUTO_CRYPTO_MAX_RISK_AMOUNT", "0")),
            max_risk_per_trade_pct=Decimal(os.getenv("AUTO_CRYPTO_MAX_RISK_PER_TRADE_PCT", "0")),
            max_entry_volatility_pct=Decimal(os.getenv("AUTO_CRYPTO_MAX_ENTRY_VOLATILITY_PCT", "0")),
            max_leverage=Decimal(os.getenv("AUTO_CRYPTO_MAX_LEVERAGE", "1")),
            max_daily_loss=Decimal(os.getenv("AUTO_CRYPTO_MAX_DAILY_LOSS", "500")),
            max_consecutive_losses=int(os.getenv("AUTO_CRYPTO_MAX_CONSECUTIVE_LOSSES", "0")),
            require_stop_loss=_bool(os.getenv("AUTO_CRYPTO_REQUIRE_STOP_LOSS", "true")),
            max_stop_loss_pct=Decimal(os.getenv("AUTO_CRYPTO_MAX_STOP_LOSS_PCT", "0")),
            max_trailing_stop_pct=Decimal(os.getenv("AUTO_CRYPTO_MAX_TRAILING_STOP_PCT", "0")),
            min_reward_risk_ratio=Decimal(os.getenv("AUTO_CRYPTO_MIN_REWARD_RISK_RATIO", "0")),
            min_total_reward_risk_ratio=Decimal(os.getenv("AUTO_CRYPTO_MIN_TOTAL_REWARD_RISK_RATIO", "0")),
            max_take_profit_targets=int(os.getenv("AUTO_CRYPTO_MAX_TAKE_PROFIT_TARGETS", "0")),
            max_slippage_bps=int(os.getenv("AUTO_CRYPTO_MAX_SLIPPAGE_BPS", "100")),
            allowed_exchanges=_csv_set(os.getenv("AUTO_CRYPTO_ALLOWED_EXCHANGES", "paper")),
            require_fixed_stop_for_pending_trailing=_bool(
                os.getenv("AUTO_CRYPTO_REQUIRE_FIXED_STOP_FOR_PENDING_TRAILING", "true")
            ),
        ),
    )
    _validate_live_readiness(settings, live_enabled_flags=_live_enabled_flags())
    return settings


def _bool(value: str) -> bool:
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _empty_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _positive_int_or_none(value: str | None) -> int | None:
    if value is None:
        return None
    parsed = int(value)
    return parsed if parsed > 0 else None


def _csv_set(value: str) -> set[str]:
    return {part.strip().lower() for part in value.split(",") if part.strip()}


def _validate_live_readiness(settings: AppSettings, *, live_enabled_flags: list[str]) -> None:
    non_paper_exchanges = settings.risk.allowed_exchanges - {"paper"}
    if not non_paper_exchanges and not live_enabled_flags:
        return
    if not settings.require_approval:
        raise ValueError(
            "AUTO_CRYPTO_REQUIRE_APPROVAL=true is required when non-paper exchanges "
            "or live execution flags are configured"
        )
    if not settings.webhook_secret:
        raise ValueError(
            "AUTO_CRYPTO_WEBHOOK_SECRET is required when non-paper exchanges "
            "or live execution flags are configured"
        )
    if len(settings.webhook_secret) < 32:
        raise ValueError(
            "AUTO_CRYPTO_WEBHOOK_SECRET must be at least 32 characters when non-paper "
            "exchanges or live execution flags are configured"
        )
    if not live_trading_signoff_confirmed():
        raise ValueError(
            f"{LIVE_TRADING_CONFIRMATION_ENV} must match the required confirmation phrase "
            "when non-paper exchanges or live execution flags are configured"
        )


def live_trading_signoff_confirmed() -> bool:
    return os.getenv(LIVE_TRADING_CONFIRMATION_ENV, "").strip() == LIVE_TRADING_CONFIRMATION


def live_readiness_requirements_satisfied() -> bool:
    webhook_secret = _empty_to_none(os.getenv(WEBHOOK_SECRET_ENV))
    return (
        _bool(os.getenv(REQUIRE_APPROVAL_ENV, "false"))
        and webhook_secret is not None
        and len(webhook_secret) >= 32
        and live_trading_signoff_confirmed()
    )


def live_execution_enabled_from_env(env_var: str) -> bool:
    return _live_flag_enabled(os.getenv(env_var, "false")) and live_readiness_requirements_satisfied()


def _live_enabled_flags() -> list[str]:
    return sorted(
        key
        for key, value in os.environ.items()
        if key.startswith("AUTO_CRYPTO_") and key.endswith("_LIVE_ENABLED") and _live_flag_enabled(value)
    )


def _live_flag_enabled(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}
