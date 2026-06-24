from decimal import Decimal

from autocrypto.futures_risk import FuturesRiskConfig, FuturesTradeContext, assess_futures_trade


def test_futures_risk_estimates_isolated_long_liquidation_buffer():
    assessment = assess_futures_trade(
        FuturesTradeContext(
            symbol="BTC/USDT",
            side="buy",
            entry_price=Decimal("100"),
            stop_loss_price=Decimal("95"),
            notional=Decimal("1000"),
            leverage=Decimal("10"),
            maintenance_margin_pct=Decimal("0.5"),
        ),
        FuturesRiskConfig(min_liquidation_buffer_pct=Decimal("5"), max_leverage=Decimal("20")),
    )

    assert assessment.approved is True
    assert assessment.reason_codes == []
    assert assessment.liquidation_price == Decimal("90.50")
    assert assessment.liquidation_buffer_pct == Decimal("9.50")
    assert assessment.stop_loss_before_liquidation is True


def test_futures_risk_rejects_stop_beyond_liquidation_and_excess_leverage():
    assessment = assess_futures_trade(
        FuturesTradeContext(
            symbol="BTC/USDT",
            side="buy",
            entry_price=Decimal("100"),
            stop_loss_price=Decimal("89"),
            notional=Decimal("1000"),
            leverage=Decimal("25"),
            maintenance_margin_pct=Decimal("0.5"),
        ),
        FuturesRiskConfig(min_liquidation_buffer_pct=Decimal("5"), max_leverage=Decimal("20")),
    )

    assert assessment.approved is False
    assert "max_leverage_exceeded" in assessment.reason_codes
    assert "stop_loss_beyond_liquidation" in assessment.reason_codes
    assert "liquidation_buffer_too_narrow" in assessment.reason_codes


def test_futures_risk_flags_adverse_funding_window_for_long_and_short():
    long_assessment = assess_futures_trade(
        FuturesTradeContext(
            symbol="ETH/USDT",
            side="buy",
            entry_price=Decimal("100"),
            stop_loss_price=Decimal("95"),
            notional=Decimal("1000"),
            leverage=Decimal("5"),
            funding_rate_bps=Decimal("12"),
            minutes_to_funding=5,
        ),
        FuturesRiskConfig(max_adverse_funding_rate_bps=Decimal("10"), funding_window_minutes=15),
    )
    short_assessment = assess_futures_trade(
        FuturesTradeContext(
            symbol="ETH/USDT",
            side="sell",
            entry_price=Decimal("100"),
            stop_loss_price=Decimal("105"),
            notional=Decimal("1000"),
            leverage=Decimal("5"),
            funding_rate_bps=Decimal("-12"),
            minutes_to_funding=5,
        ),
        FuturesRiskConfig(max_adverse_funding_rate_bps=Decimal("10"), funding_window_minutes=15),
    )

    assert long_assessment.approved is False
    assert short_assessment.approved is False
    assert long_assessment.reason_codes == ["funding_rate_too_adverse", "funding_window_risk"]
    assert short_assessment.reason_codes == ["funding_rate_too_adverse", "funding_window_risk"]
