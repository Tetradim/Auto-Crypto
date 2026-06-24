from datetime import datetime, timedelta, timezone
from decimal import Decimal

from autocrypto.scalper import (
    PriceBand,
    RebracketRuntimeState,
    ScalperBracketConfig,
    plan_rebracket,
    reentry_cooldown_remaining,
    scalper_signal_payload,
)


def test_rebracket_moves_band_up_from_recent_low():
    now = datetime(2026, 6, 23, 15, 0, tzinfo=timezone.utc)
    decision = plan_rebracket(
        symbol="BTC/USDT",
        price=Decimal("103"),
        band=PriceBand(lower=Decimal("99"), upper=Decimal("101")),
        config=ScalperBracketConfig(
            threshold=Decimal("2"),
            min_drift=Decimal("0.50"),
            spread=Decimal("0.80"),
            buffer=Decimal("0.10"),
            lookback=4,
        ),
        state=RebracketRuntimeState(recent_prices=(Decimal("102"), Decimal("101.50"), Decimal("102.40"))),
        now=now,
    )

    assert decision.should_rebracket is True
    assert decision.direction == "up"
    assert decision.new_band == PriceBand(lower=Decimal("101.40"), upper=Decimal("102.20"))
    assert decision.previous_band == PriceBand(lower=Decimal("99"), upper=Decimal("101"))
    assert decision.recent_prices == (Decimal("102"), Decimal("101.50"), Decimal("102.40"), Decimal("103"))


def test_rebracket_is_blocked_by_position_and_cooldown():
    now = datetime(2026, 6, 23, 15, 0, tzinfo=timezone.utc)
    band = PriceBand(lower=Decimal("99"), upper=Decimal("101"))
    config = ScalperBracketConfig(threshold=Decimal("1"), min_drift=Decimal("0.25"), cooldown_seconds=60)

    with_position = plan_rebracket(
        symbol="BTC/USDT",
        price=Decimal("103"),
        band=band,
        config=config,
        state=RebracketRuntimeState(),
        now=now,
        position_open=True,
    )
    cooling_down = plan_rebracket(
        symbol="BTC/USDT",
        price=Decimal("103"),
        band=band,
        config=config,
        state=RebracketRuntimeState(last_rebracket_at=now - timedelta(seconds=30)),
        now=now,
    )

    assert with_position.should_rebracket is False
    assert with_position.reason == "position_open"
    assert cooling_down.should_rebracket is False
    assert cooling_down.reason == "cooldown_active"


def test_rebracket_ignores_micro_drift():
    now = datetime(2026, 6, 23, 15, 0, tzinfo=timezone.utc)
    decision = plan_rebracket(
        symbol="BTC/USDT",
        price=Decimal("101.10"),
        band=PriceBand(lower=Decimal("100"), upper=Decimal("102")),
        config=ScalperBracketConfig(threshold=Decimal("2"), min_drift=Decimal("0.50")),
        state=RebracketRuntimeState(),
        now=now,
    )

    assert decision.should_rebracket is False
    assert decision.reason == "drift_below_threshold"


def test_scalper_signal_payload_maps_long_and_short_bands():
    band = PriceBand(lower=Decimal("100"), upper=Decimal("100.80"))

    long_payload = scalper_signal_payload(
        "BTC/USDT",
        "buy",
        band,
        quote_amount=Decimal("250"),
        stop_distance=Decimal("0.40"),
    )
    short_payload = scalper_signal_payload(
        "BTC/USDT",
        "sell",
        band,
        quote_amount=Decimal("250"),
        stop_distance=Decimal("0.40"),
    )

    assert long_payload["price"] == "100"
    assert long_payload["take_profit_price"] == "100.80"
    assert long_payload["stop_loss_price"] == "99.60"
    assert short_payload["price"] == "100.80"
    assert short_payload["take_profit_price"] == "100"
    assert short_payload["stop_loss_price"] == "101.20"


def test_reentry_cooldown_remaining_uses_last_exit_timestamp():
    now = datetime(2026, 6, 23, 15, 0, tzinfo=timezone.utc)

    assert reentry_cooldown_remaining(now - timedelta(seconds=20), cooldown_seconds=60, now=now) == 40
    assert reentry_cooldown_remaining(now - timedelta(seconds=90), cooldown_seconds=60, now=now) == 0
    assert reentry_cooldown_remaining(None, cooldown_seconds=60, now=now) == 0
