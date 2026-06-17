from decimal import Decimal

from autocrypto.config import load_settings


def test_load_settings_maps_environment_to_risk_webhook_and_repository_config(monkeypatch, tmp_path):
    db_path = tmp_path / "configured.sqlite3"
    monkeypatch.setenv("AUTO_CRYPTO_DB_PATH", str(db_path))
    monkeypatch.setenv("AUTO_CRYPTO_MAX_ORDER_NOTIONAL", "250")
    monkeypatch.setenv("AUTO_CRYPTO_MAX_LEVERAGE", "2")
    monkeypatch.setenv("AUTO_CRYPTO_REQUIRE_STOP_LOSS", "false")
    monkeypatch.setenv("AUTO_CRYPTO_ALLOWED_EXCHANGES", "paper,binance, kraken")
    monkeypatch.setenv("AUTO_CRYPTO_WEBHOOK_SECRET", "secret")
    monkeypatch.setenv("AUTO_CRYPTO_WEBHOOK_TOLERANCE_SECONDS", "120")
    monkeypatch.setenv("AUTO_CRYPTO_REQUIRE_APPROVAL", "true")

    settings = load_settings()

    assert settings.db_path == db_path
    assert settings.webhook_secret == "secret"
    assert settings.webhook_tolerance_seconds == 120
    assert settings.require_approval is True
    assert settings.risk.max_order_notional == Decimal("250")
    assert settings.risk.max_leverage == Decimal("2")
    assert settings.risk.require_stop_loss is False
    assert settings.risk.allowed_exchanges == {"paper", "binance", "kraken"}
