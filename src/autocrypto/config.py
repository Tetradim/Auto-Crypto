from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from .risk import RiskConfig


@dataclass(frozen=True)
class AppSettings:
    db_path: Path | None
    webhook_secret: str | None
    webhook_tolerance_seconds: int | None
    require_approval: bool
    risk: RiskConfig


def load_settings() -> AppSettings:
    db_path_raw = _empty_to_none(os.getenv("AUTO_CRYPTO_DB_PATH"))
    webhook_secret = _empty_to_none(os.getenv("AUTO_CRYPTO_WEBHOOK_SECRET"))
    tolerance_raw = _empty_to_none(os.getenv("AUTO_CRYPTO_WEBHOOK_TOLERANCE_SECONDS"))

    return AppSettings(
        db_path=Path(db_path_raw) if db_path_raw else None,
        webhook_secret=webhook_secret,
        webhook_tolerance_seconds=int(tolerance_raw) if tolerance_raw else None,
        require_approval=_bool(os.getenv("AUTO_CRYPTO_REQUIRE_APPROVAL", "false")),
        risk=RiskConfig(
            max_order_notional=Decimal(os.getenv("AUTO_CRYPTO_MAX_ORDER_NOTIONAL", "1000")),
            max_leverage=Decimal(os.getenv("AUTO_CRYPTO_MAX_LEVERAGE", "1")),
            max_daily_loss=Decimal(os.getenv("AUTO_CRYPTO_MAX_DAILY_LOSS", "500")),
            require_stop_loss=_bool(os.getenv("AUTO_CRYPTO_REQUIRE_STOP_LOSS", "true")),
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


def _csv_set(value: str) -> set[str]:
    return {part.strip().lower() for part in value.split(",") if part.strip()}
