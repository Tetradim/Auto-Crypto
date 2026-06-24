from decimal import Decimal

from autocrypto.advisory_risk import AdvisoryRiskInput, score_advisory_risk


def test_advisory_risk_score_is_explainable_but_not_a_hard_gate():
    score = score_advisory_risk(
        AdvisoryRiskInput(
            leverage=Decimal("20"),
            liquidation_buffer_pct=Decimal("3"),
            funding_rate_bps=Decimal("12"),
            volatility_pct=Decimal("9"),
            spread_bps=Decimal("45"),
            market_state="stressed",
            exchange_status="ok",
        )
    )

    assert score.score == 90
    assert score.level == "extreme"
    assert score.hard_gate is False
    assert score.reason_codes == [
        "high_leverage",
        "narrow_liquidation_buffer",
        "adverse_funding",
        "high_volatility",
        "wide_spread",
        "market_state_stressed",
    ]


def test_advisory_funding_risk_is_side_aware():
    favorable_short = score_advisory_risk(
        AdvisoryRiskInput(side="sell", funding_rate_bps=Decimal("12"))
    )
    adverse_short = score_advisory_risk(
        AdvisoryRiskInput(side="sell", funding_rate_bps=Decimal("-12"))
    )

    assert favorable_short.reason_codes == []
    assert adverse_short.reason_codes == ["adverse_funding"]
