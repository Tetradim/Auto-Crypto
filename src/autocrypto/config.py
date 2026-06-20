from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

from .risk import RiskConfig


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
    webhook_secret = _empty_to_none(os.getenv("AUTO_CRYPTO_WEBHOOK_SECRET"))
    tolerance_raw = _empty_to_none(os.getenv("AUTO_CRYPTO_WEBHOOK_TOLERANCE_SECONDS"))

    return AppSettings(
        db_path=Path(db_path_raw) if db_path_raw else None,
        webhook_secret=webhook_secret,
        webhook_tolerance_seconds=_positive_int_or_none(tolerance_raw),
        require_approval=_bool(os.getenv("AUTO_CRYPTO_REQUIRE_APPROVAL", "false")),
        risk=RiskConfig(
            max_order_notional=Decimal(os.getenv("AUTO_CRYPTO_MAX_ORDER_NOTIONAL", "1000")),
            max_open_notional=Decimal(os.getenv("AUTO_CRYPTO_MAX_OPEN_NOTIONAL", "0")),
            max_symbol_open_notional=Decimal(os.getenv("AUTO_CRYPTO_MAX_SYMBOL_OPEN_NOTIONAL", "0")),
            max_position_equity_pct=Decimal(os.getenv("AUTO_CRYPTO_MAX_POSITION_EQUITY_PCT", "0")),
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
        ),
    )


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
